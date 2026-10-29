"""Measures a checker program takes: Black, Ruff and mypy run on each revision of the
changed files, and Black's layout for the size of each class."""
from __future__ import annotations

import ast
import importlib
import os
import re
import subprocess
import sys
import tempfile
import typing
from abc import abstractmethod
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .project import Project
from .ratchet import (Advisory, BaseSide, Compared, ContentCache, Counts, FileCount, HeadSide,
                      InventoryMeasure, MeasureCount, MemberCount, Side, Site)
from .repository import Repository


@dataclass(frozen=True)
class CheckerSite(Site):
    """One finding a checker reported: where it is and what the checker said, so a change it
    blocks shows the line and the message instead of a count."""

    message: str

    def text(self) -> str:
        return f"{super().text()} ({self.message})"


class SideCounts(Compared):
    """Findings counted per subject on each side, summed over the measure's parts. Each part
    answers its side and the findings it took there."""

    @classmethod
    def measured(cls, found: dict[type[Side], Counter[str]], changed: set[str]) -> list[str]:
        """The subjects compared: every changed file, counting zero where a side has none."""
        return sorted(changed)

    @classmethod
    def taken(cls, results: list) -> dict[type[Side], list[CheckerSite]]:
        """Each side's findings, over all its parts."""
        found: dict[type[Side], list[CheckerSite]] = {side: [] for side in Side.sides()}
        for side, sites in results:
            found[side].extend(sites)
        return found

    @classmethod
    def combine(cls, results: list, repo: Repository, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        found = {side: Counter(site.path for site in sites)
                 for side, sites in cls.taken(results).items()}
        subjects = cls.measured(found, changed)
        def counts(side: type[Side]) -> Counts:
            return {cls.at(subject): found[side][subject] for subject in subjects}
        return counts(BaseSide), counts(HeadSide)

    @classmethod
    def sites(cls, results: list, delta: Counts) -> dict[MeasureCount, list[Site]]:
        """For each grown subject, the head's findings the base does not also report, matched by
        message: the lines and messages the change added."""
        taken = cls.taken(results)
        grown: dict[MeasureCount, list[Site]] = {}
        for key, change in delta.items():
            if change > 0 and cls.owns(key):
                subject = typing.cast(FileCount, key).path
                left = Counter(site.message for site in taken[BaseSide] if site.path == subject)
                added = []
                for site in taken[HeadSide]:
                    if site.path == subject:
                        if left[site.message]:
                            left[site.message] -= 1
                        else:
                            added.append(site)
                grown[key] = added
        return grown


class CheckedRevision(SideCounts):
    """A checker's findings per changed file. Both revisions are checked under the head's
    configuration, so the difference is the code's alone. A path missing at one revision counts
    zero there, as in every per-file measure."""

    # One output line per finding, with its path, and its line and message where it reports them.
    finding: typing.ClassVar[re.Pattern[str]]

    @staticmethod
    @abstractmethod
    def command(tree: Path, selected: list[str], side: type[Side]) -> list[str]:
        """The checker's arguments after `python -m`, run in the revision extracted to the tree."""

    @staticmethod
    def batches(selected: list[str]) -> list[list[str]]:
        """The files checked by one run of the checker."""
        return [selected]

    @staticmethod
    def extract(repo: Repository, ref: str, scratch: Path, selected: list[str]) -> None:
        """Write the revision's files the checker reads."""
        subprocess.run(["tar", "-x", "-C", scratch], input=repo.read("archive", ref), check=True,
                       timeout=60)

    @staticmethod
    def configuration(repo: Repository, ref: str) -> str:
        """The revision's checker configuration blob; a revision without one has none."""
        return repo.git("rev-parse", f"{ref}:pyproject.toml").strip() if repo.tracks(
            ref, "pyproject.toml") else ""

    @classmethod
    def inputs(cls, repo: Repository, ref: str, head: str, root: str, selected: list[str]) -> str:
        """What the findings on the selected files depend on: the revision's whole tree, which
        a checker following imports reads, and the head's configuration."""
        tree = repo.git("rev-parse", f"{ref}^{{tree}}").strip()
        return f"{tree} {cls.configuration(repo, head)}"

    @classmethod
    def findings(cls, repo: Repository, ref: str, head: str, root: str, selected: list[str],
                 side: type[Side]) -> tuple[type[Side], tuple[CheckerSite, ...]]:
        return side, ContentCache.memo(
            partial(cls.checked, repo, ref, head, root, selected, side),
            cls.__name__, root, cls.inputs(repo, ref, head, root, selected), *selected)

    @classmethod
    def checked(cls, repo: Repository, ref: str, head: str, root: str, selected: list[str],
                side: type[Side]) -> tuple[CheckerSite, ...]:
        # A missing checker exits 1 like a finding run, so importing it first fails loud instead.
        importlib.import_module(cls.command(Path(), [], side)[0])
        with tempfile.TemporaryDirectory(prefix="ratchet-check-") as scratch:
            tree = Path(scratch)
            cls.extract(repo, ref, tree, selected)
            configuration = tree / "pyproject.toml"
            configuration.unlink(missing_ok=True)
            if repo.tracks(head, "pyproject.toml"):
                configuration.write_bytes(repo.read("show", f"{head}:pyproject.toml"))
            result = Project.measured().run(
                (sys.executable, "-m", *cls.command(tree, selected, side)),
                cwd=tree, env=cls.environment(root, tree), timeout=600,
            )
        # A checker reports each file under the path it was given, inside the tree or relative to
        # it; mypy also reports the modules it follows outside the tree, which are not measured.
        output = cls.checker_output(result, ref)
        reported = ((Path(tree, match["path"]), match) for match in cls.finding.finditer(output))
        return tuple(cls.site(path.relative_to(tree).as_posix(), match)
                     for path, match in reported if path.is_relative_to(tree))

    @classmethod
    def site(cls, path: str, match: re.Match[str]) -> CheckerSite:
        """One reported finding at its repository path."""
        return CheckerSite(path, int(match["line"]), match["message"])

    @classmethod
    def parts(cls, repo: Repository, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        return tuple(partial(cls.findings, repo, ref, head, root, batch, side)
                     for side in Side.sides() for ref in (side.ref(base, head),)
                     for batch in cls.batches(sorted(changed & repo.python_paths(ref, root)))
                     if batch)


class Unformatted(Advisory, CheckedRevision):
    """1 when Black would rewrite the file: a formatted file stays formatted."""

    finding = re.compile(r"^would reformat (?P<path>.+)$", re.MULTILINE)

    @staticmethod
    def black(*arguments: str) -> list[str]:
        """Black's arguments after `python -m`. Every Black run here is already one of the
        ratchet's pool workers, so Black starts no process pool of its own."""
        return ["black", "--workers", "1", *arguments]

    @staticmethod
    def batches(selected: list[str]) -> list[list[str]]:
        # One run per pool worker: a Black start costs more than checking a file.
        shards = os.process_cpu_count() or 1
        return [selected[shard::shards] for shard in range(shards)]

    @staticmethod
    def extract(repo: Repository, ref: str, scratch: Path, selected: list[str]) -> None:
        # Black reads only the file and the configuration.
        for path, source in repo.sources(ref, set(selected)):
            (scratch / path).parent.mkdir(parents=True, exist_ok=True)
            (scratch / path).write_bytes(source)

    @staticmethod
    def command(tree: Path, selected: list[str], side: type[Side]) -> list[str]:
        return Unformatted.black("--check", *selected)

    @classmethod
    def site(cls, path: str, match: re.Match[str]) -> CheckerSite:
        """Black names the file, not a line: the finding is the whole file's layout."""
        return CheckerSite(path, 1, "Black would reformat this file")

    @classmethod
    def findings(cls, repo: Repository, ref: str, head: str, root: str, selected: list[str],
                 side: type[Side]) -> tuple[type[Side], tuple[CheckerSite, ...]]:
        """Black reads only the file and the configuration, so each file's findings are stored
        under its own blob and one Black run checks the batch's unstored files."""
        configuration = cls.configuration(repo, head)
        blobs = repo.blobs(ref, set(selected))
        parts = {path: (cls.__name__, configuration, path, blobs[path]) for path in selected}
        found = {path: typing.cast(tuple[CheckerSite, ...], stored)
                 for path in selected for stored in ContentCache.stored(*parts[path])}
        fresh = [path for path in selected if path not in found]
        checked = cls.checked(repo, ref, head, root, fresh, side) if fresh else ()
        found |= {path: ContentCache.store(tuple(site for site in checked if site.path == path),
                                           *parts[path])
                  for path in fresh}
        return side, tuple(site for sites in found.values() for site in sites)


class LintFindings(Advisory, CheckedRevision):
    """Ruff diagnostics under the selected rules."""

    finding = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+):\d+: (?P<message>.*)$", re.MULTILINE)

    @staticmethod
    def command(tree: Path, selected: list[str], side: type[Side]) -> list[str]:
        return ["ruff", "check", "--no-cache", "--output-format", "concise", *selected]


class TypeErrors(CheckedRevision):
    """Mypy errors per changed file. Mypy checks the changed files and every module they import,
    so a file's count can change when a module it imports does."""

    finding = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+): error: (?P<message>.*)$",
                         re.MULTILINE)

    @staticmethod
    def mypy(cache: Path, *arguments: str) -> list[str]:
        """Mypy's arguments after `python -m`, keeping its analysis in the cache directory."""
        return ["mypy", "--explicit-package-bases", "--cache-dir", str(cache), "--no-error-summary",
                *arguments]

    @staticmethod
    def command(tree: Path, selected: list[str], side: type[Side]) -> list[str]:
        # Mypy reuses a cached module whose path, size and whole-second mtime match its file, and
        # hashes the file otherwise. Every extracted tree has a new path, so each module is checked
        # against its content while unchanged analysis carries from one run to the next. Each side
        # keeps its own cache: the two revisions run at once, and a base usually matches the
        # previous run's base.
        cache = ContentCache.beside("mypy") / side.family_name
        return TypeErrors.mypy(cache, *(str(tree / path) for path in selected))

    @staticmethod
    def environment(root: str, tree: Path) -> dict[str, str]:
        """Module names start at the source directory, as they do when the package is installed."""
        return {**os.environ, "MYPYPATH": os.pathsep.join(Compared.source_paths(root, tree))}


