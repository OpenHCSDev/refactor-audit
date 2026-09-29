#!/usr/bin/env python3
"""Check a plan package before handing it to agents, then zip it.

  check_package.py DIR [--zip OUT.zip]

Runs every check: links (relative .md links and anchors resolve), leaks (compatibility
language that is not prohibiting it; a heuristic, so read each line), consistency (the
shared-abstractions table agrees with every surface header), style (em dashes).
Exits 1 when any check fails; lines needing review do not fail the run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit.plan_package import Check, PlanPackage  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", type=Path)
    ap.add_argument("--zip", type=Path)
    args = ap.parse_args()
    package = PlanPackage.load(args.dir)
    results = {check: check().run(package) for check in Check.members()}
    for check, result in results.items():
        print(result.render(check.family_name))
    if args.zip:
        print(f"zipped {package.zip(args.zip)} files to {args.zip}")
    return 1 if any(result.fails for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
