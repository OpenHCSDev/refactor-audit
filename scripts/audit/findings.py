"""The left-rung overlay: debt that family-based detectors (NRA) cannot see.

Each finding class owns its detection, its identity (for comparing against a baseline such
as upstream), and its rendering. Findings shown in detail declare the ``Listed`` capability.
"""
from __future__ import annotations

import ast
import collections
import re
import tomllib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from functools import cached_property
from pathlib import Path
from typing import ClassVar, Iterator, Self

from .chain_terms import ChainProfile
from .family import Family
from .repository import GitError, Repository


# ---------------------------------------------------------------- parsed package

@dataclass(frozen=True)
class ParsedModule:
    path: str
    text: str
    tree: ast.Module
    name: str

    @property
    def package(self) -> str:
        return self.name if self.path.endswith("__init__.py") else self.name.rpartition(".")[0]


@dataclass
class FunctionFacts(ast.NodeVisitor):
    """What one function compares, reads by key, and type-checks. Collected once per function."""
    qualname: str
    size: int
    dispatch: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    keys: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    type_checks: collections.Counter = field(default_factory=collections.Counter)
    literal_sets: list[tuple[str, ...]] = field(default_factory=list)
    constructed: set[str] = field(default_factory=set)

    def visit_Compare(self, node: ast.Compare) -> None:
        subject = ast.unparse(node.left)[:40]
        for op, comparator in zip(node.ops, node.comparators):
            match op, comparator:
                case ast.Eq() | ast.NotEq(), ast.Constant(value=str() as value):
                    self.dispatch[subject].add(value)
                case ast.In() | ast.NotIn(), ast.Set(elts=elements) | ast.Tuple(elts=elements) | ast.List(elts=elements):
                    values = _string_literals(elements)
                    if values:
                        self.dispatch[subject].update(values)
                        self.literal_sets.append(values)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        subject = ast.unparse(node.subject)[:40]
        for case in node.cases:
            match case.pattern:
                case ast.MatchValue(value=ast.Constant(value=str() as value)):
                    self.dispatch[subject].add(value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        match node:
            case ast.Subscript(value=container, slice=ast.Constant(value=str() as key)):
                self.keys[ast.unparse(container)[:30]].add(key)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        match node.func:
            case ast.Name(id=callee) | ast.Attribute(attr=callee):
                self.constructed.add(callee)
        match node:
            case ast.Call(func=ast.Attribute(value=container, attr="get"), args=[ast.Constant(value=str() as key), *_]):
                self.keys[ast.unparse(container)[:30]].add(key)
            case ast.Call(func=ast.Name(id="isinstance"), args=[subject, *_]):
                self.type_checks[ast.unparse(subject)[:30]] += 1
        self.generic_visit(node)


def _string_literals(elements: list[ast.expr]) -> tuple[str, ...]:
    values = []
    for element in elements:
        match element:
            case ast.Constant(value=str() as value):
                values.append(value)
            case _:
                return ()
    return tuple(sorted(values))


def _entry_points(pyproject: str) -> frozenset[str]:
    match tomllib.loads(pyproject):
        case {"project": {"scripts": dict(scripts)}}:
            return frozenset(target.partition(":")[0] for target in scripts.values())
    return frozenset()


@dataclass(frozen=True)
class Package:
    repo: Repository
    rev: str
    root: str
    modules: tuple[ParsedModule, ...]
    unparsed: tuple[str, ...]

    @classmethod
    def load(cls, repo: Repository, rev: str, root: str) -> "Package":
        parent = Path(root).parent
        modules, unparsed = [], []
        for path in repo.python_files(rev, root):
            text = repo.source(rev, path)
            relative = Path(path).relative_to(parent).with_suffix("")
            name = ".".join(relative.parts[:-1] if relative.name == "__init__" else relative.parts)
            try:
                modules.append(ParsedModule(path, text, ast.parse(text), name))
            except SyntaxError:
                unparsed.append(path)
        return cls(repo, rev, root, tuple(modules), tuple(unparsed))

    @cached_property
    def functions(self) -> dict[str, tuple[FunctionFacts, ...]]:
        facts: dict[str, tuple[FunctionFacts, ...]] = {}
        for module in self.modules:
            collected = []
            for node in ast.walk(module.tree):
                match node:
                    case ast.FunctionDef(name=name, lineno=start, end_lineno=end) | \
                         ast.AsyncFunctionDef(name=name, lineno=start, end_lineno=end):
                        visitor = FunctionFacts(f"{module.path}::{name}", end - start + 1)
                        visitor.generic_visit(node)
                        collected.append(visitor)
            facts[module.path] = tuple(collected)
        return facts

    @cached_property
    def class_fields(self) -> dict[str, frozenset[str]]:
        index = {}
        for module in self.modules:
            for node in ast.walk(module.tree):
                match node:
                    case ast.ClassDef(name=name, body=body):
                        names = frozenset(ast.unparse(item.target) for item in body if isinstance(item, ast.AnnAssign))
                        if len(names) >= 2:
                            index[f"{module.path}::{name}"] = names
        return index

    @cached_property
    def sizes(self) -> dict[str, int]:
        sizes = {}
        for module in self.modules:
            for node in ast.walk(module.tree):
                match node:
                    case ast.ClassDef(name=name, lineno=start, end_lineno=end) | \
                         ast.FunctionDef(name=name, lineno=start, end_lineno=end) | \
                         ast.AsyncFunctionDef(name=name, lineno=start, end_lineno=end):
                        sizes[f"{module.path}::{name}"] = end - start + 1
        return sizes

    @cached_property
    def imported(self) -> frozenset[str]:
        names = set()
        for module in self.modules:
            for node in ast.walk(module.tree):
                match node:
                    case ast.Import(names=aliases):
                        names.update(alias.name for alias in aliases)
                    case ast.ImportFrom(module=target, level=level, names=aliases):
                        base = _absolute(module.package, target, level)
                        names.add(base)
                        names.update(f"{base}.{alias.name}" for alias in aliases)
        return frozenset(names)

    @cached_property
    def entry_points(self) -> frozenset[str]:
        try:
            return _entry_points(self.repo.source(self.rev, "pyproject.toml"))
        except GitError:                        # no pyproject.toml at this revision
            return frozenset()

    @cached_property
    def all_text(self) -> str:
        return "\n".join(module.text for module in self.modules)


def _absolute(package: str, target: str | None, level: int) -> str:
    if not level:
        return target or ""
    parts = package.split(".")
    parts = parts[: len(parts) - (level - 1)] if level > 1 else parts
    return ".".join(parts + ([target] if target else []))


# ---------------------------------------------------------------- origins against a baseline

@dataclass(frozen=True)
class Baseline:
    identities: frozenset[tuple]
    sizes: dict[str, int]


class Origin(ABC):
    @abstractmethod
    def tag(self) -> str: ...


class Untagged(Origin):
    def tag(self) -> str:
        return ""


class Inherited(Origin):
    def tag(self) -> str:
        return "up   "


class Introduced(Origin):
    def tag(self) -> str:
        return "new  "


@dataclass(frozen=True)
class Grown(Origin):
    before: int

    def tag(self) -> str:
        return f"(base {self.before:>5}) "


class NewObject(Origin):
    def tag(self) -> str:
        return "(new)        "


# ---------------------------------------------------------------- findings

class Listed:
    """Capability: shown in detail, not only counted."""


class Finding(Family, ABC, root=True):
    title: ClassVar[str]

    @classmethod
    @abstractmethod
    def detect(cls, package: Package) -> Iterator[Self]: ...

    @abstractmethod
    def identity(self) -> tuple: ...

    def rank(self) -> int:
        return 0

    def line(self) -> str:
        return ""

    def origin(self, baseline: Baseline) -> Origin:
        return Inherited() if self.identity() in baseline.identities else Introduced()

    @classmethod
    def summary_note(cls, findings: list[Self]) -> str:
        return ""


class PerFunctionFinding(Finding, abstract=True):
    threshold: ClassVar[int] = 3

    @classmethod
    def detect(cls, package: Package) -> Iterator[Self]:
        for facts in package.functions.values():
            for fact in facts:
                yield from cls.from_facts(fact, package)

    @classmethod
    @abstractmethod
    def from_facts(cls, facts: FunctionFacts, package: Package) -> Iterator[Self]: ...


@dataclass(frozen=True)
class StringDispatch(PerFunctionFinding, Listed):
    title = "STRING DISPATCH: one subject compared against several string literals"
    function: str
    subject: str
    values: tuple[str, ...]

    @classmethod
    def from_facts(cls, facts: FunctionFacts, package: Package) -> Iterator[Self]:
        for subject, values in facts.dispatch.items():
            if len(values) >= cls.threshold:
                yield cls(facts.qualname, subject, tuple(sorted(values)))

    def identity(self) -> tuple:
        return (self.family_name, self.function, self.subject)

    def rank(self) -> int:
        return len(self.values)

    def line(self) -> str:
        return f"{len(self.values):>3}  {self.function}  on {self.subject}  {list(self.values[:8])}"


@dataclass(frozen=True)
class IsinstanceSwitch(PerFunctionFinding, Listed):
    title = "ISINSTANCE SWITCHES: several type checks on one subject"
    function: str
    subject: str
    checks: int

    @classmethod
    def from_facts(cls, facts: FunctionFacts, package: Package) -> Iterator[Self]:
        for subject, checks in facts.type_checks.items():
            if checks >= cls.threshold:
                yield cls(facts.qualname, subject, checks)

    def identity(self) -> tuple:
        return (self.family_name, self.function, self.subject)

    def rank(self) -> int:
        return self.checks

    def line(self) -> str:
        return f"{self.checks:>3}  {self.function}  on {self.subject}"


class ShapeModel(ABC):
    bypasses: ClassVar[bool]

    @abstractmethod
    def describe(self) -> str: ...


@dataclass(frozen=True)
class ModeledBy(ShapeModel):
    model: str
    bypasses = True

    def describe(self) -> str:
        return f"MATCHES {self.model}: a bypass, unless the data is copied into it elsewhere"


@dataclass(frozen=True)
class HandMapped(ShapeModel):
    """The function builds this class from the keys it reads: a second copy of the shape, not a bypass."""
    model: str
    bypasses = False

    def describe(self) -> str:
        return f"HAND-MAPPED into {self.model} (check whether that class decodes this data or presents it)"


@dataclass(frozen=True)
class Unmodeled(ShapeModel):
    bypasses = False

    def describe(self) -> str:
        return "unmodeled"


@dataclass(frozen=True)
class RawShape(PerFunctionFinding, Listed):
    title = "RAW SHAPES: one subject read by several string keys"
    external: ClassVar[re.Pattern] = re.compile(r"environ|\benv\b")
    function: str
    subject: str
    keys: tuple[str, ...]
    model: ShapeModel

    @classmethod
    def from_facts(cls, facts: FunctionFacts, package: Package) -> Iterator[Self]:
        for subject, keys in facts.keys.items():
            if len(keys) >= cls.threshold and not cls.external.search(subject):
                yield cls(facts.qualname, subject, tuple(sorted(keys)), cls.model_for(frozenset(keys), facts, package))

    @staticmethod
    def model_for(keys: frozenset[str], facts: FunctionFacts, package: Package) -> ShapeModel:
        fields = package.class_fields
        best = max(fields, key=lambda name: len(fields[name] & keys), default=None)
        if best is None or len(fields[best] & keys) < min(3, 0.6 * len(keys)):
            return Unmodeled()
        if best.rpartition("::")[2] in facts.constructed:
            return HandMapped(best)
        return ModeledBy(best)

    def identity(self) -> tuple:
        return (self.family_name, self.function, self.subject)

    def rank(self) -> int:
        return len(self.keys)

    def line(self) -> str:
        return f"{len(self.keys):>3}  {self.function}  {self.subject}  {list(self.keys[:6])}  -> {self.model.describe()}"

    @classmethod
    def summary_note(cls, findings: list[Self]) -> str:
        hand_mapped = sum(1 for f in findings if isinstance(f.model, HandMapped))
        return f"(bypassing an existing class: {sum(1 for f in findings if f.model.bypasses)}; hand-mapped into one: {hand_mapped})"


class PerNodeFinding(Finding, abstract=True):
    @classmethod
    def detect(cls, package: Package) -> Iterator[Self]:
        for module in package.modules:
            for node in ast.walk(module.tree):
                yield from cls.from_node(node, module)

    @classmethod
    @abstractmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]: ...