class GodClassExcess(Advisory, InventoryMeasure[dict[MemberCount, int]]):
    """Independent class lines beyond 500 in Black's layout, including newly introduced classes.
    Lines are counted after the revision's own Black configuration formats the file, so
    formatting a class never reads as growth while Unformatted asks for exactly that.
    Size is an ownership lead, not evidence of a restated fact, so it warns."""

    threshold = 500

    @classmethod
    def inventory(cls, repo: Repository, ref: str, root: str) -> frozenset[str]:
        # Complete inventories distinguish a moved declaration from another
        # same-named class in an unchanged module.
        return repo.python_paths(ref, root)

    @classmethod
    def context(cls, repo: Repository, ref: str) -> str:
        """Black formats each file under the revision's own configuration."""
        return CheckedRevision.configuration(repo, ref)

    @classmethod
    def read_fresh(cls, repo: Repository, ref: str, paths: list[str]) -> dict[str, dict[MemberCount, int]]:
        if not paths:
            return {}
        with tempfile.TemporaryDirectory(prefix="ratchet-layout-") as scratch:
            tree = Path(scratch)
            Unformatted.extract(repo, ref, tree, paths)
            if repo.read("ls-tree", "--name-only", ref, "--", "pyproject.toml"):
                (tree / "pyproject.toml").write_bytes(repo.read("show", f"{ref}:pyproject.toml"))
            subprocess.run(
                (sys.executable, "-m", *Unformatted.black("-q", *paths)),
                cwd=tree,
                check=True,
                timeout=600,
            )
            return {path: cls.reading(path, (tree / path).read_bytes()) for path in paths}

    @classmethod
    def measured(cls, repo: Repository, ref: str, base: str, head: str,
                 readings: dict[str, dict[MemberCount, int]], root: str) -> Counts:
        return {identity: value
                for values in readings.values() for identity, value in values.items()}

    @classmethod
    def reading(cls, path: str, source: bytes) -> dict[MemberCount, int]:
        values = {}
        def visit(node: ast.AST, path: str, scope: tuple[str, ...]) -> None:
            match node:
                case ast.ClassDef(name=name, lineno=start, end_lineno=end, decorator_list=decorators):
                    scope = (*scope, name)
                    start = min([start, *(decorator.lineno for decorator in decorators)])
                    values[cls.at(path).member(".".join(scope))] = max(0, end - start + 1 - cls.threshold)
                case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    scope = (*scope, name)
            for child in ast.iter_child_nodes(node):
                visit(child, path, scope)
        visit(ast.parse(source, path), path, ())
        return values

    @staticmethod
    def declaration(identity: MeasureCount) -> str:
        """The class a count measures; reading() keys every count by its declaration."""
        return typing.cast(MemberCount, identity).name

    @classmethod
    def align(cls, base: Counts, head: Counts) -> tuple[Counts, Counts]:
        def names(values):
            result = {}
            for identity in values:
                result.setdefault(cls.declaration(identity), []).append(identity)
            return result
        before, after = names(base), names(head)
        matched = {}
        consumed = set()
        for identity in head:
            if identity in base:
                matched[identity] = base[identity]
                consumed.add(identity)
                continue
            name = cls.declaration(identity)
            previous = before.get(name, ())
            if len(previous) == 1 and len(after[name]) == 1:
                matched[identity] = base[previous[0]]
                consumed.add(previous[0])
            else:
                # A new declaration must not introduce any god-class excess.
                matched[identity] = 0
        current = dict(head)
        for identity in [identity for identity in base if identity not in consumed]:
            matched[identity] = base[identity]
            current[identity] = 0
        return matched, current
