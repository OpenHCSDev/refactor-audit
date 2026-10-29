"""The ratchet: every Compared measure, taken at two revisions of a change and compared.

A change may not grow a blocking measure, file by file where the measure counts per file,
and may not write a new occurrence of a strict measure on the lines it changes. A comparison
runs each measure's independent parts in the measured project's process pool and reports the
counts at both revisions, their difference, the lines that introduced a strict occurrence and
the sites of every grown measure.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import os
import subprocess
import symtable
import sys
import typing
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import as_completed
from dataclasses import dataclass
from functools import cached_property, partial
from pathlib import Path

from metaclass_registry.mro_dispatch import MroDispatch, handles

from .family import Family
from .lexical import DecoratorBinding, NominalProof, ScopeNames, UnprovedIdentity
from .measures import Measure, parsed
from .project import AUDIT_SCRIPTS, Project
from .repository import ChangedPaths, ChangedSource


class Side(Family, ABC, root=True, affix="Side"):
    """One of the two revisions a comparison measures; its declared name names a checker's cache."""

    @staticmethod
    @abstractmethod
    def ref(base: str, head: str) -> str:
        """This side's revision of the compared pair."""

    @classmethod
    def sides(cls) -> tuple[type[Side], ...]:
        return cls.members()


class BaseSide(Side):
    @staticmethod
    def ref(base: str, head: str) -> str:
        return base


class HeadSide(Side):
    @staticmethod
    def ref(base: str, head: str) -> str:
        return head


def processors() -> int:
    """The processors this process may run on. The interpreter answers none when it cannot
    tell, and one is then all a comparison can count on."""
    return os.process_cpu_count() or 1


class Runner(ABC):
    """What a comparison runs: independent parts in the project's pool, combined into the
    counts at each revision. A measure runs itself unless a shared runner takes its counts."""

    @classmethod
    @abstractmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        """Independent pieces of the comparison; compare() runs each in a separate process."""

    @classmethod
    @abstractmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        """The before/after measurements from the results of parts(), in order."""

    @classmethod
    def align(cls, base: Counts, head: Counts) -> tuple[Counts, Counts]:
        return base, head

    @classmethod
    def grown_sites(cls, results: list, delta: Counts,
                    changed: ChangedPaths) -> dict[MeasureCount, list[Site]]:
        """The sites of each grown count that the parts' results already hold. A runner whose
        results hold none answers none; a LocatedSource reads its sites from the head afterwards."""
        return {}


class Compared(Measure, Runner):
    """Capability: the ratchet compares this measure's counts at the two revisions of a change."""

    @classmethod
    def owns(cls, key: MeasureCount) -> bool:
        """Whether a count is one of this measure's."""
        return key.measure is cls

    @classmethod
    def total(cls) -> MeasureCount:
        return MeasureCount(cls)

    @classmethod
    def at(cls, path: str) -> FileCount:
        return FileCount(cls, path)

    @classmethod
    def grown(cls, delta: Counts) -> bool:
        """Whether any of this measure's counts grew."""
        return any(value > 0 for key, value in delta.items() if cls.owns(key))

    @classmethod
    def blocks(cls, delta: Counts) -> bool:
        return cls.grown(delta)

    @classmethod
    def missing(cls, head: Counts, introduced: dict[FileCount, list[int]]) -> list[MeasureCount]:
        """This measure's counts the comparison has no required evidence for; a measure that
        needs none beyond its counts misses nothing."""
        return []

    @classmethod
    def grew(cls, key: MeasureCount, grown: int) -> str:
        """The line reporting one count's growth; the count words it."""
        return key.grew(grown)

    @classmethod
    def run_by(cls) -> type[Runner]:
        """What a comparison runs to take this measure's counts."""
        return cls

    @staticmethod
    def environment(root: str, tree: Path) -> dict[str, str]:
        """The environment of a tool run in a revision extracted to the tree."""
        return dict(os.environ)

    @classmethod
    def checker_output(cls, result: subprocess.CompletedProcess[bytes], subject: str) -> str:
        """A checker's report, once it exits as a finding run: zero or one."""
        output = (result.stdout + result.stderr).decode()
        if result.returncode not in (0, 1):
            raise ValueError(f"{cls.__name__} could not check {subject}: {output}")
        return output

    @staticmethod
    def source_paths(root: str, tree: Path) -> tuple[str, ...]:
        """Where a revision extracted to the tree imports from: the measured root's directory,
        then its ``src`` (``agent-comms install`` runs tools with it), never the installed one,
        then the pinned audit's scripts, a submodule no ``git archive`` of the tree carries."""
        return (*dict.fromkeys(str(tree / directory) for directory in (Path(root).parts[0], "src")),
                str(AUDIT_SCRIPTS))