@dataclass(frozen=True)
class ExactKeySet(PerNodeFinding):
    title = "EXACT KEY SETS: hand-written set(x) == {...} validation"
    location: str

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        match node:
            case ast.Compare(left=ast.Call(func=ast.Name(id="set")), comparators=[ast.Set(), *_], lineno=line):
                yield cls(f"{module.path}:{line}")

    def identity(self) -> tuple:
        return (self.family_name, self.location)


@dataclass(frozen=True)
class NamedAttribute(PerNodeFinding):
    title = "ACCESS BY ATTRIBUTE NAME"
    location: str
    name: str

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        match node:
            case ast.Call(func=ast.Name(id="getattr" | "hasattr"), args=[_, ast.Constant(value=str() as name), *_], lineno=line):
                yield cls(f"{module.path}:{line}", name)

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.name)

    @classmethod
    def summary_note(cls, findings: list[Self]) -> str:
        common = collections.Counter(f.name for f in findings).most_common(6)
        return f"(most probed: {', '.join(f'{name} x{n}' for name, n in common)})" if common else ""


@dataclass(frozen=True)
class LongCondition(PerNodeFinding, Listed):
    title = "LONG CONDITIONS: syntactic ownership leads, verify before choosing a target"
    terms_threshold: ClassVar[int] = 6
    location: str
    terms: int
    profile: ChainProfile
    text: str

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        match node:
            case ast.BoolOp(values=values, lineno=line) if len(values) >= cls.terms_threshold:
                yield cls(f"{module.path}:{line}", len(values), ChainProfile.of(node), " ".join(ast.unparse(node).split())[:90])

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.text)

    def rank(self) -> int:
        return self.terms

    def line(self) -> str:
        return f"{self.terms:>3} terms  {self.location}  [{self.profile.render()}] -> lead: {self.profile.lead}"

    @classmethod
    def summary_note(cls, findings: list[Self]) -> str:
        dominant = collections.Counter(f.profile.lead for f in findings)
        return "(leads by dominant term: " + ", ".join(f"{pattern} {n}" for pattern, n in dominant.most_common()) + ")" if findings else ""


