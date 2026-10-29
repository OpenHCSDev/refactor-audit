"""The git boundary: everything read from git is decoded here, once, into typed values."""
from __future__ import annotations

import ast
import io
import subprocess
import tokenize
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path

from .family import Family
from .measures import Measured, ParseOutcome, Tally, Unparsed, measure_source, parsed


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Repository:
    path: Path

    def read(self, *args: str, input: bytes = b"") -> bytes:
        """Git's output for one command, as bytes; a failing command fails loud."""
        result = subprocess.run(["git", "-C", str(self.path), *args], input=input,
                                capture_output=True, timeout=20)
        if result.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {result.stderr.decode().strip()}")
        return result.stdout

    def git(self, *args: str) -> str:
        return self.read(*args).decode()

    def source(self, rev: str, path: str) -> str:
        return self.git("show", f"{rev}:{path}")

    def tracks(self, ref: str, path: str) -> bool:
        """Whether the revision holds the path."""
        return bool(self.read("ls-tree", "--name-only", ref, "--", path).strip())

    def revision(self, ref: str) -> str:
        """The commit a ref names."""
        return self.git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").strip()

    # Listings are read once per process: the measures pass commit identities, whose trees
    # never change.
    @cache
    def python_paths(self, ref: str, root: str) -> frozenset[str]:
        listing = self.read("ls-tree", "-rz", "--name-only", ref, "--", root)
        return frozenset(path.decode() for path in listing.split(b"\0") if path.endswith(b".py"))

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
        tree = self.read("--literal-pathspecs", "ls-tree", "-rz", ref, "--", *sorted(selected))
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
        stream = io.BytesIO(self.read("cat-file", "--batch", input=b"\n".join(objects) + b"\n"))
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


class PytestTestPaths:
    """Where pytest's configuration declares the test tree; pytest owns these spellings."""

    file = "pyproject.toml"
    table = ("tool", "pytest", "ini_options")
    key = "testpaths"

    @classmethod
    def at(cls, repo: Repository, ref: str) -> tuple[str, ...]:
        """A revision without the configuration file declares no test tree."""
        if not repo.tracks(ref, cls.file):
            return ()
        declared = tomllib.loads(repo.source(ref, cls.file))
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

    @staticmethod
    @cache
    def of(repo: Repository, base: str, head: str) -> ChangedPaths:
        # -z name-status: "M\0path\0", or "R096\0old\0new\0" for a move Git detected.
        fields = iter(repo.read("diff", "--find-renames", "--name-status", "-z", base, head,
                                "--").split(b"\0")[:-1])
        touched: set[str] = set()
        moved: list[tuple[str, str]] = []
        for status in fields:
            paths = [next(fields).decode()]
            if status.startswith(b"R"):
                paths.append(next(fields).decode())
                moved.append((paths[0], paths[1]))
            touched.update(paths)
        return ChangedPaths(frozenset(touched), tuple(moved))


@dataclass(frozen=True)
class ChangedSource:
    """One head module, its base revision, and the new-side lines changed by its Git patch.
    A path missing at the base is empty there, as in every per-file measure."""

    path: str
    source: bytes
    lines: frozenset[int]
    base_source: bytes

    @cached_property
    def tree(self) -> ast.Module:
        return parsed(self.source, self.path)

    @cached_property
    def base_tree(self) -> ast.Module:
        return parsed(self.base_source, self.path)

    @cached_property
    def tokens(self) -> tuple[tokenize.TokenInfo, ...]:
        return tuple(tokenize.tokenize(io.BytesIO(self.source).readline))

    @classmethod
    def read(cls, repo: Repository, base: str, head: str, path: str) -> ChangedSource:
        patch = repo.read("diff", "--no-renames", "--no-ext-diff", "--no-textconv",
                          "--no-color", "--unified=0", "--inter-hunk-context=0", base, head,
                          "--", path)
        lines: set[int] = set()
        for header in patch.decode().splitlines():
            if header.startswith("@@ "):
                # Git owns this hunk header, not the inspected Python source.
                span = header.split()[2].removeprefix("+").split(",")
                start = int(span[0])
                count = int(span[1]) if len(span) == 2 else 1
                lines.update(range(start, start + count))
        base_source = repo.read("show", f"{base}:{path}") if repo.tracks(base, path) else b""
        return cls(path, repo.read("show", f"{head}:{path}"), frozenset(lines), base_source)

    @classmethod
    def departed(cls, repo: Repository, base: str, head: str, path: str) -> ChangedSource:
        """A changed module present at the base, read for what left it; a module the head
        deleted is empty there."""
        source = repo.read("show", f"{head}:{path}") if repo.tracks(head, path) else b""
        return cls(path, source, frozenset(), repo.read("show", f"{base}:{path}"))