@dataclass(frozen=True)
class MeasureCount:
    """What one reported count counts: here, a measure's total over the change. Its text is
    only the report's key; the comparison matches the record."""

    measure: type[Compared]

    @property
    def text(self) -> str:
        return self.measure.__name__

    def moved(self, changed: ChangedPaths) -> MeasureCount:
        """The count at its head path; a total belongs to no path."""
        return self

    def warning(self, text: str) -> str:
        """A check annotation for this count; a total annotates no file."""
        return f"::warning::{text}"

    def grew(self, grown: int) -> str:
        """The line saying this count grew."""
        return f"grew {self.text} +{grown}"

    @staticmethod
    def paired(counts: Counts, changed: ChangedPaths) -> Counts:
        """Counts at their head paths; a moved file's key zero-filled at its other path adds 0."""
        result: Counts = {}
        for identity, count in counts.items():
            at = identity.moved(changed)
            result[at] = result.get(at, 0) + count
        return result


@dataclass(frozen=True)
class FileCount(MeasureCount):
    """A measure's count in one file, so an improvement in one file cannot hide a regression
    in another."""

    path: str

    @property
    def text(self) -> str:
        return f"{super().text}:{self.path}"

    def moved(self, changed: ChangedPaths) -> FileCount:
        return dataclasses.replace(self, path=changed.at_head(self.path))

    def warning(self, text: str) -> str:
        return f"::warning file={self.path}::{text}"

    def member(self, name: str) -> MemberCount:
        return MemberCount(self.measure, self.path, name)


@dataclass(frozen=True)
class MemberCount(FileCount):
    """A measure's count for one declaration in a file, named by its dotted scope."""

    name: str

    @property
    def text(self) -> str:
        return f"{super().text}::{self.name}"


@dataclass(frozen=True)
class PhaseSiteCount(MeasureCount):
    """One site where a done plan phase's evidence fails."""

    phase: type
    site: str

    @property
    def text(self) -> str:
        return f"{super().text}:{self.phase.__name__}::{self.site}"

    def grew(self, grown: int) -> str:
        return f"done plan phase {self.phase.__name__} no longer holds: {self.site}"


# One revision's counts, each under the record of what it counts.
type Counts = dict[MeasureCount, int]


