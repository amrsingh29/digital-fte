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
        state = "not found" if not r.found else f"{r.sources} sessions, {r.updated} re-read, {r.removed} removed"
        print(f"{r.agent:12} {'on ' if on else 'off'}  {state}")
        for e in r.errors[:5]:
            print(f"{'':12}   ! {e}")
    print(f"\ndatabase: {config.DB_PATH}")


def _estimate(args) -> None:
    from collections import defaultdict
    from datetime import datetime

    from . import ai, config, db, estimate, refine

    cfg = config.load()
    con = db.connect(config.DB_PATH)
    n = estimate.rebuild(con, cfg)
    print(f"{n} tasks")
    if args.ai:
        act = ai.active(cfg)
        print(f"estimating with {act[0]} · {act[1].get('model')}…" if act else "AI is off (use = \"off\")")
        print(ai.run(config.DB_PATH, cfg, limit=args.limit))
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

    args = parser.parse_args()
    if args.cmd == "serve":
        # Bound to 127.0.0.1 only: the dashboard is never reachable from the network.
        uvicorn.run("digital_fte.app:app", host="127.0.0.1", port=args.port, reload=args.reload,
                    reload_dirs=[str(Path(__file__).parent)] if args.reload else None)
    elif args.cmd == "scan":
        _scan(args)
    elif args.cmd == "estimate":
        _estimate(args)


if __name__ == "__main__":
    main()
