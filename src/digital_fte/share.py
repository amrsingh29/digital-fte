"""Phase 5: a totals-only summary page you can share.

Only aggregates leave this module: monthly DFTE, hours, leverage, cost and keep
rate, plus splits by agent and by kind of work. Never task titles, prompts,
file names, project or customer names.
"""

from __future__ import annotations

import calendar
import math
from datetime import datetime
from html import escape

from . import config, db, refine
from .collectors import COLLECTORS
from .estimate import CATEGORIES_ROLE

RAMP = " .:-=+*#%@"


def _cost_share(month: str, now: datetime) -> float:
    """Share of a month's subscription cost to count: all of it for past months,
    the days elapsed for the current one (so mid-month cost per DFTE is fair)."""
    y, m = map(int, month.split("-"))
    if (y, m) == (now.year, now.month):
        return now.day / calendar.monthrange(y, m)[1]
    return 1.0


def build(cfg: dict | None = None) -> dict:
    cfg = cfg or config.load()
    con = db.connect(config.DB_PATH)
    try:
        tasks, ai_summary = refine.finalize(con, cfg)
    finally:
        con.close()
    enabled = [a for a, c in cfg["agents"].items() if c.get("enabled") and a in COLLECTORS]
    tasks = [t for t in tasks if t["agent"] in enabled and config.path_allowed(t["cwd"], cfg)]
    hpf = cfg["dfte"]["hours_per_fte_month"]
    monthly_cost = sum(cfg["agents"][a].get("cost_per_month", 0) for a in enabled)
    now = datetime.now()

    def month_of(t):
        return datetime.fromtimestamp(t["started"]).strftime("%Y-%m")

    months: dict[str, dict] = {}
    for t in tasks:
        m = months.setdefault(month_of(t), dict(n=0, net=0.0, lo=0.0, hi=0.0, rules=0.0, kept=0.0, h=0.0, y=0.0))
        live = not t["excluded"]
        m["n"] += 1
        m["net"] += t["net"]
        m["lo"] += max(0, t["h_low"] * t["k"] - t["y"]) * live
        m["hi"] += max(0, t["h_high"] * t["k"] - t["y"]) * live
        m["rules"] += t["rules"]["net"]
        if live:
            m["kept"] += t["h"] * t["k"]
            m["h"] += t["h"]
            m["y"] += t["y"]
    rows = []
    for key in sorted(months):
        m = months[key]
        dfte = m["net"] / hpf
        cost = monthly_cost * _cost_share(key, now)
        rows.append({
            "month": key, "tasks": m["n"], "net": m["net"], "dfte": dfte,
            "lo": m["lo"] / hpf, "hi": m["hi"] / hpf, "rules": m["rules"] / hpf,
            "leverage": m["kept"] / m["y"] if m["y"] else None,
            "keep": m["kept"] / m["h"] if m["h"] else None,
            "cost": cost, "cost_per_dfte": cost / dfte if dfte else None,
        })

    current = rows[-1]["month"] if rows else now.strftime("%Y-%m")
    cur = [t for t in tasks if month_of(t) == current]
    by_agent = {}
    for t in cur:
        by_agent[t["agent"]] = by_agent.get(t["agent"], 0) + t["net"] / hpf
    by_kind = {}
    for t in cur:
        by_kind[t["category"]] = by_kind.get(t["category"], 0) + t["net"] / hpf

    return {
        "generated": now, "current": current, "months": rows, "hpf": hpf,
        "human_cost": cfg["dfte"]["human_cost_per_month"],
        "agents": sorted(((COLLECTORS[a].name, v) for a, v in by_agent.items()), key=lambda x: -x[1]),
        "kinds": sorted(((k, CATEGORIES_ROLE[k], v) for k, v in by_kind.items()), key=lambda x: -x[2]),
        "ai": ai_summary, "task_count": len(tasks),
        "corrected": ai_summary.get("corrected", 0),
    }


# ---------- rendering ----------