class ChangeMeasure(Compared):
    """A count of the change as a whole, taken in one piece."""

    @classmethod
    @abstractmethod
    def counted(cls, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        """The before/after counts."""

    @classmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        return (partial(cls.counted, repo, base, head, changed, root),)

    @classmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        before, after = results[0]
        return before, after


class InventoryReading[Reading](Runner):
    """A reading of every file a revision holds, not only the changed ones. A file the change
    did not touch reads the same at both revisions, so it is read once; the reading is split
    across processes."""

    @classmethod
    @abstractmethod
    def inventory(cls, repo: Project, ref: str, root: str) -> frozenset[str]:
        """The paths measured at this revision."""

    @classmethod
    @abstractmethod
    def reading(cls, repo: Project, path: str, source: bytes) -> Reading:
        """What the measure needs from one file, independent of the revision holding it."""

    @classmethod
    @abstractmethod
    def measured(cls, repo: Project, ref: str, base: str, head: str, readings: dict[str, Reading],
                 root: str) -> Counts:
        """This revision's measurements from its files' readings, in path order."""

    @classmethod
    def context(cls, repo: Project, ref: str) -> str:
        """What a file's reading depends on besides the file itself."""
        return ""

    @classmethod
    def read(cls, repo: Project, ref: str, paths: list[str]) -> dict[str, Reading]:
        """Each file's reading, computed once per blob and context."""
        identities = repo.blobs(ref, set(paths))
        context = cls.context(repo, ref)
        parts = {path: (cls.__name__, context, path, identities[path]) for path in paths}
        readings = {path: typing.cast(Reading, stored)
                    for path in paths for stored in repo.stored(*parts[path])}
        fresh = cls.read_fresh(repo, ref, sorted(set(paths) - readings.keys()))
        return {**readings, **{path: repo.store(reading, *parts[path])
                               for path, reading in fresh.items()}}

    @classmethod
    def read_fresh(cls, repo: Project, ref: str, paths: list[str]) -> dict[str, Reading]:
        return {path: cls.reading(repo, path, source)
                for path, source in repo.sources(ref, set(paths))}

    @classmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        before, after = cls.inventory(repo, base, root), cls.inventory(repo, head, root)
        shared = sorted((before & after) - ChangedPaths.of(repo, base, head).touched)
        shards = processors()
        return (partial(cls.read, repo, base, sorted(before.difference(shared))),
                partial(cls.read, repo, head, sorted(after.difference(shared))),
                *(partial(cls.read, repo, head, shared[shard::shards]) for shard in range(shards)))

    @classmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        before, after, *shards = results
        shared = {path: reading for shard in shards for path, reading in shard.items()}
        return cls.align(*(cls.measured(repo, ref, base, head,
                                        dict(sorted({**shared, **own}.items())), root)
                           for ref, own in ((base, before), (head, after))))


class InventoryMeasure[Reading](InventoryReading[Reading], Compared):
    """A measure of every file a revision holds."""


class CountedSource(Compared):
    """A measure that is one number per source file: a pass over the tree SyntaxPasses parses
    once for every pass. Passes read the shared tree and never write it."""

    @classmethod
    @abstractmethod
    def count(cls, repo: Project, source: bytes, filename: str) -> int:
        """Return this file's count in the measured project."""

    @classmethod
    @abstractmethod
    def tally(cls, counts: list[tuple[str, int]]) -> Counts:
        """A revision's measurements from each selected file's count, in path order."""

    @classmethod
    def snapshot(cls, repo: Project, sources: list[tuple[str, bytes]]) -> Counts:
        return cls.tally([(path, cls.count(repo, source, path)) for path, source in sources])

    # The file's syntax, parsed once while every pass counts it.
    tree = staticmethod(parsed)

    @classmethod
    def run_by(cls) -> type[Runner]:
        return SyntaxPasses

    @classmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        return SyntaxPasses.parts(repo, base, head, changed, root)

    @classmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        before, after = SyntaxPasses.combine(results, repo, base, head, changed, root)
        return ({key: value for key, value in before.items() if cls.owns(key)},
                {key: value for key, value in after.items() if cls.owns(key)})


@dataclass(frozen=True)
class Site:
    """One counted occurrence: the file and line a grown measure points at."""

    path: str
    line: int

    def text(self) -> str:
        return f"{self.path}:{self.line}"


class LocatedSource(CountedSource):
    """A pass that knows the line of every occurrence it counts; its count is how many."""

    @classmethod
    @abstractmethod
    def lines(cls, repo: Project, source: bytes, filename: str) -> list[int]:
        """The line of each occurrence, once per unit counted."""

    @classmethod
    def count(cls, repo: Project, source: bytes, filename: str) -> int:
        return len(cls.lines(repo, source, filename))

    @classmethod
    def located(cls, repo: Project,
                sources: list[tuple[str, bytes]]) -> dict[MeasureCount, list[Site]]:
        """Each count's sites. A site belongs to the count one occurrence in its file
        tallies to, so the count layout stays tally's alone."""
        found: dict[MeasureCount, list[Site]] = {}
        for path, source in sources:
            (key,) = cls.tally([(path, 1)])
            found.setdefault(key, []).extend(Site(path, line) for line in cls.lines(repo, source, path))
        return found


class SourcePosition(MroDispatch):
    """The lines a counted node spans, chosen by the node's own syntax class. The parser
    positions every node a pass counts except a comprehension clause."""

    @handles(ast.stmt, ast.expr, ast.arg, ast.keyword, ast.excepthandler, ast.alias, ast.pattern)
    def positioned(self, node: ast.stmt | ast.expr | ast.arg | ast.keyword | ast.excepthandler
                   | ast.alias | ast.pattern) -> range:
        return range(node.lineno, typing.cast(int, node.end_lineno) + 1)

    def lines(self, node: ast.AST) -> range:
        return type(self).handler_for(type(node))(self, node)


class RosterPosition(SourcePosition):
    """A loop's roster is the literal it runs over, which also places a comprehension."""

    @handles(ast.For, ast.AsyncFor, ast.comprehension)
    def loop(self, node: ast.For | ast.AsyncFor | ast.comprehension) -> range:
        return self.lines(node.iter)


class NamePosition(SourcePosition):
    """A name occupies one line: an attribute's is where the attribute ends."""

    @handles(ast.Attribute)
    def attribute(self, node: ast.Attribute) -> range:
        line = typing.cast(int, node.end_lineno)
        return range(line, line + 1)

    def lines(self, node: ast.AST) -> range:
        line = super().lines(node).start
        return range(line, line + 1)


class OccurrenceMeasure(LocatedSource):
    """Shared additive counting for syntactic occurrence measures."""

    position: typing.ClassVar[SourcePosition] = SourcePosition()

    @staticmethod
    @abstractmethod
    def occurrences(node: ast.AST) -> int:
        """Return this node's contribution, excluding its descendants."""

    @staticmethod
    def sites(repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        """The nodes this measure reads; every node unless a measure excludes some."""
        return ast.walk(tree)

    @classmethod
    def lines(cls, repo: Project, source: bytes, filename: str) -> list[int]:
        tree = cls.tree(source, filename)
        return [cls.position.lines(node).start for node in cls.sites(repo, tree, filename)
                for _ in range(cls.occurrences(node))]

    @classmethod
    def tally(cls, counts: list[tuple[str, int]]) -> Counts:
        return {cls.total(): sum(count for _, count in counts)}


class PerFileMeasure(CountedSource):
    """An improvement in one file cannot hide a regression in another."""

    @classmethod
    def tally(cls, counts: list[tuple[str, int]]) -> Counts:
        return {cls.at(path): count for path, count in counts}

    @classmethod
    def align(cls, base: Counts, head: Counts) -> tuple[Counts, Counts]:
        identities = sorted(base.keys() | head.keys(), key=lambda identity: identity.text)
        return ({key: base.get(key, 0) for key in identities},
                {key: head.get(key, 0) for key in identities})


class PerFileOccurrenceMeasure(PerFileMeasure, OccurrenceMeasure):
    """A per-node count, kept separately for each file."""


class SyntaxPasses(InventoryReading[dict[type[CountedSource], int]]):
    """The front end of every CountedSource pass, as a compiler runs its passes over one tree.
    It is the runner its passes name, not a measure: it owns no count of its own.

    Each changed file is read and parsed once per revision and every pass counts that tree;
    the counts are stored under the file's blob, so a file an earlier run saw is not parsed
    again. The changed files of each revision are split across the pool."""

    @staticmethod
    def passes() -> tuple[type[CountedSource], ...]:
        return Measure.members_with(CountedSource)

    @classmethod
    def inventory(cls, repo: Project, ref: str, root: str) -> frozenset[str]:
        return repo.python_paths(ref, root)

    @classmethod
    def reading(cls, repo: Project, path: str, source: bytes) -> dict[type[CountedSource], int]:
        return {measure: measure.count(repo, source, path) for measure in cls.passes()}

    @classmethod
    def measured(cls, repo: Project, ref: str, base: str, head: str,
                 readings: dict[str, dict[type[CountedSource], int]], root: str) -> Counts:
        return {key: value for measure in cls.passes() for key, value in measure.tally(
            [(path, reading[measure]) for path, reading in readings.items()]).items()}

    @classmethod
    def side(cls, side: type[Side], repo: Project, ref: str,
             paths: list[str]) -> tuple[type[Side], dict[str, dict[type[CountedSource], int]]]:
        return side, cls.read(repo, ref, paths)

    @classmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        shards = processors()
        return tuple(partial(cls.side, side, repo, ref, paths[shard::shards])
                     for side in Side.sides() for ref in (side.ref(base, head),)
                     for paths in (sorted(changed & cls.inventory(repo, ref, root)),)
                     for shard in range(min(shards, len(paths))))

    @classmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        readings: dict[type[Side], dict[str, dict[type[CountedSource], int]]] = {
            side: {} for side in Side.sides()}
        for side, read in results:
            readings[side].update(read)
        return PerFileMeasure.align(*(cls.measured(repo, side.ref(base, head), base, head,
                                                   dict(sorted(readings[side].items())), root)
                                      for side in Side.sides()))


class Advisory(Compared):
    """Growth that restates no fact, such as a lint convention or layout: the ratchet warns
    on the file and never fails on it. Only measures of restated facts block a merge."""

    @classmethod
    def blocks(cls, delta: Counts) -> bool:
        return False

    @classmethod
    def grew(cls, key: MeasureCount, grown: int) -> str:
        return key.warning(f"{cls.__name__} +{grown} (warns, never blocks)")


@dataclass(frozen=True)
class DeclaredClass:
    """One top-level class of one changed module at one revision, read for its fields, its
    methods and its direct parents."""

    path: str
    tree: ast.Module
    node: ast.ClassDef

    @classmethod
    def top(cls, path: str, tree: ast.Module) -> dict[str, DeclaredClass]:
        """The module's classes whose module-level name is still that one declaration."""
        return {name: cls(path, tree, statement)
                for name, (_, statement) in DecoratorBinding.declarations(tree, "", tree.body).items()
                if isinstance(statement, ast.ClassDef)}

    @property
    def scope(self) -> str:
        return f".{self.node.name}"

    @cached_property
    def fields(self) -> dict[str, ast.AnnAssign]:
        """The class body's annotated names bound by that annotation alone."""
        return {name: statement for name, (_, statement) in DecoratorBinding.declarations(
                    self.tree, self.scope, self.node.body).items()
                if isinstance(statement, ast.AnnAssign)}

    @cached_property
    def methods(self) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        """The class body's functions bound by that definition alone."""
        return {name: statement for name, (_, statement) in DecoratorBinding.declarations(
                    self.tree, self.scope, self.node.body).items()
                if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)}

    def binds(self, name: str) -> bool:
        """Whether the class scope still binds ``name`` in any form."""
        return name in DecoratorBinding.bindings(self.tree, self.scope)

    @cached_property
    def identity(self) -> NominalProof:
        """This class as its own module's declaration names it."""
        return DecoratorBinding.identity(self.tree, self.path, self.scope, ast.Name(id=self.node.name))

    def derives(self, parent: DeclaredClass) -> bool:
        """Whether one direct base resolves to ``parent``. Bases are read from this class's
        scope, so a base name the class body rebinds proves nothing; a call, a subscript or a
        name the module binds twice resolves to no class."""
        return any(DecoratorBinding.identity(self.tree, self.path, self.scope, base).same(parent.identity)
                   for base in self.node.bases)

    @cached_property
    def annotated(self) -> DecoratorBinding:
        return DecoratorBinding.named("typing", "Annotated", self.tree, self.path, self.scope)

    def compared(self, field: str) -> list[ast.expr]:
        """The field's name, its type without typing.Annotated metadata, and its default."""
        statement = self.fields[field]
        match statement.annotation:
            case ast.Subscript(value=value, slice=ast.Tuple(elts=[declared, *_])) if self.annotated.marks(value):
                annotation: ast.expr = declared
            case annotation:
                pass
        return [statement.target, annotation, *([statement.value] if statement.value else [])]

    def declared(self, field: str) -> NominalProof:
        """The field's type and default, each reference resolved to its owner in this class's
        scope; the name is the field's own and needs no owner."""
        _, *written = self.compared(field)
        return DecoratorBinding.identity(self.tree, self.path, self.scope, ast.Tuple(elts=written))

    def holding(self, held: DeclaredClass, base: DeclaredClass) -> tuple[str, ...]:
        """The fields this class newly declares whose type is the module-level binding of
        ``held``."""
        binding = DecoratorBinding.named(
            ".".join(DecoratorBinding.module_of(held.path)), held.node.name, self.tree, self.path,
            self.scope)
        return tuple(name for name, statement in self.fields.items()
                     if name not in base.fields and binding.marks(statement.annotation))


@dataclass(frozen=True)
class Move:
    """One declaration proved moved unchanged: its parts in the class it left, paired in walk
    order with its parts in the class it reached."""

    left: DeclaredClass
    gave: tuple[ast.AST, ...]
    reached: DeclaredClass
    took: tuple[ast.AST, ...]


class ProvedMove(Family, ABC, root=True, affix="Move"):
    """A way the base and head declarations prove occurrences moved rather than written. Each
    member finds its moves; the family pays each moved occurrence from one departure of the
    occurrence it was, once, so a retained copy, an extra occurrence or a second copy is still
    written."""

    @classmethod
    @abstractmethod
    def moves(cls, sources: list[ChangedSource], left: list[ChangedSource]) -> Iterator[Move]:
        """This member's proved moves across the changed modules."""

    @classmethod
    def credit(cls, repo: Project, measure: type[CleanChangedLines], sources: list[ChangedSource],
               left: list[ChangedSource], departed: Counter[tuple[str, str]]) -> Counter[ast.AST]:
        """Each proved destination occurrence's credit; ``departed`` spends what it pays."""
        credited: Counter[ast.AST] = Counter()
        for member in cls.members():
            for move in member.moves(sources, left):
                cls.transfer(repo, measure, move, departed, credited)
        return credited

    @staticmethod
    def transfer(repo: Project, measure: type[CleanChangedLines], move: Move,
                 departed: Counter[tuple[str, str]], credited: Counter[ast.AST]) -> None:
        """One move's occurrences, paid one for one by those that left the old declaration.
        The parts are proved the same, so they pair node for node in walk order; parts whose
        raw shapes differ pair nothing."""
        scope = move.left.scope
        gone = {id(node) for node in measure.sites(repo, move.left.tree, move.left.path)}
        came = {id(node) for node in measure.sites(repo, move.reached.tree, move.reached.path)}
        scopes = measure.scopes(move.left.tree)
        for gave, took in zip(move.gave, move.took, strict=True):
            before, after = list(ast.walk(gave)), list(ast.walk(took))
            if [type(node) for node in before] != [type(node) for node in after]:
                continue
            for old, new in zip(before, after, strict=True):
                if ((carried := min(measure.occurrences(old), measure.occurrences(new)))
                        and id(old) in gone and id(new) in came):
                    identity = scopes[old], ast.unparse(old)
                    if identity[0] == scope or identity[0].startswith(f"{scope}."):
                        paid = min(carried, departed[identity])
                        departed[identity] -= paid
                        credited[new] += paid


class FieldExtraction(ProvedMove):
    """Fields a class gave up to the one new class it now holds in a typed field.

    The held field's annotation is the provenance edge: an old class present at both
    revisions newly declares one field typed as one class absent from the base, and no other
    old class holds that class. Each field the old class lost, declared once in the new class
    under the same name with the same type (typing.Annotated metadata aside) and the same
    default, carries its occurrences there.
    """

    @classmethod
    def moves(cls, sources: list[ChangedSource], left: list[ChangedSource]) -> Iterator[Move]:
        existing = {name for source in [*sources, *left] for name in DeclaredClass.top(
            source.path, source.base_tree)}
        new = [held for source in sources
               for name, held in DeclaredClass.top(source.path, source.tree).items()
               if name not in existing]
        edges: list[tuple[DeclaredClass, DeclaredClass, DeclaredClass]] = []
        for source in sources:
            before = DeclaredClass.top(source.path, source.base_tree)
            for name, owner in DeclaredClass.top(source.path, source.tree).items():
                if name in before:
                    for held in new:
                        edges.extend((before[name], owner, held) for _ in owner.holding(held, before[name]))
        owners = Counter((base.path, base.node.name) for base, _, _ in edges)
        holders = Counter((held.path, held.node.name) for _, _, held in edges)
        for base, owner, held in edges:
            if owners[base.path, base.node.name] == holders[held.path, held.node.name] == 1:
                for field in sorted(base.fields):
                    if (not owner.binds(field) and field in held.fields
                            and base.declared(field).same(held.declared(field))):
                        yield Move(base, tuple(base.compared(field)), held, tuple(held.compared(field)))


class MemberRelocation(ProvedMove):
    """A method a class gave up to its direct parent, unchanged.

    The parent is the provenance edge: at both revisions one direct base of the class resolves
    to the parent's own declaration, the class no longer binds the method at the head, the
    parent binds it once at the head and not at all at the base, and the definition is the
    same, decorators, signature and body. Every name the definition reads from outside itself
    resolves to the same owner from the parent's scope as it did from the class's. A method
    that calls super, reads __class__ or spells a private name the compiler mangles with its
    class's name means something else in another class and moves nothing.
    """

    @classmethod
    def moves(cls, sources: list[ChangedSource], left: list[ChangedSource]) -> Iterator[Move]:
        kept = [(before[name], after[name]) for source in sources
                for before, after in [(DeclaredClass.top(source.path, source.base_tree),
                                       DeclaredClass.top(source.path, source.tree))]
                for name in sorted(before.keys() & after.keys())]
        for child, child_head in kept:
            for parent_base, parent in kept:
                if child.derives(parent_base) and child_head.derives(parent):
                    for name, old in child.methods.items():
                        if (name in parent.methods and not child_head.binds(name)
                                and not parent_base.binds(name)
                                and ast.dump(old) == ast.dump(new := parent.methods[name])
                                and cls.portable(old)
                                and cls.owners(child, old).same(cls.owners(parent, new))):
                            yield Move(child, (old,), parent, (new,))

    @staticmethod
    def portable(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Whether the definition means the same in any class: no super, no __class__, no
        private name the compiler would mangle with the enclosing class's name."""
        for node in ast.walk(function):
            match node:
                case ast.Name(id="super" | "__class__"):
                    return False
                case ast.Constant():
                    continue
            for _, value in ast.iter_fields(node):
                if isinstance(value, str) and value.startswith("__") and not value.endswith("__"):
                    return False
        return True

    @staticmethod
    def owners(declared: DeclaredClass, function: ast.FunctionDef | ast.AsyncFunctionDef) -> NominalProof:
        """What the definition's outside names refer to, read from its class's scope: the names
        its decorators, defaults and annotations read, and every global its body reads, each
        resolved to its owning module and name. A global declaration, a name the class body
        binds or one the module's declarations cannot resolve proves nothing."""
        table = symtable.symtable(ast.unparse(function), declared.path, "exec")
        names = {symbol.get_name() for symbol in table.get_symbols()
                 if symbol.get_name() != function.name}
        for child in MemberRelocation.nested(table):
            for symbol in child.get_symbols():
                if symbol.is_declared_global():
                    return UnprovedIdentity()
                if symbol.is_global():
                    names.add(symbol.get_name())
        return DecoratorBinding.identity(declared.tree, declared.path, declared.scope,
                                         ast.Tuple(elts=[ast.Name(id=name) for name in sorted(names)]))

    @staticmethod
    def nested(table: symtable.SymbolTable) -> Iterator[symtable.SymbolTable]:
        """Every scope inside the table, the definition's own first."""
        for child in table.get_children():
            yield child
            yield from MemberRelocation.nested(child)


class CleanChangedLines(OccurrenceMeasure):
    """No occurrence may be written, even when another occurrence is deleted.

    An occurrence is identified by its enclosing scope and its source text, so rewriting
    the rest of a line around an unchanged occurrence writes nothing new, while a different
    occurrence elsewhere never pays for a new one. Scopes are class and function names, not
    modules, so an occurrence that leaves one changed module and arrives in another, as a
    class moved between files, is the same occurrence moved, not a new one written.
    """

    @classmethod
    def introduced(
        cls, repo: Project, base: str, head: str, paths: tuple[str, ...],
        departed_from: tuple[str, ...],
    ) -> dict[FileCount, list[int]]:
        """Each strict measure's sites on each path's changed lines. Occurrences that left the
        changed modules in ``departed_from`` (those present at the base) pay for the same
        occurrences arriving elsewhere, and for those a proved move carried."""
        sources = [ChangedSource.read(repo, base, head, path) for path in paths]
        left = [ChangedSource.departed(repo, base, head, path) for path in departed_from]
        found: dict[FileCount, list[int]] = {}
        for measure in Compared.members_with(cls):
            departed: Counter[tuple[str, str]] = Counter()
            for source in left:
                gone = measure.identities(repo, source.base_tree, source.path)
                gone.subtract(measure.identities(repo, source.tree, source.path))
                departed.update(+gone)
            extracted = ProvedMove.credit(repo, measure, sources, left, departed)
            for source in sources:
                found[measure.at(source.path)] = measure.changed_sites(repo, source, departed, extracted)
        return found

    @staticmethod
    def scopes(tree: ast.AST) -> dict[ast.AST, str]:
        """Each node's enclosing class and function names, dotted; the module is empty."""
        return ScopeNames.of(tree).found

    @classmethod
    def identities(cls, repo: Project, tree: ast.AST, filename: str) -> Counter[tuple[str, str]]:
        """Every occurrence in the module, keyed by its scope and source text."""
        scopes = cls.scopes(tree)
        found: Counter[tuple[str, str]] = Counter()
        for node in cls.sites(repo, tree, filename):
            if count := cls.occurrences(node):
                found[scopes[node], ast.unparse(node)] += count
        return found

    @classmethod
    def blocks(cls, delta: Counts) -> bool:
        """Never on raw growth: Comparison.failed fails every introduced occurrence on its own,
        and a moved or extracted occurrence grows its destination's raw count while writing
        nothing. A new occurrence still fails through ``introduced`` even when an unrelated
        deletion offsets the totals."""
        return False

    @classmethod
    def missing(cls, head: Counts, introduced: dict[FileCount, list[int]]) -> list[MeasureCount]:
        """A file with occurrences at the head but no introduced-site report is unproved: only
        an explicit report, even an empty one, says no occurrence there is new."""
        return [key for key, value in head.items()
                if cls.owns(key) and value > 0 and key not in introduced]

    @classmethod
    def changed_sites(cls, repo: Project, source: ChangedSource, departed: Counter[tuple[str, str]],
                      extracted: Counter[ast.AST]) -> list[int]:
        """The first changed line of each occurrence written there that the base lacks and no
        changed module gave up; a moved occurrence spends the departure it came from, and an
        occurrence a proved move carried spends its credit."""
        scopes = cls.scopes(source.tree)
        unwritten = cls.identities(repo, source.tree, source.path)
        unwritten.subtract(cls.identities(repo, source.base_tree, source.path))
        sites = []
        for node in cls.sites(repo, source.tree, source.path):
            if ((count := cls.occurrences(node))
                    and (touched := source.lines.intersection(cls.position.lines(node)))):
                identity = scopes[node], ast.unparse(node)
                arrived = max(0, min(count, unwritten[identity]))
                unwritten[identity] -= arrived
                moved = min(arrived, departed[identity])
                departed[identity] -= moved
                carried = min(arrived - moved, extracted[node])
                extracted[node] -= carried
                sites.extend([min(touched)] * (arrived - moved - carried))
        return sites


@dataclass(frozen=True)
class Comparison:
    root: str
    base_revision: str
    head_revision: str
    paths: tuple[str, ...]
    base: dict[MeasureCount, int]
    head: dict[MeasureCount, int]
    introduced: dict[FileCount, list[int]]
    found: dict[MeasureCount, list[Site]]

    @property
    def delta(self) -> dict[MeasureCount, int]:
        return {name: value - self.base[name] for name, value in self.head.items()}

    @staticmethod
    def keyed[Count: MeasureCount, Value](counts: dict[Count, Value]) -> dict[str, Value]:
        """The report's JSON keys: the one place a count's record becomes text."""
        return {identity.text: value for identity, value in counts.items()}

    def report(self) -> dict[str, object]:
        """The JSON report: the compared revisions and paths, then each count keyed by its text."""
        return {
            "root": self.root, "base_revision": self.base_revision,
            "head_revision": self.head_revision, "paths": list(self.paths),
            "base": self.keyed(self.base), "head": self.keyed(self.head),
            "introduced": self.keyed(self.introduced),
            "found": {name: [dataclasses.asdict(site) for site in sites]
                      for name, sites in self.keyed(self.found).items()},
            "delta": self.keyed(self.delta),
            "missing": sorted(key.text for key in self.missing),
        }

    @property
    def missing(self) -> list[MeasureCount]:
        """Counts whose measure's required evidence the comparison lacks."""
        return [key for measure in Measure.members_with(Compared)
                for key in measure.missing(self.head, self.introduced)]

    @property
    def failed(self) -> bool:
        return (any(measure.blocks(self.delta) for measure in Measure.members_with(Compared))
                or any(self.introduced.values()) or bool(self.missing))


def compare(repo: Project, base: str, head: str, root: str, workers: int,
            measured: tuple[type[Compared], ...]) -> Comparison:
    source_root = Path(root)
    if source_root.is_absolute() or ".." in source_root.parts or not source_root.parts:
        raise ValueError("--root must be a repository-relative source directory")
    root = source_root.as_posix().rstrip("/") + "/"
    base, head = repo.revision(base), repo.revision(head)
    changed = ChangedPaths.of(repo, base, head)
    touched = changed.touched
    paths = set(touched & (repo.python_paths(base, root) | repo.python_paths(head, root)))
    before: Counts = {}
    after: Counts = {}
    introduced: dict[FileCount, list[int]] = {}
    # Every measure reads production Python paths only, so with none touched each would
    # report no change.
    if not touched & (repo.production_paths(base) | repo.production_paths(head)):
        return Comparison(root, base, head, (), before, after, introduced, {})

    # A pass of a shared runner is measured once, by that runner.
    measures = tuple(dict.fromkeys(measure.run_by() for measure in measured))
    # Forked workers start from the already-imported measures instead of importing them again.
    with repo.pool(workers) as pool:
        parts = {measure: [pool.submit(part)
                           for part in measure.parts(repo, base, head, paths, root)]
                 for measure in measures}
        sites = [pool.submit(
            CleanChangedLines.introduced, repo, base, head,
            tuple(sorted(paths & repo.python_paths(head, root))),
            tuple(sorted(paths & repo.python_paths(base, root))),
        )]
        # Each measure combines as soon as its parts finish, while slower checkers still run.
        combined = {}
        results: dict[type[Runner], list] = {measure: [] for measure in measures}
        owners = {future: measure for measure, futures in parts.items() for future in futures}
        for measure in measures:
            if not parts[measure]:
                combined[measure] = measure.combine([], repo, base, head, paths, root)
        for future in as_completed(owners):
            measure = owners[future]
            if measure not in combined and all(part.done() for part in parts[measure]):
                results[measure] = [part.result() for part in parts[measure]]
                combined[measure] = measure.combine(results[measure], repo, base, head, paths, root)
        # A shared runner measures all its passes; the comparison holds only the asked ones.
        for measure in measures:
            for side, taken in zip((before, after), combined[measure], strict=True):
                side.update({key: value for key, value in taken.items()
                             if any(asked.owns(key) for asked in measured)})
        for path_sites in sites:
            introduced.update({identity: sorted(found)
                               for identity, found in path_sites.result().items()})
    comparison = Comparison(root, base, head, tuple(sorted(paths)),
                            MeasureCount.paired(before, changed),
                            MeasureCount.paired(after, changed), introduced, {})
    # A measure whose results hold its findings reports them for each grown count.
    found = {key: sites for measure in measures
             for key, sites in measure.grown_sites(results[measure], comparison.delta,
                                                   changed).items()
             if any(asked.owns(key) for asked in measured)}
    # Only a grown measure is read for its sites, so a clean change pays for none.
    grown = [measure for measure in LocatedSource.members_with(LocatedSource)
             if measure in measured and measure.grown(comparison.delta)]
    if grown:
        sources = repo.sources(head, paths & repo.python_paths(head, root))
        found |= {key: sites for measure in grown
                  for key, sites in measure.located(repo, sources).items()
                  if comparison.delta[key] > 0}
    return dataclasses.replace(comparison, found=found)


def ratchet(repo: Project, base: str, head: str, root: str, workers: int) -> int:
    """Compare every declared measure, print the JSON report on stdout and what grew on
    stderr, and answer the exit status: 1 when the change grows a blocking measure."""
    repo.prune()
    result = compare(repo, base, head, root, workers, Measure.members_with(Compared))
    # Flushed first: stdout is block-buffered into a pipe, and the growth lines must end the log.
    print(json.dumps(result.report(), indent=2), flush=True)
    # The failing step's last lines become check annotations (.github/run-step.sh merges
    # stderr), so what grew goes to stderr last; stdout stays the one JSON report.
    for key, grown in sorted(result.delta.items(), key=lambda item: item[0].text):
        if grown > 0:
            print(key.measure.grew(key, grown), file=sys.stderr)
    for name, sites in sorted(Comparison.keyed(result.found).items()):
        print(f"found {name} at {' '.join(site.text() for site in sites)}", file=sys.stderr)
    for identity, lines in sorted(Comparison.keyed(result.introduced).items()):
        if lines:
            print(f"introduced {identity} at lines {lines}", file=sys.stderr)
    for identity in sorted(key.text for key in result.missing):
        print(f"missing introduced-site report for {identity}", file=sys.stderr)
    return int(result.failed)


def main(project: Project, arguments: list[str] | None = None) -> int:
    """The ratchet's command line, over the project an entry declares."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Repository-relative production source directory")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--workers", type=int, default=processors(),
                        help="Pool width; a caller running ratchets in parallel narrows it")
    args = parser.parse_args(arguments)
    return ratchet(project, args.base, args.head, args.root, args.workers)
