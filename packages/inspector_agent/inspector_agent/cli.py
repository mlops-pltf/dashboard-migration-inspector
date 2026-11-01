"""inspector-validate: validate a migration package from the command line.

By default the agent runs (needs a model: see INSPECTOR_LLM_MODEL_* in .env.example). ``--rules-only`` skips the LLM and
reports the rule sweep's leads as they are; ``--with-data`` also runs their data probes through the data agent.

The other agents are reached at INSPECTOR_*_AGENT_URL (URLs or AgentCore runtime ARNs). ``--in-process`` runs them
inside this process instead (same A2A requests, no servers or ports): one command on a machine without Docker,
when the expert and data agent packages are installed alongside.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="inspector-validate", description=__doc__)
    p.add_argument("package", help="package folder, .zip, or s3://... location")
    p.add_argument("--rules-only", action="store_true", help="no LLM: report the rule sweep's leads")
    p.add_argument("--with-data", action="store_true", help="with --rules-only: settle leads with data probes")
    p.add_argument("--max-turns", type=int, default=60, help="agent turn budget")
    p.add_argument("--json", type=Path, help="write report.json here")
    p.add_argument("--md", type=Path, help="write report.md here")
    p.add_argument("--quiet", action="store_true", help="print only the verdict line")
    p.add_argument("--in-process", action="store_true", help="run the experts and the data agent in this process")
    args = p.parse_args(argv)

    from inspector_contract import plugins, progress

    from .tools.findings_ledger import markdown
    from .inspector_agent import run_rules_only, run_validation

    if not args.quiet:
        progress.setup()
    plugins.load()
    if args.in_process:
        _team_in_process()
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


def _team_in_process() -> None:
    """Serve the other three agents' A2A apps inside this process; requests go through the real A2A protocol."""
    import httpx

    from inspector_contract.a2a import A2AClient

    from .tools.data_probe.client import register as register_data
    from .tools.expert_client.client import register

    try:
        from data_agent.data_agent import app as data_app
        from strategy_agent.strategy_agent import app as strategy_app
        from thoughtspot_agent.thoughtspot_agent import app as thoughtspot_app
    except ImportError as exc:
        raise SystemExit(f"--in-process needs the expert and data agent packages installed: {exc}") from exc
    for side, app in (("thoughtspot", thoughtspot_app), ("strategy", strategy_app)):
        register(side, A2AClient(f"http://{side}-agent/", transport=httpx.ASGITransport(app=app())))
    register_data(A2AClient("http://data-agent/", transport=httpx.ASGITransport(app=data_app())))


if __name__ == "__main__":
    sys.exit(main())
