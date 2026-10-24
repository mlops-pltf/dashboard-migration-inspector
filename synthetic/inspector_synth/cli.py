"""inspector-synth: generate the warehouse seed, migration packages and ground truth; verify witnesses."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import SEED, package, verify, warehouse
from .scenarios import SCENARIOS

ROOT = Path(__file__).resolve().parent.parent


def _generate(args) -> int:
    ids = args.scenario or sorted(SCENARIOS)
    for sid in ids:
        sc = SCENARIOS[sid]
        package.write_package(sc, args.packages)
        package.write_ground_truth(sc, args.ground_truth)
        print(f"{sid}  {sc.expected_verdict:<12} {sc.title}")
    print(f"\n{len(ids)} packages -> {args.packages}\nground truth -> {args.ground_truth}")
    return 0


def _warehouse(args) -> int:
    counts = warehouse.write(args.out, args.seed)
    for table, n in counts.items():
        print(f"{table:<18} {n:>7} rows")
    print(f"\nCSV + Postgres init scripts -> {args.out}")
    return 0


def _verify(args) -> int:
    outcomes = verify.run(args.ground_truth, args.dsn)
    failed = [o for o in outcomes if not o.ok]
    for o in outcomes:
        print(f"{'ok  ' if o.ok else 'FAIL'} {o.scenario}/{o.witness}  expect={o.expect:<6} actual={o.actual:<6} {o.detail}")
    print(f"\n{len(outcomes) - len(failed)}/{len(outcomes)} witnesses behave as expected")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="inspector-synth", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write migration packages and ground truth")
    g.add_argument("--packages", type=Path, default=ROOT / "packages")
    g.add_argument("--ground-truth", type=Path, default=ROOT / "ground_truth")
    g.add_argument("--scenario", action="append", choices=sorted(SCENARIOS), help="repeatable; default all")
    g.set_defaults(func=_generate)

    w = sub.add_parser("warehouse", help="write warehouse CSVs and Postgres init scripts")
    w.add_argument("--out", type=Path, default=ROOT / "warehouse")
    w.add_argument("--seed", type=int, default=SEED)
    w.set_defaults(func=_warehouse)

    v = sub.add_parser("verify", help="run witness queries against the warehouse")
    v.add_argument("--ground-truth", type=Path, default=ROOT / "ground_truth")
    v.add_argument("--dsn", default=verify.DEFAULT_DSN)
    v.set_defaults(func=_verify)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
