"""The git boundary: everything read from git is decoded here, once, into typed values."""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .family import Family
from .measures import Measured, ParseOutcome, Tally, Unparsed, measure_source


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Repository:
    path: Path

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def source(self, rev: str, path: str) -> str:
        return self.git("show", f"{rev}:{path}")

    def python_files(self, rev: str, root: str) -> tuple[str, ...]:
        listing = self.git("ls-tree", "-r", "--name-only", rev, "--", root.rstrip("/") + "/")
        return tuple(path for path in listing.splitlines() if path.endswith(".py"))

    def merge_base(self, a: str, b: str) -> str:
        return self.git("merge-base", a, b).strip()

    def measure(self, rev: str, path: str) -> ParseOutcome:
        return measure_source(path, self.source(rev, path))

    def changes(self, base: str, rev: str, root: str) -> tuple["Change", ...]:
        listing = self.git("diff", "--name-status", "-M", base, rev, "--", root)
        return tuple(Change.parse(line) for line in listing.splitlines() if line.endswith(".py"))


class Change(Family, ABC, root=True):
    """One file's change between two revisions. Names are git's status letters."""

    path: str

    @staticmethod
    def parse(line: str) -> "Change":
        status, *paths = line.split("\t")
        return Change.decode(status[0]).from_paths(paths)

    @classmethod
    @abstractmethod
    def from_paths(cls, paths: list[str]) -> "Change": ...

    @abstractmethod
    def before(self, repo: Repository, base: str) -> ParseOutcome: ...

    @abstractmethod
    def after(self, repo: Repository, rev: str) -> ParseOutcome: ...

    def delta(self, repo: Repository, base: str, rev: str) -> ParseOutcome:
        """What this change added: after minus before. Deleting code counts as negative."""
        before, after = self.before(repo, base), self.after(repo, rev)
        match before, after:
            case Measured(tally=old), Measured(tally=new):
                return Measured(self.path, after.code_lines, new - old)
            case Unparsed() as failed, _:
                return failed
            case _, failed:
                return failed


def _absent(path: str) -> ParseOutcome:
    return Measured(path, 0, Tally())


@dataclass(frozen=True)
class Added(Change, name="A"):
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Added":
        return cls(paths[-1])

    def before(self, repo: Repository, base: str) -> ParseOutcome:
        return _absent(self.path)

    def after(self, repo: Repository, rev: str) -> ParseOutcome:
        return repo.measure(rev, self.path)


@dataclass(frozen=True)
class Copied(Added, name="C"):
    source: str = ""

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Copied":
        return cls(paths[-1], paths[0])


@dataclass(frozen=True)
class Modified(Change, name="M"):
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Modified":
        return cls(paths[-1])

    def before(self, repo: Repository, base: str) -> ParseOutcome:
        return repo.measure(base, self.path)

    def after(self, repo: Repository, rev: str) -> ParseOutcome:
        return repo.measure(rev, self.path)


@dataclass(frozen=True)
class TypeChanged(Modified, name="T"):
    pass


@dataclass(frozen=True)
class Renamed(Change, name="R"):
    old: str
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Renamed":
        return cls(paths[0], paths[-1])

    def before(self, repo: Repository, base: str) -> ParseOutcome:
        return repo.measure(base, self.old)

    def after(self, repo: Repository, rev: str) -> ParseOutcome:
        return repo.measure(rev, self.path)


@dataclass(frozen=True)
class Deleted(Change, name="D"):
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Deleted":
        return cls(paths[-1])

    def before(self, repo: Repository, base: str) -> ParseOutcome:
        return repo.measure(base, self.path)

    def after(self, repo: Repository, rev: str) -> ParseOutcome:
        return _absent(self.path)
