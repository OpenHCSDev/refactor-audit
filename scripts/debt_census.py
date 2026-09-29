#!/usr/bin/env python3
"""Measure structural debt in a Python package, or in what changed between two revisions.

  debt_census.py --root src/pkg [--rev HEAD]                        snapshot of the tree
  debt_census.py --root src/pkg --base REV [--rev HEAD]             what changed since REV
  debt_census.py --root src/pkg --upstream upstream/main            a fork against its upstream

Changes are measured per touched file as after minus before, so code moved between files
nets to zero and deleted code counts as negative. Run with the project's Python.
"""
from __future__ import annotations

import argparse
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).parent))
from audit.census import Census, CountTable, DensityTable  # noqa: E402
from audit.family import Family  # noqa: E402
from audit.repository import Repository  # noqa: E402


@dataclass(frozen=True)
class Scope:
    repo: Repository
    root: str
    rev: str
    excluded: tuple[str, ...]

    def includes(self, path: str) -> bool:
        return not path.startswith(self.excluded)

    def snapshot(self, rev: str) -> Census:
        return Census.of(self.repo.measure(rev, p) for p in self.repo.python_files(rev, self.root) if self.includes(p))

    def changes_since(self, base: str) -> Census:
        return Census.of(c.delta(self.repo, base, self.rev) for c in self.repo.changes(base, self.rev, self.root)
                         if self.includes(c.path))


class Report(ABC):
    @abstractmethod
    def render(self, top: int) -> str: ...

    @abstractmethod
    def record(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class SnapshotReport(Report):
    rev: str
    census: Census

    def render(self, top: int) -> str:
        return "\n\n".join(part for part in (f"snapshot at {self.rev}", DensityTable((("snapshot", self.census.tally),)).render(),
                                             self.census.render_coverage(), self.census.render_top(top)) if part)

    def record(self) -> dict[str, object]:
        return {"snapshot": self.census.record()}


@dataclass(frozen=True)
class ChangeReport(Report):
    base: str
    rev: str
    census: Census

    def render(self, top: int) -> str:
        label = f"{self.base}..{self.rev}"
        return "\n\n".join(part for part in (f"changed between {label} (touched files, after minus before)",
                                             CountTable(label, self.census.tally).render(),
                                             DensityTable((("added", self.census.tally),)).render(),
                                             self.census.render_coverage(), self.census.render_top(top)) if part)

    def record(self) -> dict[str, object]:
        return {"base": self.base, "rev": self.rev, "change": self.census.record()}


@dataclass(frozen=True)
class ForkReport(Report):
    merge_base: str
    upstream: Census
    fork: Census

    def render(self, top: int) -> str:
        table = DensityTable((("upstream", self.upstream.tally), ("fork adds", self.fork.tally))).render()
        return "\n\n".join(part for part in (f"upstream measured at merge-base {self.merge_base[:10]}; fork = everything changed since",
                                             table, self.upstream.render_coverage(), self.fork.render_coverage(),
                                             self.fork.render_top(top)) if part)

    def record(self) -> dict[str, object]:
        return {"merge_base": self.merge_base, "upstream": self.upstream.record(), "fork": self.fork.record()}


class Mode(Family, ABC, root=True, affix="Mode"):
    """What to measure. Each mode declares the option that selects it."""
    option: ClassVar[str]
    help: ClassVar[str]

    @abstractmethod
    def report(self, scope: Scope) -> Report: ...


@dataclass(frozen=True)
class SnapshotMode(Mode):
    option = ""
    help = "the tree at --rev (the default)"

    def report(self, scope: Scope) -> Report:
        return SnapshotReport(scope.rev, scope.snapshot(scope.rev))


@dataclass(frozen=True)
class ChangeMode(Mode):
    base: str
    option = "--base"
    help = "measure what changed between this revision and --rev"

    def report(self, scope: Scope) -> Report:
        return ChangeReport(self.base, scope.rev, scope.changes_since(self.base))


@dataclass(frozen=True)
class ForkMode(Mode):
    upstream: str
    option = "--upstream"
    help = "compare upstream at the merge-base with everything the fork changed since"

    def report(self, scope: Scope) -> Report:
        merge_base = scope.repo.merge_base(scope.rev, self.upstream)
        return ForkReport(merge_base, scope.snapshot(merge_base), scope.changes_since(merge_base))


class SelectMode(argparse.Action):
    def __call__(self, parser, namespace, value, option_string=None):
        namespace.mode = self.const(value)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--root", required=True, help="package directory, e.g. src/toad")
    ap.add_argument("--rev", default="HEAD")
    ap.add_argument("--exclude", action="append", default=[], help="path prefix to leave out (repeatable)")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json", type=Path)
    modes = ap.add_mutually_exclusive_group()
    for mode in Mode.members():
        if mode.option:
            modes.add_argument(mode.option, action=SelectMode, const=mode, metavar="REV", help=mode.help)
    ap.set_defaults(mode=SnapshotMode())
    return ap


def main() -> int:
    args = parser().parse_args()
    scope = Scope(Repository(args.repo), args.root, args.rev, tuple(args.exclude))
    report = args.mode.report(scope)
    print(report.render(args.top))
    if args.json:
        args.json.write_text(json.dumps(report.record(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
