"""CLI:  python -m touch_labor.jobs run|report|sources  (see README)."""
from __future__ import annotations

import argparse

import pandas as pd

from . import report, runner


def main() -> None:
    ap = argparse.ArgumentParser(prog="touch_labor.jobs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch current postings and record them")
    r.add_argument("--sites", nargs="*", help="site keys (default: all)")
    r.add_argument("--companies", nargs="*", help="company names (default: all covered)")
    r.add_argument("--exclude", nargs="*", help="company names to skip")
    sub.add_parser("report", help="print per-site and per-company posting counts")
    sub.add_parser("sources", help="show which companies are covered and which were skipped, with reasons")
    args = ap.parse_args()

    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_colwidth", 50)
    if args.cmd == "run":
        stats = runner.run(args.sites, args.companies, exclude=args.exclude)
        print(f"done: {stats['ok']} scopes ok, {stats['failed']} failed")
    elif args.cmd == "report":
        if not report.available():
            raise SystemExit("no postings recorded yet; run `run` first")
        t = report.tables()
        print(t["by_site"].to_string(index=False))
        print()
        print(t["by_company"].to_string(index=False))
        if len(t["failures"]):
            print("\nlast run failures:")
            print(t["failures"].to_string(index=False))
    else:
        s = pd.DataFrame(runner.load_sources())
        print(s[["company", "platform", "status", "hourly_relevance", "source_file"]].to_string(index=False))


if __name__ == "__main__":
    main()