def ascii_globe(w: int = 46, h: int = 21) -> str:
    """A static frame of the dashboard's ASCII globe."""
    lx, ly, lz = -0.55, -0.62, 0.56
    norm = math.sqrt(lx * lx + ly * ly + lz * lz)
    lx, ly, lz = lx / norm, ly / norm, lz / norm
    out = []
    for r in range(h):
        line = []
        for c in range(w):
            x = (c + 0.5 - w / 2) / (w / 2)
            y = (r + 0.5 - h / 2) / (h / 2)
            d2 = x * x + y * y
            if d2 > 1:
                line.append(" ")
                continue
            z = math.sqrt(1 - d2)
            lam = max(0.0, x * lx + y * ly + z * lz)
            t = 0.42
            y1, z1 = y * math.cos(t) - z * math.sin(t), y * math.sin(t) + z * math.cos(t)
            lon, lat = math.atan2(x * math.cos(0.6) + z1 * math.sin(0.6), -x * math.sin(0.6) + z1 * math.cos(0.6)), math.asin(max(-1, min(1, y1)))
            grid = abs(math.sin(lon * 5)) < 0.17 or abs(math.sin(lat * 6)) < 0.15
            ink = min(1.0, 0.18 + 0.72 * lam + (0.3 if grid else 0))
            line.append(RAMP[min(len(RAMP) - 1, 1 + int(ink * (len(RAMP) - 1.01)))])
        out.append("".join(line).rstrip())
    return "\n".join(out)


def _mname(m: str, long: bool = True) -> str:
    return datetime.strptime(m, "%Y-%m").strftime("%B %Y" if long else "%b")


def _chart(rows: list[dict]) -> str:
    """Monthly DFTE: range band, net line, rules-only dotted line, on one scale."""
    if not rows:
        return ""
    W, H, L, R, T, B = 720, 230, 44, 16, 14, 30
    top = max(max(r["hi"] for r in rows), max(r["rules"] for r in rows), 0.5)
    step = next(s for s in (0.25, 0.5, 1, 2, 5, 10, 20) if top / s <= 6)
    ymax = math.ceil(top / step) * step
    n = len(rows)
    X = lambda i: L + (W - L - R) * (i / (n - 1) if n > 1 else 0.5)
    Y = lambda v: T + (H - T - B) * (1 - v / ymax)
    pts = lambda key: " ".join(f"{X(i):.1f},{Y(r[key]):.1f}" for i, r in enumerate(rows))
    band = pts("hi") + " " + " ".join(f"{X(i):.1f},{Y(r['lo']):.1f}" for i, r in reversed(list(enumerate(rows))))
    ticks = []
    v = 0.0
    while v <= ymax + 1e-9:
        ticks.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                     f'<text class="tick" x="{L - 8}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
        v += step
    labels = "".join(f'<text class="tick" x="{X(i):.1f}" y="{H - 8}" text-anchor="middle">{_mname(r["month"], False)}</text>'
                     for i, r in enumerate(rows))
    last = rows[-1]
    return f"""<svg viewBox="0 0 {W} {H}" role="img" aria-label="Digital FTE per month">
  {''.join(ticks)}
  <polygon class="band" points="{band}"/>
  <polyline class="rules" points="{pts('rules')}"/>
  <polyline class="net" points="{pts('dfte')}"/>
  <circle class="end" cx="{X(n - 1):.1f}" cy="{Y(last['dfte']):.1f}" r="4.5"/>
  {labels}
</svg>"""


def _words(d: float) -> str:
    words = {1: "one", 1.5: "one and a half", 2: "two", 2.5: "two and a half", 3: "three", 3.5: "three and a half",
             4: "four", 4.5: "four and a half", 5: "five"}
    if d < 0.75:
        return f"about {round(d * 100)}% of a full-time person"
    r = round(d * 2) / 2
    return f"like having {words.get(r, f'{r:g}')} extra {'person' if r == 1 else 'people'} on the team"


