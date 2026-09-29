#!/usr/bin/env python3
"""Review merged pull requests: the debt each merge added, over the files it changed.

  merge_review.py --root src/pkg --since REV [--rev origin/main] [--top 10]

For every first-parent merge between REV and --rev, measures its changed files after minus
before, then reports the net change per measure, the merges that added the most debt (each
measure weighted by its declared weight), debt added per kind of branch, and every merge
that added dispatch candidates. These are leads, not proof that polymorphism was bypassed.
Parse failures withhold the rankings and exit nonzero. Run with the project's Python.
"""
from __future__ import annotations

import argparse
import collections
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit.census import Census  # noqa: E402
from audit.measures import DispatchMeasure, Measure, Tally, Unparsed, interpreter  # noqa: E402
from audit.origin import ModuleOrigin, decode_origin  # noqa: E402
from audit.repository import Repository  # noqa: E402


@dataclass(frozen=True)
class MergeReview:
    sha: str
    origin: ModuleOrigin
    change: Tally
    unparsed: tuple[Unparsed, ...]

    @classmethod
    def of(cls, repo: Repository, sha: str, merged_at: int, subject: str, root: str) -> "MergeReview":
        census = Census.of(file_change.delta(repo, f"{sha}^1", sha)
                           for file_change in repo.changes(f"{sha}^1", sha, root))
        return cls(sha, decode_origin(merged_at, subject), census.tally, census.unparsed)

    def added(self) -> int:
        return sum(measure.weight * max(0, self.change[measure]) for measure in Measure.members())

    def rose(self, limit: int) -> str:
        risen = sorted(((m, self.change[m]) for m in Measure.members() if self.change[m] > 0 and m.weight),
                       key=lambda item: -item[0].weight * item[1])
        return ", ".join(f"{m.family_name} +{n}" for m, n in risen[:limit])

    def dispatch_added(self) -> tuple[tuple[type[Measure], int], ...]:
        return tuple((m, self.change[m]) for m in Measure.members() if issubclass(m, DispatchMeasure) and self.change[m] > 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--root", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--rev", default="origin/main")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    repo = Repository(args.repo)
    log = repo.git("log", "--first-parent", "--merges", "--reverse", "--format=%h%x00%ct%x00%s", f"{args.since}..{args.rev}")
    reviews = []
    for line in log.splitlines():
        sha, merged_at, subject = line.split("\x00", 2)
        reviews.append(MergeReview.of(repo, sha, int(merged_at), subject, args.root))
    failures = [(review, failure) for review in reviews for failure in review.unparsed]
    if failures:
        print(f"WARNING: {len(failures)} changed-file measurements did not parse under Python "
              f"{interpreter()}. Review incomplete; debt rankings withheld. "
              "Re-run with the project's Python and fix any invalid source.", file=sys.stderr)
        for review, failure in failures:
            print(f"  {review.sha}^1..{review.sha}  {failure.path}: {failure.error}", file=sys.stderr)
        return 2
    net = sum((r.change for r in reviews), Tally())
    print(f"{len(reviews)} merges, {args.since}..{args.rev}\n")
    print("net change: " + ", ".join(f"{m.family_name} {net[m]:+d}" for m in Measure.members() if net[m]))
    print(f"code lines {net.code_lines:+d}\n")
    print("merges adding the most debt (weighted):")
    for review in sorted(reviews, key=lambda r: -r.added())[: args.top]:
        print(f"  {review.added():>5}  {review.origin.label()}  {review.rose(4)}")
    by_kind = collections.Counter()
    for review in reviews:
        by_kind[review.origin.branch_kind] += review.added()
    print("\nweighted debt added, by branch kind: " + ", ".join(f"{k} {v}" for k, v in by_kind.most_common() if v))
    dispatch = [(r, m, n) for r in reviews for m, n in r.dispatch_added()]
    print("\nmerges that added dispatch candidates (verify ownership at each site):")
    for review, measure, n in dispatch:
        print(f"  {review.origin.label()}  {measure.family_name} +{n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
