"""Where a module came from: the pull request that added it, decoded once from git's log."""
from __future__ import annotations

import collections
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from .family import Family
from .measures import Measured, Tally, interpreter
from .repository import Repository


class ChangeKind(Family, ABC, root=True, affix="Change"):
    """Why a module was written, judged by the branch that introduced it."""


class Attributed:
    """Capability: listed by origin, and timed against the foundation."""


class RefactorChange(ChangeKind): ...
class FeatureChange(ChangeKind, Attributed): ...
class DirectChange(ChangeKind, Attributed): ...


class ModuleOrigin(ABC):
    merged_at: int
    branch_kind: str

    @abstractmethod
    def kind(self, refactor_branches: re.Pattern) -> type[ChangeKind]: ...

    @abstractmethod
    def label(self) -> str: ...


@dataclass(frozen=True)
class MergedPullRequest(ModuleOrigin):
    merged_at: int
    number: int
    branch: str
    github_merge: ClassVar[re.Pattern] = re.compile(r"Merge pull request #(\d+) from [^/\s]+/(\S+)")
    squash_merge: ClassVar[re.Pattern] = re.compile(r"\(#(\d+)\)\s*$")

    def kind(self, refactor_branches: re.Pattern) -> type[ChangeKind]:
        return RefactorChange if refactor_branches.search(self.branch) else FeatureChange

    def label(self) -> str:
        return f"#{self.number:<5} {self.branch[:48]}"

    @property
    def branch_kind(self) -> str:
        return self.branch.partition("/")[0]


@dataclass(frozen=True)
class DirectCommit(ModuleOrigin):
    merged_at: int
    subject: str

    def kind(self, refactor_branches: re.Pattern) -> type[ChangeKind]:
        return DirectChange

    def label(self) -> str:
        return f"direct  {self.subject[:48]}"

    @property
    def branch_kind(self) -> str:
        return "direct"


def origin_of(repo: Repository, rev: str, path: str) -> ModuleOrigin:
    """The first-parent commit that added the path: when the code became visible on the main line."""
    log = repo.git("log", "--first-parent", "--diff-filter=A", "--format=%ct%x00%s", rev, "--", path).splitlines()
    timestamp, _, subject = log[-1].partition("\x00")
    return decode_origin(int(timestamp), subject)


def decode_origin(merged_at: int, subject: str) -> ModuleOrigin:
    """A first-parent commit subject, decoded once: a merged pull request, or a direct commit."""
    match MergedPullRequest.github_merge.search(subject), MergedPullRequest.squash_merge.search(subject):
        case re.Match() as merge, _:
            return MergedPullRequest(merged_at, int(merge.group(1)), merge.group(2))
        case None, re.Match() as squash:
            return MergedPullRequest(merged_at, int(squash.group(1)), subject)
    return DirectCommit(merged_at, subject)


class Timing(Family, ABC, root=True, affix="Foundation"):
    @staticmethod
    def of(origin: ModuleOrigin, foundation: ModuleOrigin) -> type["Timing"]:
        return MergedBeforeFoundation if origin.merged_at < foundation.merged_at else MergedAfterFoundation


class MergedBeforeFoundation(Timing): ...
class MergedAfterFoundation(Timing): ...


@dataclass
class Group:
    modules: int = 0
    tally: Tally = field(default_factory=Tally)

    def add(self, measured: Measured) -> None:
        self.modules += 1
        self.tally = self.tally + measured.tally


@dataclass
class Attribution:
    since: str
    rev: str
    by_kind: dict[type[ChangeKind], Group] = field(default_factory=lambda: collections.defaultdict(Group))
    by_origin: dict[ModuleOrigin, Group] = field(default_factory=lambda: collections.defaultdict(Group))
    timing: collections.Counter = field(default_factory=collections.Counter)
    unparsed: list[str] = field(default_factory=list)

    def record(self, origin: ModuleOrigin, kind: type[ChangeKind], measured: Measured,
               foundation: ModuleOrigin | None) -> None:
        self.by_kind[kind].add(measured)
        if issubclass(kind, Attributed):
            self.by_origin[origin].add(measured)
            if foundation is not None:
                self.timing[Timing.of(origin, foundation)] += 1

    @staticmethod
    def _origin_line(origin: ModuleOrigin, group: Group) -> str:
        return (f"  {origin.label():<56}{group.modules:>4} modules {group.tally.code_lines:>7} lines  "
                f"headline {group.tally.headline():>5.0f}/kL")

    def render(self, foundation: str) -> str:
        lines = [f"{sum(g.modules for g in self.by_kind.values())} modules added between {self.since} and {self.rev}", "",
                 f"{'':<12}{'modules':>8}{'code lines':>12}{'headline /kL':>14}"]
        lines += [f"{kind.family_name:<12}{group.modules:>8}{group.tally.code_lines:>12}{group.tally.headline():>14.0f}"
                  for kind, group in self.by_kind.items()]
        if self.timing:
            counts = ", ".join(f"{timing.family_name.replace('_', ' ')}: {n}" for timing, n in self.timing.items())
            lines += ["", f"feature modules relative to {foundation}: {counts}",
                      "(first-parent merge times: when the code became visible on the main line)"]
        lines += ["", "modules by origin (" + ", ".join(k.family_name for k in ChangeKind.members_with(Attributed)) + "):"]
        ranked = sorted(self.by_origin, key=lambda origin: -self.by_origin[origin].tally.code_lines)
        lines += [self._origin_line(origin, self.by_origin[origin]) for origin in ranked]
        if self.unparsed:
            lines += ["", f"WARNING: {len(self.unparsed)} modules did not parse under Python {interpreter()}; "
                          "re-run with the project's Python."]
        return "\n".join(lines)
