/* Dashboard. Two views of the same local data:
   - "dfte":     tasks with rules-based H, K, Y → net hours → Digital FTE
   - "activity": raw sessions: prompts, actions, lines, active time
   Everything is aggregated in the browser so filters stay instant. */
(async function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("dfte." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("dfte." + k, JSON.stringify(v)); } catch (e) {} },
  };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- helpers ---------- */
  const monthName = (m, long = true) => new Date(m + "-15").toLocaleString("en", { month: long ? "long" : "short", year: long ? "numeric" : undefined });
  const dayName = (d) => new Date(d + "T12:00").toLocaleString("en", { month: "short", day: "numeric" });
  const fmtH = (h) => (h < 10 ? h.toFixed(1) : Math.round(h)) + "h";
  const fmtMin = (h) => (h < 1 ? Math.round(h * 60) + "m" : h.toFixed(1) + "h");
  const int = (n) => Math.round(n).toLocaleString("en");
  const money = (v) => "$" + int(v);
  const dot = (id) => `<span class="dot" style="background:var(--a-${id})"></span>`;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const css = () => { const cs = getComputedStyle(document.documentElement); return (n) => cs.getPropertyValue(n).trim(); };

  function scramble(el, text, ms = 750) {
    if (reduce) { el.textContent = text; return; }
    const ramp = "@%#*+=-:", t0 = performance.now();
    el.classList.add("scramble");
    (function step(now) {
      const p = Math.min(1, (now - t0) / ms);
      el.textContent = [...text].map((c, i) => (/\d/.test(c) && p < (i + 1) / (text.length + 1) + 0.35 ? ramp[(Math.random() * ramp.length) | 0] : c)).join("");
      if (p < 1) requestAnimationFrame(step); else { el.textContent = text; el.classList.remove("scramble"); }
    })(t0);
  }

  function barRows(rows, fmt, max, clickable) {
    return rows.map((r) => `
      <${clickable ? "button type=button" : "div"} class="bar${r.on ? " on" : ""}${r.off ? " off" : ""}${r.other ? " other" : ""}" data-id="${esc(r.id)}">
        <span class="n" title="${esc(r.name)}">${r.dot || ""}${esc(r.name)}</span>
        <span class="t"><i style="width:${r.off ? 0 : (r.v / max) * 100}%;${r.color ? `background:var(${r.color})` : ""}"></i></span>
        <span class="v">${r.off ? esc(r.off) : fmt(r.v)}</span>
      </${clickable ? "button" : "div"}>`).join("");
  }

  function bars(el, rows, fmt, onClick) {
    const max = Math.max(0.001, ...rows.map((r) => r.v));
    el.innerHTML = barRows(rows, fmt, max, !!onClick) || `<div class="note">Nothing this month.</div>`;
    if (onClick) el.querySelectorAll(".bar").forEach((b) => b.addEventListener("click", () => onClick(b.dataset.id)));
  }

  // By project: the top 8 projects, the rest rolled into "Other", and the groups that
  // aren't projects (learning, setup, unassigned) kept apart below a divider.
  const PROJECT_LIMIT = 8;
  function projectBars(el, rows, fmt) {
    const pinnedNames = [...Object.values(data.purposes).filter((n) => n !== "Project work"), "Unassigned", "(no project)"];
    const pinned = pinnedNames.map((n) => rows.find((r) => r.name === n)).filter(Boolean);
    const projects = rows.filter((r) => !pinnedNames.includes(r.name));
    const shows = (r) => fmt(r.v) !== fmt(0);           // hide rows that round to zero
    const top = S.allProjects ? projects : projects.filter(shows).slice(0, PROJECT_LIMIT);
    const rest = projects.filter((r) => !top.includes(r));
    const shown = [...top];
    if (rest.length) {
      shown.push({ id: "__other", name: `Other (${rest.length} project${rest.length === 1 ? "" : "s"})`, other: true,
                   v: rest.reduce((t, r) => t + r.v, 0) });
    }
    const max = Math.max(0.001, ...shown.map((r) => r.v), ...pinned.map((r) => r.v));
    const toggle = projects.length > PROJECT_LIMIT || projects.some((r) => !shows(r))
      ? `<button type="button" class="linkish more-projects">${S.allProjects ? "Show fewer" : `Show all ${projects.length}`}</button>` : "";
    el.innerHTML = (barRows(shown, fmt, max, false) || `<div class="note">No project work this month.</div>`) + toggle +
      (pinned.length ? `<div class="bar-divider"></div>` + barRows(pinned, fmt, max, false) : "");
    const t = el.querySelector(".more-projects");
    if (t) t.addEventListener("click", () => { S.allProjects = !S.allProjects; projectBars(el, rows, fmt); });
  }

  /* ---------- state ---------- */
  const S = { view: store.get("view", "dfte"), editing: null, allProjects: false, month: null, agent: null, cat: null, showAll: false, mode: store.get("mode", "mono") };
  let data = null, AG = {}, enabled = {};

  const pickAgent = (id) => { if (!enabled[id]) return; S.agent = S.agent === id ? null : id; S.showAll = false; render(); };

  const isLatest = () => S.month === data.months.at(-1);
  const lastScan = () => (data.last_scan ? new Date(data.last_scan * 1000).toLocaleTimeString("en", { hour: "numeric", minute: "2-digit" }) : "never");

  /* ============ ACTIVITY: your sessions ============ */
  const REAL = {
    items(month, { byAgent = true } = {}) {
      return data.sessions.filter((s) => s.date.startsWith(month) && enabled[s.agent] && (!byAgent || !S.agent || s.agent === S.agent));
    },
    agg(ss) {
      const a = { n: ss.length, prompts: 0, actions: 0, added: 0, removed: 0, min: 0, projects: new Set() };
      ss.forEach((s) => { a.prompts += s.prompts; a.actions += s.actions; a.added += s.lines_added; a.removed += s.lines_removed; a.min += s.active_min; a.projects.add(s.project); });
      return a;
    },
    banner: () => [`Live · ${int(data.sessions.length)} sessions read from your agents · last scan ${lastScan()}`, true],
    headline(a) {
      const who = S.agent ? AG[S.agent].name : "Your agents";
      return {
        eyebrow: `Activity · ${monthName(S.month)}`, big: int(a.actions), unit: "ACTIONS",
        range: `${int(a.prompts)} prompts · ${a.n} sessions · ${fmtH(a.min / 60)} active`,
        sentence: a.n ? `${who} took ${int(a.actions)} actions on ${int(a.prompts)} of your prompts, across ${a.projects.size} project${a.projects.size === 1 ? "" : "s"}.`
          : `No sessions in ${monthName(S.month)}.`,
      };
    },
    metrics(a) {
      return [
        ["Prompts", int(a.prompts), "things you asked your agents"],
        ["Actions", int(a.actions), "files read and edited, commands, searches"],
        ["Lines written", "+" + int(a.added), `${int(a.removed)} lines removed`],
        ["Active time", fmtH(a.min / 60), "idle gaps over 10 min left out"],
      ];
    },
    legend() {
      return data.agents.filter((g) => enabled[g.id]).map((g) => `<span><i class="sw" style="background:var(--a-${g.id})"></i>${esc(g.name)}</span>`).join("");
    },
    chart(v) {
      const ag = data.agents.filter((g) => enabled[g.id] && (!S.agent || g.id === S.agent));
      const per = (m, id) => REAL.items(m, { byAgent: false }).filter((s) => s.agent === id).reduce((t, s) => t + s.actions, 0);
      const idx = data.months.indexOf(S.month);
      return {
        yFmt: (x) => int(x),
        tooltip: (i) => `<b>${monthName(data.months[i])}</b><br>` + ag.map((g) => `${esc(g.name)}: ${int(per(data.months[i], g.id))} actions`).join("<br>"),
        series: ag.map((g) => ({
          type: "bar", stack: "a", name: g.name, barMaxWidth: 34,
          data: data.months.map((m, i) => ({ value: per(m, g.id), itemStyle: { color: v(`--a-${g.id}`), opacity: i === idx ? 1 : 0.45 } })),
        })),
      };
    },
    agentBars() {
      const all = REAL.items(S.month, { byAgent: false });
      return data.agents.map((g) => ({
        id: g.id, name: g.name, dot: dot(g.id), color: S.mode === "color" ? `--a-${g.id}` : null, on: S.agent === g.id,
        v: all.filter((s) => s.agent === g.id).reduce((t, s) => t + s.actions, 0),
        off: !g.found ? "not found" : !enabled[g.id] ? "off" : null,
      }));
    },
    projectBars() {
      const by = {};
      REAL.items(S.month).forEach((s) => (by[s.project] = (by[s.project] || 0) + s.actions));
      return Object.entries(by).sort((a, b) => b[1] - a[1]).map(([p, v]) => ({ id: p, name: p, v }));
    },
    fmtBar: int,
    table() {
      const ss = REAL.items(S.month).slice().sort((a, b) => b.started - a.started);
      return {
        title: "Sessions", chips: [], total: ss.length,
        note: "Each session is split into tasks in the DFTE view.",
        head: `<tr><th>Date</th><th>Session</th><th class="hide-sm">Agent</th><th class="num">Prompts</th><th class="num">Actions</th><th class="num hide-sm">Lines</th><th class="num">Active</th></tr>`,
        rows: (S.showAll ? ss : ss.slice(0, 12)).map((s) => `
          <tr>
            <td class="date">${dayName(s.date)}</td>
            <td><div class="title">${esc(s.title)}</div><div class="proj">${esc(s.project)}${s.model ? " · " + esc(s.model) : ""}</div></td>
            <td class="ag hide-sm">${dot(s.agent)}${esc(AG[s.agent].name)}</td>
            <td class="num">${s.prompts}</td>
            <td class="num">${int(s.actions)}</td>
            <td class="num hide-sm">${s.lines_added ? "+" + int(s.lines_added) : "–"}</td>
            <td class="num"><b>${fmtMin(s.active_min / 60)}</b></td>
          </tr>`).join(""),
      };
    },
    agentsNote: () => `Switches save to <code>~/.digital-fte/config.toml</code>`,
    orbit() {
      const all = REAL.items(S.month, { byAgent: false }), total = REAL.agg(all);
      const latest = S.month === data.months.at(-1);
      return {
        level: Math.min(1, total.actions / 3000),
        agents: data.agents.filter((g) => enabled[g.id]).map((g) => {
          const mine = all.filter((s) => s.agent === g.id);
          return {
            id: g.id, name: g.name, color: `--a-${g.id}`,
            share: total.actions ? mine.reduce((t, s) => t + s.actions, 0) / total.actions : 0,
            active: latest && mine.some((s) => s.ended > data.now - 3600),
          };
        }),
      };
    },
    async toggle(id) {
      await fetch(`/api/agents/${id}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ enabled: !enabled[id] }) });
      await load();
    },
    async rescan() {
      await fetch("/api/scan", { method: "POST" });
      await load(true);
    },
  };

  /* ============ DFTE: tasks with H, K, Y ============ */
  const net = (t, h = t.h) => (t.excluded ? 0 : Math.max(0, h * t.k - t.y));
  const DFTE = {
    items(month, { byAgent = true, byCat = false } = {}) {
      return data.tasks.filter((t) => t.date.startsWith(month) && enabled[t.agent]
        && (!byAgent || !S.agent || t.agent === S.agent) && (!byCat || !S.cat || t.purpose === S.cat));
    },
    agg(ts) {
      const HPF = data.settings.hours_per_fte_month;
      const a = { n: ts.length, net: 0, lo: 0, hi: 0, kept: 0, h: 0, y: 0, rules: 0, ai: 0, by: {} };
      ts.forEach((t) => {
        a.by[t.purpose] = (a.by[t.purpose] || 0) + net(t) / HPF;
        a.net += net(t); a.lo += net(t, t.h_low); a.hi += net(t, t.h_high); a.rules += t.rules.net; a.ai += !!t.ai;
        if (!t.excluded) { a.kept += t.h * t.k; a.h += t.h; a.y += t.y; }
      });
      a.dfte = a.net / HPF; a.dfteLo = a.lo / HPF; a.dfteHi = a.hi / HPF; a.gross = a.kept / HPF; a.rulesDfte = a.rules / HPF;
      return a;
    },
    banner: () => [aiBanner(), true],
    headline(a) {
      const WORDS = { 1: "one", 1.5: "one and a half", 2: "two", 2.5: "two and a half", 3: "three", 3.5: "three and a half", 4: "four", 4.5: "four and a half", 5: "five" };
      const who = S.agent ? `${AG[S.agent].name} alone is` : "It's";
      const r = Math.round(a.dfte * 2) / 2;
      return {
        eyebrow: `Digital FTE · ${monthName(S.month)}${isLatest() ? " · month to date" : ""}`, big: a.dfte.toFixed(1), unit: "DFTE",
        range: `range ${a.dfteLo.toFixed(1)} – ${a.dfteHi.toFixed(1)} · gross ${a.gross.toFixed(1)}` + (a.ai ? ` · rules only ${a.rulesDfte.toFixed(1)}` : ""),
        split: Object.keys(data.purposes).filter((k) => a.by[k] >= 0.05)
          .map((k) => `${a.by[k].toFixed(1)} ${data.purposes[k].toLowerCase()}`).join(" · "),
        sentence: !a.n ? `No tasks in ${monthName(S.month)}.`
          : a.dfte < 0.75 ? `${who} about ${Math.round(a.dfte * 100)}% of a full-time person${isLatest() ? " so far this month" : ""}.`
          : `${who} like having ${WORDS[r] || r} extra ${r === 1 ? "person" : "people"} on the team${isLatest() ? " so far this month" : ""}.`,
      };
    },
    metrics(a) {
      const cost = costFor(S.month);
      const prev = data.months[data.months.indexOf(S.month) - 1];
      const pa = prev ? DFTE.agg(DFTE.items(prev)) : null;
      const prevCpd = pa && pa.dfte ? costFor(prev) / pa.dfte : null;
      return [
        ["Net hours", fmtH(a.net), `${a.n} tasks · ${fmtH(a.kept)} of work kept`],
        ["Leverage", a.y ? (a.kept / a.y).toFixed(1) + "×" : "–", "hours delivered per hour you spent"],
        ["Cost per DFTE", a.dfte ? money(cost / a.dfte) : "–", `vs ${money(data.settings.human_cost_per_month)} for a person` + (prevCpd ? ` · ${money(prevCpd)} in ${monthName(prev, false)}` : "")],
        ["Keep rate", a.h ? Math.round((a.kept / a.h) * 100) + "%" : "–", "of agent output you used"],
      ];
    },
    legend: () => `<span><i class="sw solid"></i>Net DFTE</span><span><i class="sw band"></i>Range</span><span><i class="sw dash"></i>Gross</span><span><i class="sw dot"></i>Rules only</span>`,
    chart(v) {
      const rows = data.months.map((m) => DFTE.agg(DFTE.items(m)));
      const idx = data.months.indexOf(S.month);
      const line = S.agent && S.mode === "color" ? v(`--a-${S.agent}`) : v("--fg");
      return {
        yFmt: (x) => x.toFixed(1),
        tooltip: (i) => { const r = rows[i], m = data.months[i], c = costFor(m);
          return `<b>${monthName(m)}</b><br>Net ${r.dfte.toFixed(2)} DFTE (${r.dfteLo.toFixed(1)}–${r.dfteHi.toFixed(1)})<br>Rules only ${r.rulesDfte.toFixed(2)} · gross ${r.gross.toFixed(2)}` +
            `<br>Cost per DFTE ${r.dfte ? money(c / r.dfte) : "–"}<br>${r.n} tasks · ${r.ai} AI-estimated`; },
        boundaryGap: false,
        series: [
          { type: "line", stack: "band", data: rows.map((r) => r.dfteLo), symbol: "none", lineStyle: { opacity: 0 }, silent: true },
          { type: "line", stack: "band", data: rows.map((r) => r.dfteHi - r.dfteLo), symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: v("--pill"), opacity: 1 }, silent: true },
          { type: "line", data: rows.map((r) => r.gross), symbol: "none", lineStyle: { type: "dashed", width: 1.5, color: v("--faint") }, silent: true },
          { type: "line", data: rows.map((r) => r.rulesDfte), symbol: "none", lineStyle: { type: "dotted", width: 2, color: v("--muted") }, silent: true },
          { type: "line", data: rows.map((r) => r.dfte), lineStyle: { width: 2, color: line }, itemStyle: { color: line }, symbol: "circle", symbolSize: (_, p) => (p.dataIndex === idx ? 10 : 5) },
        ],
      };
    },
    agentBars() {
      const all = data.tasks.filter((t) => t.date.startsWith(S.month));
      return data.agents.map((g) => ({
        id: g.id, name: g.name, dot: dot(g.id), color: S.mode === "color" ? `--a-${g.id}` : null, on: S.agent === g.id,
        v: DFTE.agg(all.filter((t) => t.agent === g.id)).dfte, off: enabled[g.id] ? null : "off",
      }));
    },
    projectBars() {
      const by = {};
      DFTE.items(S.month).forEach((t) => (by[t.group] = (by[t.group] || 0) + net(t)));
      return Object.entries(by).sort((a, b) => b[1] - a[1]).map(([p, h]) => ({ id: p, name: p, v: h / data.settings.hours_per_fte_month }));
    },
    fmtBar: (v) => v.toFixed(2),
    table() {
      const ts = DFTE.items(S.month, { byCat: true }).sort((a, b) => b.date.localeCompare(a.date) || net(b) - net(a));
      return {
        title: "Tasks", chips: Object.entries(data.purposes), total: ts.length,
        note: "Click a task to see why it got its estimate, or to correct it.",
        head: `<tr><th>Date</th><th>Task</th><th class="hide-sm">Agent</th><th class="hide-sm">Category</th><th class="num" title="Human-hours estimate">H</th><th class="num" title="Keep factor">K</th><th class="num hide-sm" title="Your time">You</th><th class="num">Net</th></tr>`,
        rows: (S.showAll ? ts : ts.slice(0, 12)).map((t) => `
          <tr class="task${t.excluded ? " excluded" : ""}${S.editing === t.id ? " open" : ""}" data-id="${esc(t.id)}" tabindex="0">
            <td class="date">${dayName(t.date)}</td>
            <td><div class="title">${esc(t.title)}${t.correction ? ' <span class="tag-sm">edited</span>' : ""}${t.excluded ? ' <span class="tag-sm">not counted</span>' : ""}</div>
              <div class="proj">${esc(t.project)} · ${t.prompts} prompts · ${t.actions} actions</div><div class="why">${esc(why(t.evidence))}</div></td>
            <td class="ag hide-sm">${dot(t.agent)}${esc(AG[t.agent].name)}</td>
            <td class="hide-sm">${esc(t.category)}<span class="rng">${t.purpose === "project" ? esc(t.role) : esc(data.purposes[t.purpose])}</span></td>
            <td class="num">${fmtH(t.h)}<span class="rng">${t.ai || t.correction ? "rules " + fmtH(t.rules.h) : `${t.h_low.toFixed(1)}–${t.h_high.toFixed(1)}`}</span></td>
            <td class="num${t.k < 0.5 ? " k0" : ""}" title="${esc(t.k_basis)}">${t.k.toFixed(2)}</td>
            <td class="num hide-sm">${fmtMin(t.y)}</td>
            <td class="num"><b>${fmtH(net(t))}</b></td>
          </tr>${S.editing === t.id ? editor(t) : ""}`).join(""),
      };
    },
    agentsNote: () => REAL.agentsNote(),
    orbit() {
      const all = DFTE.items(S.month, { byAgent: false }), total = DFTE.agg(all);
      return {
        level: Math.min(1, total.dfte / 4),
        agents: data.agents.filter((g) => enabled[g.id]).map((g) => {
          const mine = all.filter((t) => t.agent === g.id);
          return { id: g.id, name: g.name, color: `--a-${g.id}`, share: total.net ? DFTE.agg(mine).net / total.net : 0,
            active: isLatest() && mine.some((t) => t.ended > data.now - 3600) };
        }),
      };
    },
    toggle: (id) => REAL.toggle(id),
    rescan: () => REAL.rescan(),
  };

  const V = () => (S.view === "activity" ? REAL : DFTE);

  // Subscription cost counted for a month: in full for past months, up to today for the current one.
  function costFor(month) {
    const cost = data.agents.filter((g) => enabled[g.id] && (!S.agent || g.id === S.agent)).reduce((s, g) => s + g.cost, 0);
    const now = new Date(data.now * 1000), [y, m] = month.split("-").map(Number);
    if (y === now.getFullYear() && m === now.getMonth() + 1) return cost * now.getDate() / new Date(y, m, 0).getDate();
    return cost;
  }

  function renderSchedule() {
    const st = data.schedule;
    $("#schedule-note").innerHTML = st.installed
      ? `Runs every day at <b>${esc(st.at)}</b>. If the Mac is asleep then, it runs when it wakes; if it was shut down, it runs at login.
         Missed days are caught up on the next run. ${st.loaded ? "" : "<b>macOS hasn't loaded it</b>, run <code>fte schedule install</code> again. "}` +
        (st.last_log ? `<br><span class="note">Last run: ${esc(st.last_log)}</span>` : "")
      : `Not set up. Run <code>.venv/bin/fte schedule install --at 20:00</code> in the project folder to refresh every day.`;
  }

  /* ---------- AI status, task editor, AI services ---------- */
  const activeSvc = () => data.ai.services.find((x) => x.active);
  const pendingAI = () => data.tasks.length - (data.ai.estimated || 0);

  function aiBanner() {
    const p = data.ai.progress, svc = activeSvc();
    if (p.running) return `${p.service}: estimating task ${p.done} of ${p.total}…${p.errors ? ` (${p.errors} failed)` : ""}`;
    if (!svc) return `Rules-based estimates · AI is off · last scan ${lastScan()}`;
    if (!svc.ready) return `Rules-based estimates · ${svc.name}: ${svc.status}`;
    const learned = Object.entries(data.ai.learned || {}).map(([c, f]) => `${c} ×${f}`).join(", ");
    return `${svc.name} · ${svc.model} estimated ${int(data.ai.estimated)} of ${int(data.tasks.length)} tasks` +
      (data.ai.failed ? ` · ${data.ai.failed} failed` : "") + ` · ${data.ai.corrected} corrected by you` +
      (learned ? ` · learned: ${learned}` : "") + ` · last scan ${lastScan()}`;
  }

  function editor(t) {
    const c = t.correction || {};
    const opts = data.categories.map((k) => `<option${k === t.category ? " selected" : ""}>${esc(k)}</option>`).join("");
    const aiLine = t.ai ? `<p><b>${esc(data.ai.service)}:</b> ${esc(t.ai.reason)} <span class="note">(said ${fmtH(t.ai.h)}${t.ai.clamped ? `, kept within 0.25–2× of the rules estimate` : ""})</span></p>` : "";
    return `<tr class="editor-row"><td colspan="8"><div class="editor">
      ${aiLine}
      <p class="note">Rules: ${fmtH(t.rules.h)} · K ${t.rules.k.toFixed(2)} · ${esc(why(t.evidence))}</p>
      <p class="note">K: ${esc(t.k_basis)}${t.learned ? ` · learned factor ×${t.learned} for ${esc(t.category)}` : ""}</p>
      <form class="fix" data-id="${esc(t.id)}">
        <label>Hours a person would need <input name="h" type="number" min="0" step="any" value="${c.h ?? t.h}"></label>
        <label>Keep factor <input name="k" type="number" min="0" max="1" step="any" value="${c.k ?? t.k}"></label>
        <label>Purpose <select name="purpose">${Object.entries(data.purposes).map(([k, l]) => `<option value="${k}"${k === t.purpose ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
        <label>Category <select name="category">${opts}</select></label>
        <label class="chk"><input name="excluded" type="checkbox"${t.excluded ? " checked" : ""}> Not real work</label>
        <div class="acts">
          <button type="submit" class="ghost strong">Save</button>
          ${t.correction ? '<button type="button" class="ghost" data-act="reset">Reset to estimate</button>' : ""}
          <button type="button" class="ghost" data-act="close">Close</button>
        </div>
      </form></div></td></tr>`;
  }

  async function saveFix(form) {
    const f = new FormData(form), id = form.dataset.id, t = data.tasks.find((x) => x.id === id);
    const h = parseFloat(f.get("h")), k = parseFloat(f.get("k"));
    const body = {
      h: Math.abs(h - t.h) > 0.001 || t.correction?.h != null ? h : null,
      k: Math.abs(k - t.k) > 0.001 || t.correction?.k != null ? k : null,
      category: f.get("category") !== t.category || t.correction?.category ? f.get("category") : null,
      purpose: f.get("purpose") !== t.purpose || t.correction?.purpose ? f.get("purpose") : null,
      excluded: f.get("excluded") === "on",
    };
    const r = await fetch(`/api/tasks/${encodeURIComponent(id)}/correction`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { alert((await r.json()).detail || "Couldn't save"); return; }
    S.editing = null;
    await load();
  }

  function renderAI() {
    const svc = activeSvc(), p = data.ai.progress;
    const canRun = svc && svc.ready && !p.running && pendingAI() > 0;
    $("#ai-note").innerHTML = `Only short task summaries are sent. Keys stay in your Keychain or environment. ` +
      (canRun ? `<button type="button" class="ghost strong" id="ai-run">Estimate ${int(pendingAI())} tasks now</button>` : "") +
      (data.ai.use !== "off" ? ` <button type="button" class="ghost" data-use="off">Turn AI off</button>` : "");
    // Only services that can run (plus the active one, so a problem with it stays visible).
    const shown = data.ai.services.filter((x) => x.ready || x.active);
    const others = data.ai.services.filter((x) => !shown.includes(x));
    $("#ai-more").innerHTML = others.length
      ? `Also available once set up in <code>~/.digital-fte/config.toml</code>: ` +
        others.map((x) => `<span title="${esc(x.status)}">${esc(x.name)}</span>`).join(", ") + ". Hover a name to see what it needs."
      : "";
    $("#ai-services").innerHTML = shown.map((x) => `
      <div class="agent${x.active ? " active" : ""}">
        <span class="dot" style="background:${x.ready ? "var(--a-opencode)" : "var(--faint)"}"></span>
        <div>
          <div class="nm">${esc(x.name)}${x.active ? ' <span class="tag-sm">active</span>' : ""}</div>
          <div class="p">${esc(x.model || "no model set")} · ${x.api === "anthropic" ? "Anthropic API" : "OpenAI-compatible"}</div>
          <div class="st">${esc(x.status)}</div>
        </div>
        ${x.active ? "" : `<button type="button" class="ghost" data-use="${esc(x.name)}"${x.ready ? "" : " disabled"}>Use</button>`}
      </div>`).join("");
    document.querySelectorAll("[data-use]").forEach((b) => b.addEventListener("click", async () => {
      await fetch("/api/ai/use", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: b.dataset.use }) });
      await load();
    }));
    const run = $("#ai-run");
    if (run) run.addEventListener("click", async () => { await fetch("/api/ai/run", { method: "POST" }); setTimeout(load, 800); });
  }

  // One line of evidence under each task: what H was built from.
  function why(ev) {
    const parts = [];
    if (ev.code) parts.push(`+${int(ev.code)} code lines`);
    if (ev.doc) parts.push(`+${int(ev.doc)} doc lines`);
    if (ev.removed) parts.push(`${int(ev.removed)} removed`);
    if (ev.reads) parts.push(`${ev.reads} reads`);
    if (ev.commands) parts.push(`${ev.commands} commands`);
    if (ev.web) parts.push(`${ev.web} web`);
    if (ev.words) parts.push(`${int(ev.words)} words of answers`);
    return parts.join(" · ");
  }

  /* ---------- orbit ---------- */
  const orbit = new Orbit($("#stage"), { onSelect: (id) => { S.agent = id; S.showAll = false; render(); } });
  window.__orbit = orbit; // handy for poking at the animation from devtools
  const setMode = (m) => {
    S.mode = m; store.set("mode", m); orbit.setMode(m);
    document.querySelectorAll(".mode button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === m));
  };
  document.querySelectorAll(".mode button").forEach((b) => b.addEventListener("click", () => { setMode(b.dataset.mode); render(); }));
  setMode(S.mode);

  /* ---------- chart ---------- */
  const chart = echarts.init($("#chart"), null, { renderer: "svg" });
  window.addEventListener("resize", () => chart.resize());
  chart.on("click", (p) => { if (p.dataIndex != null) { S.month = data.months[p.dataIndex]; $("#month").value = S.month; S.showAll = false; render(true); } });

  function renderChart() {
    const v = css(), c = V().chart(v);
    const axis = { color: v("--muted"), fontFamily: "Geist Mono", fontSize: 11 };
    chart.setOption({
      animationDuration: 500,
      grid: { left: 44, right: 12, top: 14, bottom: 26 },
      xAxis: { type: "category", boundaryGap: c.boundaryGap ?? true, data: data.months.map((m) => monthName(m, false)),
        axisLine: { lineStyle: { color: v("--line") } }, axisTick: { show: false }, axisLabel: axis },
      yAxis: { type: "value", splitLine: { lineStyle: { color: v("--line") } }, axisLabel: { ...axis, formatter: c.yFmt } },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow", shadowStyle: { color: v("--pill"), opacity: 0.5 } },
        backgroundColor: v("--surface"), borderColor: v("--line"), textStyle: { color: v("--fg"), fontFamily: "Geist", fontSize: 12 },
        formatter: (ps) => c.tooltip(ps[0].dataIndex) },
      series: c.series,
    }, true);
  }

  /* ---------- render ---------- */
  function render(animate) {
    const view = V(), a = view.agg(view.items(S.month));
    const [bannerText, live] = view.banner();
    $("#banner").textContent = bannerText;
    $("#banner").classList.toggle("live", live);
    if (view === DFTE) {
      // The only AI setting on the main page: which service estimated, and a way to change it.
      const change = document.createElement("button");
      change.type = "button"; change.className = "linkish"; change.textContent = "Change";
      change.addEventListener("click", () => openSettings("#ai"));
      $("#banner").append(" · ", change);
    }

    const h = view.headline(a);
    $("#h-eyebrow").textContent = h.eyebrow;
    $("#h-unit").textContent = h.unit;
    if (animate) scramble($("#h-dfte"), h.big); else $("#h-dfte").textContent = h.big;
    $("#h-range").textContent = h.range;
    $("#h-split").textContent = h.split || "";
    $("#h-split").hidden = !h.split;
    $("#h-sentence").textContent = h.sentence;
    $("#h-filter").hidden = !S.agent;
    if (S.agent) $("#h-filter-name").textContent = AG[S.agent].name;

    $("#metrics").innerHTML = view.metrics(a).map(([k, v, s]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
    $("#legend").innerHTML = view.legend();
    renderChart();
    bars($("#by-agent"), view.agentBars(), view.fmtBar, pickAgent);
    projectBars($("#by-project"), view.projectBars(), view.fmtBar);
    renderTable();
    renderAgents();
    const o = view.orbit();
    orbit.setData(o.level, o.agents);
    orbit.select(S.agent, false);
  }

  function renderTable() {
    const t = V().table();
    $("#table-title").textContent = t.title;
    $("#table-note").textContent = t.note;
    $("#task-head").innerHTML = t.head;
    $("#task-rows").innerHTML = t.rows || `<tr><td colspan="8" class="note">Nothing for this filter.</td></tr>`;
    $("#cats").innerHTML = t.chips.length ? [["", "All"], ...t.chips.map((c) => (Array.isArray(c) ? c : [c, c]))].map(([v, l]) =>
      `<button type="button" class="chip" data-cat="${v}" aria-pressed="${(S.cat || "") === v}">${l}</button>`).join("") : "";
    $("#cats").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { S.cat = b.dataset.cat || null; S.showAll = false; renderTable(); }));
    $("#more").hidden = t.total <= 12;
    $("#more").textContent = S.showAll ? "Show fewer" : `Show all ${t.total}`;
  }
  $("#more").addEventListener("click", () => { S.showAll = !S.showAll; renderTable(); });
  $("#task-rows").addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act) {
      const id = act.closest("form").dataset.id;
      if (act.dataset.act === "close") { S.editing = null; renderTable(); }
      if (act.dataset.act === "reset") fetch(`/api/tasks/${encodeURIComponent(id)}/correction`, { method: "DELETE" }).then(() => { S.editing = null; load(); });
      return;
    }
    if (e.target.closest(".editor")) return;
    const row = e.target.closest("tr.task");
    if (row && V() === DFTE) { S.editing = S.editing === row.dataset.id ? null : row.dataset.id; renderTable(); }
  });
  $("#task-rows").addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches("tr.task")) e.target.click(); });
  $("#task-rows").addEventListener("submit", (e) => { e.preventDefault(); saveFix(e.target); });

  function renderAgents() {
    $("#agents-note").innerHTML = V().agentsNote();
    $("#agents").innerHTML = data.agents.map((g) => `
      <div class="agent">
        <span class="dot" style="background:var(--a-${g.id})"></span>
        <div>
          <div class="nm">${esc(g.name)}</div>
          <div class="p" title="${esc(g.path)}">${esc(g.path)}</div>
          <div class="st">${esc(g.status)}${g.cost ? ` · $${g.cost}/mo` : ""}</div>
        </div>
        <button type="button" class="switch" role="switch" aria-checked="${!!enabled[g.id]}" aria-label="Count ${esc(g.name)}"
          data-id="${g.id}" ${g.found === false ? "disabled" : ""}></button>
      </div>`).join("");
    $("#agents").querySelectorAll(".switch").forEach((b) => b.addEventListener("click", () => V().toggle(b.dataset.id)));
  }

  /* ---------- settings (collapsed by default, remembered) ---------- */
  const settings = $("#settings");
  settings.open = store.get("settings-open", false);
  settings.addEventListener("toggle", () => store.set("settings-open", settings.open));
  function openSettings(sel) {
    settings.open = true;
    $(sel).scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  /* ---------- loading ---------- */
  async function load(animate = false) {
    data = await fetch("/api/data").then((r) => r.json());
    AG = Object.fromEntries(data.agents.map((g) => [g.id, g]));
    enabled = Object.fromEntries(data.agents.map((g) => [g.id, g.enabled]));
    renderFormula();
    if (S.agent && !enabled[S.agent]) S.agent = null;
    if (!data.months.includes(S.month)) S.month = data.months.at(-1);
    $("#month").innerHTML = data.months.slice().reverse().map((m) => `<option value="${m}">${monthName(m)}</option>`).join("");
    $("#month").value = S.month;
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.view === S.view));
    if (!data.months.length) { $("#banner").textContent = "No sessions found yet. Check the agent paths in Settings, then Rescan."; renderAgents(); return; }
    render(animate);
    renderAI();
    renderSchedule();
    // Keep the page live while a scan or AI run is going, but never while you're editing a task.
    if (data.scanning || data.ai.progress.running) setTimeout(() => (S.editing ? null : load()), data.scanning ? 1500 : 4000);
  }

  /* ---------- controls ---------- */
  $("#month").addEventListener("change", (e) => { S.month = e.target.value; S.showAll = false; render(true); });
  $("#h-clear").addEventListener("click", () => { S.agent = null; render(); });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    if (S.view === b.dataset.view) return;
    S.view = b.dataset.view; store.set("view", S.view); S.cat = null; S.showAll = false;
    document.querySelectorAll(".seg button").forEach((x) => x.setAttribute("aria-pressed", x.dataset.view === S.view));
    render(true);
  }));
  $("#rescan").addEventListener("click", async () => {
    const btn = $("#rescan"), el = $("#h-dfte"), txt = el.textContent;
    btn.disabled = true; btn.textContent = "↻ Scanning…";
    let spinning = true;
    (function spin() { if (!spinning) return; scramble(el, txt, 600); setTimeout(spin, 650); })();
    try { await V().rescan(); } finally { spinning = false; btn.disabled = false; btn.textContent = "↻ Rescan"; }
  });

  const isDark = () => document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const themeLabel = () => ($("#theme").textContent = isDark() ? "Light" : "Dark");
  $("#theme").addEventListener("click", () => {
    const t = isDark() ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("dfte.theme", t); } catch (e) {}
    themeLabel(); orbit.clearColorCache(); render();
  });
  themeLabel();

  function renderFormula() {
    const r = data.rates, d = data.settings;
    $("#formula").textContent =
`Net hours (task) = max(0, H × K − Y)
DFTE (month)     = Σ Net hours ÷ ${d.hours_per_fte_month} productive hours per FTE

H  hours a competent person would need (developer for code, presales consultant
   for documents and demo prep, analyst for research and analysis):
     code lines ÷ ${r.code_lines_per_hour}/h  +  doc lines ÷ ${r.doc_lines_per_hour}/h  +  removed lines ÷ ${r.removed_lines_per_hour}/h
     + answer words ÷ ${r.answer_words_per_hour}/h  +  reads × ${r.read_minutes}m  +  commands × ${r.command_minutes}m  +  web × ${r.web_minutes}m
   one edit counts at most ${r.max_lines_per_edit} lines · range ${r.range_low}× to ${r.range_high}×
K  keep factor: 1.0 file in git · 0.9 file still on disk · 0.3 file gone · ${d.default_keep} if nothing to check
Y  your time: typing prompts (${r.typing_wpm} wpm, max 5m each) + time before each follow-up
   (max ${d.review_cap_minutes}m) + reading the final answer (${r.reading_wpm} wpm)

AI step (${data.ai.use}): reads a short summary of each task and re-estimates H, the category, and K when
   no files could be checked. Its H is kept within 0.25×–2× of the rules H. Your corrections always win;
   after 3+ corrected H values in a category, that category's typical ratio is applied to the rest.

A task = a stretch of work in one session; a break over ${r.task_gap_minutes}m starts a new one.
Subagent work counts toward the task that launched it. Tune any rate in ~/.digital-fte/config.toml.`;
  }

  await load(true);
})();
