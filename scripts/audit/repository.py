"""The git boundary: everything read from git is decoded here, once, into typed values.

Git's command lines and output formats are spelled only in Repository; every caller asks a
typed question. A path a revision lacks has one answer, owned here: its contents are empty
and its identity is the empty string."""
from __future__ import annotations

import ast
import io
import subprocess
import tokenize
import tomllib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import ClassVar

from .family import Family
from .measures import Measured, MeasuredRevisions, ParseOutcome, Tally, Unparsed, scoped


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Commit:
    """One commit as a log lists it."""

    sha: str
    time: int
    subject: str

    # The log format each field is read from, separated by NUL.
    format: ClassVar[str] = "--format=%h%x00%ct%x00%s"

    @classmethod
    def listed(cls, log: bytes) -> tuple[Commit, ...]:
        return tuple(cls(sha, int(time), subject) for line in log.decode().splitlines()
                     for sha, time, subject in (line.split("\x00", 2),))


@dataclass(frozen=True)
class Repository:
    path: Path

    def _run(self, *args: str, input: bytes = b"") -> bytes:
        """Git's output for one command, as bytes; a failing command fails loud."""
        result = subprocess.run(["git", "-C", str(self.path), *args], input=input,
                                capture_output=True, timeout=20)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.decode().strip()}")
        return result.stdout

    @staticmethod
    def python(path: str) -> bool:
        """Whether a path is a Python module."""
        return path.endswith(".py")

    def blob(self, ref: str, path: str) -> bytes:
        """The file at the revision; a revision without it fails loud."""
        return self._run("show", f"{ref}:{path}")

    def identity(self, ref: str, path: str) -> str:
        """The object a path names at the revision, a file's blob or a directory's tree; empty
        where the revision lacks the path."""
        entry = self._run("--literal-pathspecs", "ls-tree", ref, "--", path)
        return entry.split(b"\t", 1)[0].split()[2].decode() if entry else ""

    def tracks(self, ref: str, path: str) -> bool:
        """Whether the revision holds the path."""
        return bool(self.identity(ref, path))

    def has(self, commit: str) -> bool:
        """Whether the clone holds the commit."""
        listed = self._run("cat-file", "--batch-check=%(objecttype)", input=f"{commit}\n".encode())
        return listed.split() == [b"commit"]

    def contents(self, ref: str, path: str) -> bytes:
        """The file at the revision, empty where the revision lacks it."""
        return self.blob(ref, path) if self.tracks(ref, path) else b""

    def source(self, ref: str, path: str) -> str:
        return self.blob(ref, path).decode()

    def tree(self, ref: str) -> str:
        """The revision's whole tree, by identity."""
        return self._run("rev-parse", f"{ref}^{{tree}}").decode().strip()

    def revision(self, ref: str) -> str:
        """The commit a ref names."""
        return self._run("rev-parse", "--verify", "--end-of-options",
                         f"{ref}^{{commit}}").decode().strip()

    def extract(self, ref: str, into: Path, *paths: str) -> None:
        """Write the revision's tree, or only the paths under it, into a directory."""
        archive = self._run("archive", ref, *(("--", *paths) if paths else ()))
        subprocess.run(["tar", "-x", "-C", str(into)], input=archive, check=True, timeout=60)

    # Listings are read once per process: the measures pass commit identities, whose trees
    # never change.
    @cache
    def python_paths(self, ref: str, root: str) -> frozenset[str]:
        listing = self._run("ls-tree", "-rz", "--name-only", ref, "--", root)
        return frozenset(path for path in listing.decode().split("\0") if self.python(path))

    @cache
    def production_paths(self, ref: str) -> frozenset[str]:
        """Every Python path the revision tracks outside its declared test tree."""
        tests = PytestTestPaths.at(self, ref)
        return frozenset(path for path in self.python_paths(ref, ".")
                         if not any(Path(path).is_relative_to(tree) for tree in tests))

    def blobs(self, ref: str, selected: set[str]) -> dict[str, str]:
        """Each selected path's blob identity at the revision."""
        if not selected:
            return {}
        tree = self._run("--literal-pathspecs", "ls-tree", "-rz", ref, "--", *sorted(selected))
        identities = {}
        for entry in tree.split(b"\0"):
            if entry:
                metadata, path = entry.split(b"\t", 1)
                _, kind, identity = metadata.split()
                if kind != b"blob":
                    raise ValueError(f"Expected a source blob at {path!r}")
                identities[path.decode()] = identity.decode()
        if identities.keys() != selected:
            raise ValueError("Git omitted a selected path")
        return identities

    def sources(self, ref: str, selected: set[str]) -> list[tuple[str, bytes]]:
        """Read the selected Git blobs with one batch command."""
        if not selected:
            return []
        identities = {path: identity.encode()
                      for path, identity in self.blobs(ref, selected).items()}
        objects = dict.fromkeys(identities.values())
        stream = io.BytesIO(self._run("cat-file", "--batch", input=b"\n".join(objects) + b"\n"))
        blobs = {}
        for identity in objects:
            recorded, kind, size = stream.readline().split()
            if recorded != identity or kind != b"blob":
                raise ValueError("Git returned an unrelated object")
            length = int(size)
            content = stream.read(length)
            if len(content) != length or stream.read(1) != b"\n":
                raise ValueError("Git returned an incomplete object")
            blobs[identity] = content
        if stream.read(1):
            raise ValueError("Git returned unrequested objects")
        return [(path, blobs[identities[path]]) for path in sorted(selected)]

    def merge_base(self, a: str, b: str) -> str:
        return self._run("merge-base", a, b).decode().strip()

    def merges(self, since: str, rev: str) -> tuple[Commit, ...]:
        """The first-parent merges after one revision up to another, oldest first."""
        return Commit.listed(self._run("log", "--first-parent", "--merges", "--reverse",
                                       Commit.format, f"{since}..{rev}"))

    def added_at(self, rev: str, path: str) -> Commit:
        """The first-parent commit that first added the path: when it became visible on the
        main line."""
        return Commit.listed(self._run("log", "--first-parent", "--diff-filter=A", Commit.format,
                                       rev, "--", path))[-1]

    def changes(self, base: str, head: str, root: str = ".") -> tuple[Change, ...]:
        """Each path the change touches under the root, with the moves Git detects."""
        fields = iter(self._run("diff", "--find-renames", "--name-status", "-z", base, head,
                                "--", root).split(b"\0")[:-1])
        return tuple(Change.listed(fields))

    def python_changes(self, base: str, head: str, root: str) -> tuple[Change, ...]:
        return tuple(change for change in self.changes(base, head, root) if self.python(change.path))

    def changed_lines(self, base: str, head: str, path: str) -> frozenset[int]:
        """The head's lines that the patch of one path changes."""
        patch = self._run("diff", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color",
                          "--unified=0", "--inter-hunk-context=0", base, head, "--", path)
        lines: set[int] = set()
        for header in patch.decode().splitlines():
            if header.startswith("@@ "):
                # Git owns this hunk header, not the inspected Python source.
                span = header.split()[2].removeprefix("+").split(",")
                start = int(span[0])
                count = int(span[1]) if len(span) == 2 else 1
                lines.update(range(start, start + count))
        return frozenset(lines)


