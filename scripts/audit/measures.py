"""Measures of structural debt, each owning how it recognizes its pattern.

Every measure is one class in the Measure family. A measure the census counts declares the
AST node types it inspects (NodeCount); dispatch is built from those declarations, so adding
a measure is one class. A measure the ratchet compares across revisions is Compared (see
ratchet.py); a measure that is both is one class with both capabilities. Counts are keyed by
measure class. Parsing uses the running interpreter: run with the project's Python, because a
file the parser cannot read is reported as Unparsed and contributes nothing.
"""
from __future__ import annotations

import ast
import collections
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import ClassVar

from .family import Family


class Headline:
    """Capability: the measures behind the headline density."""


class Measure(Family, ABC, root=True):
    """One kind of structural debt. Its capabilities say who counts it: the census (NodeCount),
    the ratchet (Compared), or both."""


class NodeCount(ABC):
    """Capability: the census counts this measure per AST node of its declared types."""

    node_types: ClassVar[tuple[type[ast.AST], ...]]
    weight: ClassVar[int] = 0          # contribution to a file's ranking score

    @classmethod
    @abstractmethod
    def occurrences(cls, node: ast.AST) -> int:
        """This node's contribution, excluding its descendants."""

    @classmethod
    def admits(cls, path: str) -> bool:
        return True


@lru_cache(maxsize=4)
def parsed(source: bytes, filename: str) -> ast.Module:
    """A file's syntax, parsed once while every pass counts it."""
    return ast.parse(source, filename)


class PatternMeasure(Measure, NodeCount, abstract=True):
    """A measure that counts one per matching node."""

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        return int(cls.matches(node))

    @classmethod
    @abstractmethod
    def matches(cls, node: ast.AST) -> bool: ...


class ForeignAbsenceSyntax:
    """Select explicit None comparisons; truth-value negation does not prove absence."""

    @staticmethod
    def attribute(node: ast.AST) -> ast.Attribute | None:
        match node:
            case ast.Compare(left=ast.Attribute() as attribute, ops=[ast.Is() | ast.IsNot()], comparators=[ast.Constant(value=None)]):
                return attribute
        return None


class ForeignNegationSyntax:
    """Select negated attributes without claiming what their truth value means."""

    @staticmethod
    def attribute(node: ast.AST) -> ast.Attribute | None:
        match node:
            case ast.UnaryOp(op=ast.Not(), operand=ast.Attribute() as attribute):
                return attribute
        return None


class ForeignAttributeProbe(NodeCount):
    @staticmethod
    @abstractmethod
    def attribute(node: ast.AST) -> ast.Attribute | None: ...

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        attribute = cls.attribute(node)
        return int(attribute is not None and not _is_self(attribute.value))



class ForeignNegation(ForeignNegationSyntax, ForeignAttributeProbe, Measure):
    """Unresolved truth-value syntax is a reading lead, not ranked absence debt."""
    node_types = (ast.UnaryOp,)


def _is_self(node: ast.expr) -> bool:
    match node:
        case ast.Name(id="self"):
            return True
    return False



class StringKeySubscript(PatternMeasure, Headline):
    weight = 1
    node_types = (ast.Subscript,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Subscript(slice=ast.Constant(value=str())):
                return True
        return False


class LiteralKeyGet(PatternMeasure):
    weight = 1
    node_types = (ast.Call,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Attribute(attr="get"), args=[ast.Constant(value=str()), *_]):
                return True
        return False


class StringEquality(Measure, NodeCount):
    weight = 1
    node_types = (ast.Compare,)

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        match node:
            case ast.Compare(ops=ops, comparators=comparators):
                return sum(1 for op, cmp in zip(ops, comparators)
                           if isinstance(op, (ast.Eq, ast.NotEq)) and _is_string_constant(cmp))
        return 0


class NoneIdentity(Measure, NodeCount):
    weight = 1
    node_types = (ast.Compare,)

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        match node:
            case ast.Compare(ops=ops, comparators=comparators):
                return sum(1 for op, cmp in zip(ops, comparators)
                           if isinstance(op, (ast.Is, ast.IsNot)) and _is_none_constant(cmp))
        return 0


class IsinstanceCall(PatternMeasure):
    weight = 1
    node_types = (ast.Call,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="isinstance")):
                return True
        return False


class AttributeByName(PatternMeasure):
    weight = 3
    node_types = (ast.Call,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="getattr" | "hasattr"), args=[_, ast.Constant(value=str()), *_]):
                return True
        return False