@dataclass(frozen=True)
class GodObject(PerNodeFinding, Listed, abstract=True):
    qualname: str
    size: int
    threshold: ClassVar[int]
    node_types: ClassVar[tuple[type[ast.AST], ...]]

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        if isinstance(node, cls.node_types):
            size = node.end_lineno - node.lineno + 1
            if size > cls.threshold:
                yield cls(f"{module.path}::{node.name}", size)

    def identity(self) -> tuple:
        return (self.family_name, self.qualname)

    def rank(self) -> int:
        return self.size

    def line(self) -> str:
        return f"{self.size:>5}  {self.qualname}"

    def origin(self, baseline: Baseline) -> Origin:
        before = baseline.sizes.get(self.qualname)
        return NewObject() if before is None else Grown(before)


@dataclass(frozen=True)
class GodClass(GodObject):
    title = "GOD CLASSES (over 500 lines)"
    threshold = 500
    node_types = (ast.ClassDef,)


@dataclass(frozen=True)
class GodFunction(GodObject):
    title = "GOD FUNCTIONS (over 100 lines)"
    threshold = 100
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)


class PerLineFinding(Finding, abstract=True):
    pattern: ClassVar[re.Pattern]

    @classmethod
    def detect(cls, package: Package) -> Iterator[Self]:
        for module in package.modules:
            for number, text in enumerate(module.text.splitlines(), 1):
                if cls.pattern.search(text):
                    yield cls(f"{module.path}:{number}", text.strip()[:100])