class Change(Family, ABC, root=True):
    """One file's change between two revisions. Names are git's status letters."""

    path: str
    # How many paths git lists for this status: a move or a copy names its source too.
    arity: ClassVar[int] = 1

    @staticmethod
    def listed(fields: Iterator[bytes]) -> Iterator[Change]:
        """The changes in git's ``--name-status -z`` fields: a status, then its paths."""
        for status in fields:
            member = Change.decode(status[:1].decode())
            yield member.from_paths([next(fields).decode() for _ in range(member.arity)])

    @classmethod
    @abstractmethod
    def from_paths(cls, paths: list[str]) -> "Change": ...

    @abstractmethod
    def before(self, census: MeasuredRevisions, base: str) -> ParseOutcome: ...

    @abstractmethod
    def after(self, census: MeasuredRevisions, rev: str) -> ParseOutcome: ...

    def touched(self) -> tuple[str, ...]:
        """The paths whose content the change alters."""
        return (self.path,)

    def moved(self) -> tuple[tuple[str, str], ...]:
        """The move from one path to another this change is, if it is one."""
        return ()

    def added(self) -> tuple[str, ...]:
        """The path this change brings into the tree, if it brings one."""
        return ()

    def delta(self, census: MeasuredRevisions, base: str, rev: str) -> ParseOutcome:
        """What this change added: after minus before. Deleting code counts as negative."""
        before, after = self.before(census, base), self.after(census, rev)
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

    def before(self, census: MeasuredRevisions, base: str) -> ParseOutcome:
        return _absent(self.path)

    def after(self, census: MeasuredRevisions, rev: str) -> ParseOutcome:
        return census.measure(rev, self.path)

    def added(self) -> tuple[str, ...]:
        return (self.path,)


