#!/usr/bin/env python3
"""The left-rung overlay: debt that NRA's family-based detectors cannot see.

  overlay.py --root src/pkg [--rev HEAD] [--upstream upstream/main] [--top 10] [--json FILE]

With --upstream, each finding is tagged with its origin: `up` if it already existed at the
merge-base, `new` if not; god classes and functions show their size at the merge-base.
Run with the project's Python.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit.findings import Overlay, Package  # noqa: E402
from audit.repository import Repository  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--root", required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--upstream")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    repo = Repository(args.repo)
    overlay = Overlay.run(Package.load(repo, args.rev, args.root))
    baseline = None
    if args.upstream:
        merge_base = repo.merge_base(args.rev, args.upstream)
        baseline = Overlay.run(Package.load(repo, merge_base, args.root)).baseline()
        print(f"origins against merge-base {merge_base[:10]}\n")
    print(overlay.render(args.top, baseline))
    if args.json:
        args.json.write_text(json.dumps(overlay.record(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