class GetattrDefault(PatternMeasure):
    weight = 3
    node_types = (ast.Call,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="getattr"), args=[_, ast.Constant(value=str()), _]):
                return True
        return False


class BroadExcept(PatternMeasure):
    weight = 2
    node_types = (ast.ExceptHandler,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.ExceptHandler(type=None) | ast.ExceptHandler(type=ast.Name(id="Exception" | "BaseException")):
                return True
        return False


class JsonLoads(PatternMeasure):
    node_types = (ast.Call,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Attribute(value=ast.Name(id="json"), attr="loads")):
                return True
        return False


class LongFunction(PatternMeasure):
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    lines: ClassVar[int] = 100

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.FunctionDef(lineno=start, end_lineno=end) | ast.AsyncFunctionDef(lineno=start, end_lineno=end):
                return end - start + 1 > cls.lines
        return False


class ClassDefinition(PatternMeasure):
    node_types = (ast.ClassDef,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        return True


class AbstractBase(PatternMeasure):
    node_types = (ast.ClassDef,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.ClassDef(bases=bases):
                return any(_base_name(base) in ("ABC", "Protocol") for base in bases)
        return False


class EnumClass(PatternMeasure):
    node_types = (ast.ClassDef,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.ClassDef(bases=bases):
                return any(_base_name(base).endswith(("Enum", "Flag")) for base in bases)
        return False


def _is_string_constant(node: ast.AST) -> bool:
    match node:
        case ast.Constant(value=str()):
            return True
    return False


def _is_none_constant(node: ast.AST) -> bool:
    match node:
        case ast.Constant(value=None):
            return True
    return False


def _base_name(node: ast.AST) -> str:
    match node:
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return name
    return ""


@cache
def _dispatch() -> dict[type[ast.AST], tuple[type[Measure], ...]]:
    table: dict[type[ast.AST], list[type[Measure]]] = collections.defaultdict(list)
    for measure in Measure.members_with(NodeCount):
        for node_type in measure.node_types:
            table[node_type].append(measure)
    return {node_type: tuple(measures) for node_type, measures in table.items()}


@dataclass
class Tally:
    counts: collections.Counter = field(default_factory=collections.Counter)
    code_lines: int = 0

    def __add__(self, other: "Tally") -> "Tally":
        counts = collections.Counter(self.counts)
        counts.update(other.counts)        # Counter's + would drop negative totals; changes can be negative
        return Tally(counts, self.code_lines + other.code_lines)

    def __sub__(self, other: "Tally") -> "Tally":
        counts = collections.Counter(self.counts)
        counts.subtract(other.counts)
        return Tally(counts, self.code_lines - other.code_lines)

    def __getitem__(self, measure: type[Measure]) -> int:
        return self.counts[measure]

    def density(self, measure: type[Measure]) -> float:
        return 1000 * self.counts[measure] / max(self.code_lines, 1)

    def score(self) -> int:
        return sum(measure.weight * count for measure, count in self.counts.items())

    def headline(self) -> float:
        return sum(self.density(measure) for measure in Measure.members_with(Headline))

    def as_record(self) -> dict[str, int]:
        return {measure.family_name: self.counts[measure] for measure in Measure.members_with(NodeCount)} | {"code_lines": self.code_lines}


@dataclass(frozen=True)
class ParseOutcome(ABC):
    path: str
    code_lines: int

    def measured(self) -> tuple[Measured, ...]:
        return ()

    def unparsed(self) -> tuple[Unparsed, ...]:
        return ()


@dataclass(frozen=True)
class Measured(ParseOutcome):
    tally: Tally

    def measured(self) -> tuple[Measured, ...]:
        return (self,)


@dataclass(frozen=True)
class Unparsed(ParseOutcome):
    error: str

    def unparsed(self) -> tuple[Unparsed, ...]:
        return (self,)


def code_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def measure_source(path: str, text: str) -> ParseOutcome:
    lines = code_lines(text)
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return Unparsed(path, lines, str(error))
    dispatch = _dispatch()
    tally = Tally(code_lines=lines)
    for node in ast.walk(tree):
        for measure in dispatch.get(type(node), ()):
            if measure.admits(path):
                tally.counts[measure] += measure.occurrences(node)
    return Measured(path, lines, tally)


def interpreter() -> str:
    return sys.version.split()[0]
