#!/usr/bin/env python3
"""Attribute new modules to the pull request that created them.

  attribute_origin.py --root src/pkg --since REV [--rev HEAD]
                      [--refactor-branches REGEX] [--foundation src/pkg/module.py]

Classifies each module added since REV by the branch of the first-parent merge that
introduced it, and prints debt density per class and per pull request. With --foundation,
reports whether each feature module reached the main line before or after that file did,
which answers "was this written before the abstractions it should use?". Classify by
origin, never by module name. Run with the project's Python.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit.measures import Measured, Unparsed  # noqa: E402
from audit.origin import Attribution, origin_of  # noqa: E402
from audit.repository import Repository  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--root", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--refactor-branches", type=re.compile, default=re.compile(r"refactor|integration/"))
    ap.add_argument("--foundation", default="")
    args = ap.parse_args()
    repo = Repository(args.repo)
    existing = frozenset(repo.python_files(args.since, args.root))
    foundation = origin_of(repo, args.rev, args.foundation) if args.foundation else None
    attribution = Attribution(args.since, args.rev)
    for path in repo.python_files(args.rev, args.root):
        if path in existing:
            continue
        origin = origin_of(repo, args.rev, path)
        match repo.measure(args.rev, path):
            case Measured() as measured:
                attribution.record(origin, origin.kind(args.refactor_branches), measured, foundation)
            case Unparsed():
                attribution.unparsed.append(path)
    print(attribution.render(args.foundation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
