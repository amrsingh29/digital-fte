import argparse
from pathlib import Path

import uvicorn


def _scan(args) -> None:
    from . import config
    from .scan import scan

    results = scan(full=args.full, only=args.agent)
    cfg = config.load()
    for r in results:
        on = cfg["agents"].get(r.agent, {}).get("enabled", False)
        state = "not found" if not r.found else f"{r.sources} sessions, {r.updated} re-read" + (
            f", {r.gone} deleted by the agent (kept here)" if r.gone else "")
        print(f"{r.agent:12} {'on ' if on else 'off'}  {state}")
        for e in r.errors[:5]:
            print(f"{'':12}   ! {e}")
    print(f"\ndatabase: {config.DB_PATH}")


def _estimate(args) -> None:
    import time
    from collections import defaultdict
    from datetime import datetime

    from . import ai, config, db, estimate, refine

    cfg = config.load()
    con = db.connect(config.DB_PATH)
    n = estimate.rebuild(con, cfg)
    print(f"{n} tasks")
    if args.ai or args.recheck_roles:
        act = ai.active(cfg)
        print(f"estimating with {act[0]} · {act[1].get('model')}…" if act else "AI is off (use = \"off\")")
        # --recheck-roles (e.g. after changing the role catalog): every task is sent again; each keeps
        # its current estimate until the new one arrives, so the dashboard never falls back meanwhile.
        out = ai.run(config.DB_PATH, cfg, limit=args.limit, redo_before=time.time() if args.recheck_roles else None)
        print(out)
        if "already running" in str(out.get("skipped", "")):
            print("Another AI run is in progress (usually the dashboard's). Nothing was changed; run this again "
                  "when it finishes (Settings → AI estimates in the dashboard shows its progress).")
    tasks, summary = refine.finalize(con, cfg)
    enabled = {a for a, c in cfg["agents"].items() if c.get("enabled")}
    hpf = cfg["dfte"]["hours_per_fte_month"]
    by = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for t in tasks:
        if t["agent"] in enabled:
            b = by[datetime.fromtimestamp(t["started"]).strftime("%Y-%m")]
            b[0] += 1
            b[1] += t["rules"]["net"]
            b[2] += t["net"]
            b[3] += max(0, t["h_low"] * t["k"] - t["y"]) * (not t["excluded"])
            b[4] += max(0, t["h_high"] * t["k"] - t["y"]) * (not t["excluded"])
    if summary.get("service"):
        print(f"AI: {summary['service']} · {summary['model']} estimated {summary['estimated']} of {len(tasks)} tasks"
              f" ({summary['failed']} failed) · {summary['corrected']} corrected by you")
    print("\nmonth     tasks  rules DFTE  final DFTE  (low-high)")
    for m in sorted(by):
        c, rules, final, lo, hi = by[m]
        print(f"{m}  {c:5}  {rules / hpf:10.2f}  {final / hpf:10.2f}  ({lo / hpf:.2f}-{hi / hpf:.2f})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="fte", description="Digital FTE dashboard")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the dashboard on localhost")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--reload", action="store_true")

    scan = sub.add_parser("scan", help="read agent history into the local database")
    scan.add_argument("--full", action="store_true", help="re-read everything, not just what changed")
    scan.add_argument("--agent", help="scan one agent only (claude_code, opencode, cursor, cline)")

    est = sub.add_parser("estimate", help="recompute tasks and print monthly DFTE")
    est.add_argument("--ai", action="store_true", help="also run the AI step for tasks it hasn't seen")
    est.add_argument("--limit", type=int, help="AI: at most this many tasks")
    est.add_argument("--recheck-roles", action="store_true", help="ask the AI to pick roles again for every task "
                     "(after you change the role catalog)")

    ref = sub.add_parser("refresh", help="scan + AI estimates (what the nightly job runs)")
    ref.add_argument("--if-older-than", type=float, metavar="HOURS", help="skip if the last scan is more recent")

    sch = sub.add_parser("schedule", help="nightly automatic refresh (macOS launchd)")
    sch_sub = sch.add_subparsers(dest="action", required=True)
    ins = sch_sub.add_parser("install", help="run every day at a time; catches up on wake and at login")
    ins.add_argument("--at", default="20:00", help="HH:MM, local time (default 20:00)")
    sch_sub.add_parser("remove", help="stop the automatic refresh")
    sch_sub.add_parser("status", help="show whether it's installed and the last run")

    shr = sub.add_parser("share", help="write the totals-only summary page")
    shr.add_argument("--out", default=str(Path("~/.digital-fte/summary.html").expanduser()), help="output file")
    shr.add_argument("--name", help="your name for the page title, e.g. Amrendra")
    shr.add_argument("--fragment", action="store_true", help="content only, no html/head/body (for publishing)")

    args = parser.parse_args()
    if args.cmd == "serve":
        # Bound to 127.0.0.1 only: the dashboard is never reachable from the network.
        uvicorn.run("digital_fte.app:app", host="127.0.0.1", port=args.port, reload=args.reload,
                    reload_dirs=[str(Path(__file__).parent)] if args.reload else None)
    elif args.cmd == "scan":
        _scan(args)
    elif args.cmd == "estimate":
        _estimate(args)
    elif args.cmd == "share":
        from . import share
        summary = share.build()
        html = share.render(summary, args.name) if args.fragment else share.page(summary, args.name)
        Path(args.out).expanduser().write_text(html)
        print(f"wrote {args.out}")
    elif args.cmd == "refresh":
        from . import jobs
        jobs.refresh(args.if_older_than)
    elif args.cmd == "schedule":
        from . import jobs
        if args.action == "install":
            path = jobs.install(args.at)
            print(f"installed: {path}\nruns daily at {args.at}, on wake if asleep then, and at login if missed\nlog: {jobs.LOG}")
        elif args.action == "remove":
            print("removed" if jobs.remove() else "not installed")
        else:
            st = jobs.status()
            print(f"installed: {st['installed']}  loaded: {st['loaded']}  daily at: {st['at']}\nlast log line: {st['last_log']}")


if __name__ == "__main__":
    main()