@dataclass(frozen=True)
class LegacyMarker(PerLineFinding):
    title = "LEGACY MARKERS"
    pattern = re.compile(r"legacy|compat\w*|deprecat\w*|backward|fallback|shim", re.I)
    location: str
    text: str

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.text)


@dataclass(frozen=True)
class PositionalInsert(PerLineFinding):
    title = "POSITIONAL INSERTS (column order implied)"
    pattern = re.compile(r"INSERT(?:\s+OR\s+\w+)?\s+INTO\s+\w+\s+VALUES", re.I)
    location: str
    text: str

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.text)


@dataclass(frozen=True)
class ColumnInsert(PerLineFinding):
    title = "INSERTS WITH COLUMN LISTS"
    pattern = re.compile(r"INSERT(?:\s+OR\s+\w+)?\s+INTO\s+\w+\s*\(", re.I)
    location: str
    text: str

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.text)


@dataclass(frozen=True)
class EmbeddedScript(PerNodeFinding):
    title = "JAVASCRIPT EMBEDDED IN STRINGS"
    javascript: ClassVar[re.Pattern] = re.compile(r"\bconst\s+\w+\s*=|\bawait import\(|\brequire\(|JSON\.stringify|process\.stdout\.write")
    location: str
    length: int

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        match node:
            case ast.Constant(value=str() as text, lineno=line) if len(text) > 200 and cls.javascript.search(text):
                yield cls(f"{module.path}:{line}", len(text))

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0])