def render(s: dict, name: str | None = None) -> str:
    """The page content (title, styles, body), without the html/head/body skeleton."""
    rows = s["months"]
    cur = rows[-1] if rows else None
    now = s["generated"]
    is_current = cur and cur["month"] == now.strftime("%Y-%m")
    money = lambda v: "–" if v is None else f"${v:,.0f}"
    pct = lambda v: "–" if v is None else f"{v * 100:.0f}%"
    lev = lambda v: "–" if v is None else f"{v:.1f}×"
    ai = s["ai"] or {}
    est_by = f"{ai['service']} ({ai['model']})" if ai.get("service") else "rules only"
    who = f"{escape(name)}'s" if name else "My"

    table = "".join(f"""
      <tr{' class="now"' if r is cur else ''}>
        <th scope="row">{_mname(r['month'])}{' <span class="mtd">to date</span>' if r is cur and is_current else ''}</th>
        <td>{r['dfte']:.2f}</td><td class="dim">{r['lo']:.1f}–{r['hi']:.1f}</td><td class="dim">{r['rules']:.2f}</td>
        <td>{r['net']:,.0f}</td><td>{r['tasks']}</td><td>{lev(r['leverage'])}</td><td>{money(r['cost_per_dfte'])}</td>
      </tr>""" for r in rows)
    amax = max([v for _, v in s["agents"]] + [0.001])
    agents = "".join(f'<div class="bar"><span>{escape(n)}</span><i><b style="width:{v / amax * 100:.1f}%"></b></i><em>{v:.2f}</em></div>'
                     for n, v in s["agents"]) or '<p class="dim">No tasks this month.</p>'
    kmax = max([v for *_, v in s["kinds"]] + [0.001])
    kinds = "".join(f'<div class="bar"><span>{escape(k)} <small>{escape(role)}</small></span><i><b style="width:{v / kmax * 100:.1f}%"></b></i><em>{v:.2f}</em></div>'
                    for k, role, v in s["kinds"]) or '<p class="dim">No tasks this month.</p>'

    head = (f"""
  <p class="eyebrow">Digital FTE · {_mname(cur['month'])}{' · month to date' if is_current else ''}</p>
  <p class="big">{cur['dfte']:.1f}<small>DFTE</small></p>
  <p class="range">range {cur['lo']:.1f} – {cur['hi']:.1f} · rules only {cur['rules']:.1f}</p>
  <p class="lede">My AI coding agents delivered {_words(cur['dfte'])}{' so far this month' if is_current else ''}.</p>"""
            if cur else '<p class="lede">No tasks recorded yet.</p>')

    stats = f"""
  <dl class="stats">
    <div><dt>Net hours</dt><dd>{cur['net']:,.0f}</dd><p>{cur['tasks']} tasks</p></div>
    <div><dt>Leverage</dt><dd>{lev(cur['leverage'])}</dd><p>hours delivered per hour I spent</p></div>
    <div><dt>Cost per DFTE</dt><dd>{money(cur['cost_per_dfte'])}</dd><p>vs {money(s['human_cost'])} for a person</p></div>
    <div><dt>Keep rate</dt><dd>{pct(cur['keep'])}</dd><p>of agent output used</p></div>
  </dl>""" if cur else ""

    return f"""<title>{who} Digital FTE</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500&family=Geist+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,400&display=swap">
<style>
/* Layout: one reading column. Thesis number and a still ASCII globe, then the monthly record, then the splits. */
:root{{
  --bg:#FAFAF8; --surface:#FFFFFF; --fg:#161616; --muted:#6F6F6A; --faint:#A6A6A0; --line:#E6E6E2; --band:#ECECE8; --accent:#C2603D;
  --serif:"Newsreader","Iowan Old Style",Georgia,serif;
  --sans:"Geist","Helvetica Neue",Arial,sans-serif;
  --mono:"Geist Mono",ui-monospace,"SF Mono",Menlo,monospace;
}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
  --bg:#0F0F0E; --surface:#171716; --fg:#ECECE8; --muted:#9C9C96; --faint:#5E5E5A; --line:#262624; --band:#1F1F1D; --accent:#F08C66; color-scheme:dark}}}}
:root[data-theme="dark"]{{
  --bg:#0F0F0E; --surface:#171716; --fg:#ECECE8; --muted:#9C9C96; --faint:#5E5E5A; --line:#262624; --band:#1F1F1D; --accent:#F08C66; color-scheme:dark}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--fg);font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased;margin:0;padding-inline:20px;padding-block:28px 48px}}
.wrap{{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:40px}}
.hero{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center}}
.eyebrow,dt,.tick,.mtd,th,.note{{font-family:var(--mono)}}
.eyebrow{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:0}}
.big{{font-family:var(--serif);font-size:clamp(72px,14vw,120px);line-height:.95;letter-spacing:-.03em;margin:10px 0 0;font-variant-numeric:tabular-nums}}
.big small{{font-family:var(--mono);font-size:14px;letter-spacing:.06em;color:var(--muted);margin-left:10px}}
.range{{font-family:var(--mono);font-size:13px;color:var(--muted);margin:8px 0 0}}
.lede{{font-family:var(--serif);font-size:22px;line-height:1.35;margin:16px 0 0;max-width:30ch;text-wrap:balance}}
.globe{{font-family:var(--mono);font-size:7.5px;line-height:1.08;color:var(--muted);margin:0;user-select:none}}
h2{{font-family:var(--serif);font-weight:400;font-size:26px;letter-spacing:-.015em;margin:0 0 12px;padding-bottom:10px;border-bottom:1px solid var(--line);text-wrap:balance}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0}}
.stats div{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
dt{{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}}
dd{{font-family:var(--mono);font-size:26px;font-weight:500;margin:6px 0 0;font-variant-numeric:tabular-nums}}
.stats p{{font-size:12.5px;color:var(--muted);margin:2px 0 0}}
svg{{width:100%;height:auto;display:block}}
svg .grid{{stroke:var(--line);stroke-width:1}}
svg .tick{{fill:var(--muted);font-size:11px}}
svg .band{{fill:var(--band);stroke:none}}
svg .net{{fill:none;stroke:var(--fg);stroke-width:2}}
svg .rules{{fill:none;stroke:var(--muted);stroke-width:1.6;stroke-dasharray:2 4}}
svg .end{{fill:var(--accent)}}
.legend{{display:flex;gap:18px;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin-top:8px}}
.legend i{{display:inline-block;width:18px;margin-right:6px;vertical-align:3px;border-top:2px solid var(--fg)}}
.legend i.r{{border-top:2px dotted var(--muted)}}
.legend i.b{{border:0;height:8px;background:var(--band);vertical-align:0}}
.scroll{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}
thead th{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:500}}
th[scope=row]{{text-align:left;font-family:var(--sans);font-weight:500}}
thead th:first-child{{text-align:left}}
tr.now td,tr.now th{{background:var(--band)}}
td{{font-family:var(--mono)}}
.dim{{color:var(--muted)}}
.mtd{{font-size:10.5px;color:var(--muted);border:1px solid var(--line);border-radius:5px;padding:0 5px;margin-left:6px;font-weight:400}}
.split{{display:grid;grid-template-columns:1fr 1fr;gap:36px}}
.split > section{{min-width:0}}
.bar{{display:grid;grid-template-columns:minmax(0,10em) minmax(0,1fr) 3.4em;gap:12px;align-items:center;padding:6px 0}}
.bar span{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.bar small{{color:var(--muted);font-size:12px}}
.bar i{{height:8px;background:var(--band);border-radius:4px;overflow:hidden}}
.bar b{{display:block;height:100%;background:var(--fg);border-radius:4px}}
.bar em{{font-style:normal;font-family:var(--mono);font-size:13px;text-align:right}}
.method p{{max-width:68ch;margin:0 0 10px;color:var(--muted)}}
.method code{{font-family:var(--mono);font-size:13px;color:var(--fg)}}
.note{{font-size:12px;color:var(--faint);margin:0}}
@media (max-width:640px){{
  .hero{{grid-template-columns:1fr}}
  .globe{{display:none}}
  .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .split{{grid-template-columns:1fr;gap:28px}}
  body{{padding-inline:16px}}
}}
</style>
<main class="wrap">
  <header class="hero">
    <div>{head}</div>
    <pre class="globe" aria-hidden="true">{ascii_globe()}</pre>
  </header>
  {stats}
  <section>
    <h2>Month by month</h2>
    {_chart(rows)}
    <div class="legend"><span><i></i>Digital FTE</span><span><i class="b"></i>Range</span><span><i class="r"></i>Rules only</span></div>
    <div class="scroll" style="margin-top:18px">
      <table>
        <thead><tr><th>Month</th><th>DFTE</th><th>Range</th><th>Rules only</th><th>Net hours</th><th>Tasks</th><th>Leverage</th><th>Cost per DFTE</th></tr></thead>
        <tbody>{table}</tbody>
      </table>
    </div>
  </section>
  <div class="split">
    <section><h2>By agent</h2>{agents}</section>
    <section><h2>By kind of work</h2>{kinds}</section>
  </div>
  <section class="method">
    <h2>How it's measured</h2>
    <p>Each agent session is split into tasks. For every task: <code>net hours = H × K − Y</code>, where H is the hours a competent
      person would need without AI, K is the share of the output that was kept, and Y is my own time prompting and reviewing.
      <code>DFTE = net hours ÷ {s['hpf']:g}</code> productive hours per person per month.</p>
    <p>H starts from rules (lines written, files read, commands, answers) and is refined by {escape(est_by)}, which judges how hard
      each task really was, within 0.25× to 2× of the rules estimate. {s['corrected']} task{'s' if s['corrected'] != 1 else ''} were
      reviewed and corrected by hand. Cost per DFTE uses the agents' monthly subscriptions, counting the current month up to today.</p>
    <p class="note">Generated {now:%-d %B %Y, %H:%M} from {s['task_count']} tasks. Totals only: no task names, prompts, files or customer names.</p>
  </section>
</main>"""


def page(s: dict, name: str | None = None) -> str:
    """A complete HTML document, for viewing on localhost or saving as a file."""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'
            + render(s, name) + "</body></html>")
