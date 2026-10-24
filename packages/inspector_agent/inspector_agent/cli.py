"""inspector-validate: validate a migration package from the command line.

By default the agent runs (needs the LiteLLM proxy and a model behind it). ``--rules-only`` skips the LLM and
reports the rule sweep's leads as they are; ``--with-data`` also runs their data probes through the data agent.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="inspector-validate", description=__doc__)
    p.add_argument("package", type=Path, help="package directory or .zip")
    p.add_argument("--rules-only", action="store_true", help="no LLM: report the rule sweep's leads")
    p.add_argument("--with-data", action="store_true", help="with --rules-only: settle leads with data probes")
    p.add_argument("--max-turns", type=int, default=60, help="agent turn budget")
    p.add_argument("--json", type=Path, help="write report.json here")
    p.add_argument("--md", type=Path, help="write report.md here")
    p.add_argument("--quiet", action="store_true", help="print only the verdict line")
    args = p.parse_args(argv)

    from .tools.findings_ledger import markdown
    from .inspector_agent import run_rules_only, run_validation

    report = run_rules_only(str(args.package), with_data=args.with_data) if args.rules_only else \
        run_validation(str(args.package), max_turns=args.max_turns)
    if args.json:
        args.json.write_text(report.model_dump_json(indent=2))
    md = markdown(report)
    if args.md:
        args.md.write_text(md)
    c = report.counts
    print(f"{report.package_id}: {report.verdict}  (blockers failing {c['blocker_fail']}, "
          f"unverified {c['blocker_unverified']}, warnings {c['warning']}, proven {c['pass']}) [{report.stage}]")
    if not args.quiet and not args.md:
        print(md)
    return {"READY": 0, "NEEDS_REVIEW": 2, "NOT_READY": 1}[report.verdict]


if __name__ == "__main__":
    sys.exit(main())