@dataclass(frozen=True)
class ChildProcessSite(PerNodeFinding):
    title = "CHILD PROCESS SITES (spawn and kill)"
    calls: ClassVar[re.Pattern] = re.compile(r"create_subprocess_exec|subprocess\.(Popen|run)|os\.kill|killpg")
    location: str
    call: str

    @classmethod
    def from_node(cls, node: ast.AST, module: ParsedModule) -> Iterator[Self]:
        match node:
            case ast.Call(func=function, lineno=line) if cls.calls.search(ast.unparse(function)):
                yield cls(f"{module.path}:{line}", ast.unparse(function))

    def identity(self) -> tuple:
        return (self.family_name, self.location.rpartition(":")[0], self.call)


@dataclass(frozen=True)
class LiteralRoster(Finding, Listed):
    title = "LITERAL ROSTERS: one set of string literals written in several places"
    values: tuple[str, ...]
    copies: int

    @classmethod
    def detect(cls, package: Package) -> Iterator[Self]:
        written = collections.Counter(values for facts in package.functions.values()
                                      for fact in facts for values in fact.literal_sets)
        for values, copies in written.items():
            if copies >= 2:
                yield cls(values, copies)

    def identity(self) -> tuple:
        return (self.family_name, self.values)

    def rank(self) -> int:
        return self.copies

    def line(self) -> str:
        return f"{self.copies:>3}x  {list(self.values)}"


@dataclass(frozen=True)
class DeadModule(Finding, Listed):
    title = "DEAD MODULES: imported by nothing in the package"
    path: str
    lines: int
    named_in_strings: bool

    @classmethod
    def detect(cls, package: Package) -> Iterator[Self]:
        for module in package.modules:
            if module.path.endswith(("__init__.py", "__main__.py")) or module.name in package.imported \
                    or module.name in package.entry_points:
                continue
            short = module.name.rpartition(".")[2]
            mention = re.compile(rf"['\"]{re.escape(module.name)}['\"]|-m\s+{re.escape(module.name)}|['\"]{re.escape(short)}['\"]")
            yield cls(module.path, module.text.count("\n"), bool(mention.search(package.all_text)))

    def identity(self) -> tuple:
        return (self.family_name, self.path)

    def line(self) -> str:
        note = "  named in a string: check it is not launched by name" if self.named_in_strings else ""
        return f"{self.lines:>5}  {self.path}{note}"


# ---------------------------------------------------------------- the overlay

@dataclass(frozen=True)
class Overlay:
    package: Package
    findings: dict[type[Finding], list[Finding]]

    @classmethod
    def run(cls, package: Package) -> "Overlay":
        return cls(package, {kind: list(kind.detect(package)) for kind in Finding.members()})

    def baseline(self) -> Baseline:
        return Baseline(frozenset(f.identity() for found in self.findings.values() for f in found), self.package.sizes)

    def render(self, top: int, baseline: Baseline | None) -> str:
        lines = [f"WARNING: {len(self.package.unparsed)} files did not parse; re-run with the project's Python."] \
            if self.package.unparsed else []
        lines.append("SUMMARY")
        for kind, found in self.findings.items():
            lines.append(f"  {kind.family_name:<22}{len(found):>5}  {kind.summary_note(found)}")
        for kind in Finding.members_with(Listed):
            ranked = sorted(self.findings[kind], key=lambda f: -f.rank())[:top]
            if ranked:
                lines += ["", kind.title]
                lines += [f"  {(f.origin(baseline) if baseline else Untagged()).tag()}{f.line()}" for f in ranked]
        return "\n".join(lines)

    def record(self) -> dict[str, list[dict]]:
        return {kind.family_name: [asdict(f) for f in found] for kind, found in self.findings.items()}