@dataclass(frozen=True)
class Copied(Added, name="C"):
    source: str = ""
    arity = 2

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Copied":
        return cls(paths[-1], paths[0])


@dataclass(frozen=True)
class Modified(Change, name="M"):
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Modified":
        return cls(paths[-1])

    def before(self, census: MeasuredRevisions, base: str) -> ParseOutcome:
        return census.measure(base, self.path)

    def after(self, census: MeasuredRevisions, rev: str) -> ParseOutcome:
        return census.measure(rev, self.path)


@dataclass(frozen=True)
class TypeChanged(Modified, name="T"):
    pass


@dataclass(frozen=True)
class Renamed(Change, name="R"):
    old: str
    path: str
    arity = 2

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Renamed":
        return cls(paths[0], paths[-1])

    def before(self, census: MeasuredRevisions, base: str) -> ParseOutcome:
        return census.measure(base, self.old)

    def after(self, census: MeasuredRevisions, rev: str) -> ParseOutcome:
        return census.measure(rev, self.path)

    def touched(self) -> tuple[str, ...]:
        return (self.old, self.path)

    def moved(self) -> tuple[tuple[str, str], ...]:
        return ((self.old, self.path),)


@dataclass(frozen=True)
class Deleted(Change, name="D"):
    path: str

    @classmethod
    def from_paths(cls, paths: list[str]) -> "Deleted":
        return cls(paths[-1])

    def before(self, census: MeasuredRevisions, base: str) -> ParseOutcome:
        return census.measure(base, self.path)

    def after(self, census: MeasuredRevisions, rev: str) -> ParseOutcome:
        return _absent(self.path)


class PytestTestPaths:
    """Where pytest's configuration declares the test tree; pytest owns these spellings."""

    file = "pyproject.toml"
    table = ("tool", "pytest", "ini_options")
    key = "testpaths"

    @classmethod
    def at(cls, repo: Repository, ref: str) -> tuple[str, ...]:
        """A revision without the configuration file declares no test tree."""
        declared = tomllib.loads(repo.contents(ref, cls.file).decode())
        for section in cls.table:
            declared = declared.get(section, {})
        return tuple(declared.get(cls.key, ()))


@dataclass(frozen=True)
class ChangedPaths:
    """The paths a change touches, and the moves Git pairs among them.

    Both sides of a move are touched, including moves into or out of the declared production
    source boundary, so each revision measures the path it has. A moved path's per-file counts
    then compare at its head path, so moving a file adds nothing it did not already hold."""

    touched: frozenset[str]
    moved: tuple[tuple[str, str], ...]

    @cached_property
    def destinations(self) -> dict[str, str]:
        """Each moved path's head path."""
        return dict(self.moved)

    def at_head(self, path: str) -> str:
        """Where a path is at the head: its move's destination, or itself."""
        return self.destinations.get(path, path)

    @staticmethod
    @cache
    def of(repo: Repository, base: str, head: str) -> ChangedPaths:
        changes = repo.changes(base, head)
        return ChangedPaths(frozenset(path for change in changes for path in change.touched()),
                            tuple(move for change in changes for move in change.moved()))


@dataclass(frozen=True)
class ChangedSource:
    """One head module, its base revision, and the new-side lines changed by its Git patch.
    A path missing at the base is empty there, as in every per-file measure."""

    path: str
    source: bytes
    lines: frozenset[int]
    base_source: bytes

    @cached_property
    def head(self) -> ModuleScope:
        """The head module, walked once for every measure that reads it."""
        return scoped(self.source, self.path)

    @cached_property
    def base(self) -> ModuleScope:
        return scoped(self.base_source, self.path)

    @cached_property
    def tokens(self) -> tuple[tokenize.TokenInfo, ...]:
        return tuple(tokenize.tokenize(io.BytesIO(self.source).readline))

    @classmethod
    def read(cls, repo: Repository, base: str, head: str, path: str) -> ChangedSource:
        return cls(path, repo.blob(head, path), repo.changed_lines(base, head, path),
                   repo.contents(base, path))

    @classmethod
    def departed(cls, repo: Repository, base: str, head: str, path: str) -> ChangedSource:
        """A changed module present at the base, read for what left it; a module the head
        deleted is empty there."""
        return cls(path, repo.contents(head, path), frozenset(), repo.blob(base, path))
