"""Measures of structural debt, each owning how it recognizes its pattern.

Every measure declares the AST node types it inspects; dispatch is built from those
declarations, so adding a measure is one class. Counts are keyed by measure class.
Parsing uses the running interpreter: run with the project's Python, because a file the
parser cannot read is reported as Unparsed and contributes nothing.
"""
from __future__ import annotations

import ast
import collections
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cache
from typing import ClassVar

from .family import Family
from .handler_declarations import BuiltinHandlerDeclarations


class Headline:
    """Capability: the three measures behind the headline density."""


class Measure(Family, ABC, root=True):
    node_types: ClassVar[tuple[type[ast.AST], ...]]
    weight: ClassVar[int] = 0          # contribution to a file's ranking score

    @classmethod
    @abstractmethod
    def count(cls, node: ast.AST) -> int: ...

    @classmethod
    def admits(cls, path: str) -> bool:
        return True


class PatternMeasure(Measure, abstract=True):
    """A measure that counts one per matching node."""

    @classmethod
    def count(cls, node: ast.AST) -> int:
        return int(cls.matches(node))

    @classmethod
    @abstractmethod
    def matches(cls, node: ast.AST) -> bool: ...


class TypeIdentityCheck(PatternMeasure, Headline):
    weight = 3
    node_types = (ast.Compare,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.Compare(left=ast.Call(func=ast.Name(id="type"))):
                return True
        return False


class LongBooleanChain(PatternMeasure, Headline):
    weight = 5
    node_types = (ast.BoolOp,)
    length: ClassVar[int] = 4

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.BoolOp(values=values):
                return len(values) >= cls.length
        return False


class BooleanChainTerms(Measure):
    """Terms in chains of four or more: an eleven-term chain weighs eleven, not one (IMPL-14)."""
    node_types = (ast.BoolOp,)
    weight = 1

    @classmethod
    def count(cls, node: ast.AST) -> int:
        match node:
            case ast.BoolOp(values=values) if len(values) >= LongBooleanChain.length:
                return len(values)
        return 0


class CodecSubclass(PatternMeasure):
    """Classes adapting a codec for one type (TIME-9); such a type should declare its own wire form."""
    node_types = (ast.ClassDef,)
    weight = 3

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.ClassDef(bases=bases):
                return any(_base_name(base).endswith("Codec") for base in bases)
        return False


class ForeignAbsenceProbe(Measure):
    """`other.attr is None` and `not other.attr`: code outside an object probing its absent state
    (IDEN-3). The owner should report that state itself; each probe is its state, restated."""
    node_types = (ast.Compare, ast.UnaryOp)
    weight = 1

    @classmethod
    def count(cls, node: ast.AST) -> int:
        match node:
            case ast.Compare(left=ast.Attribute(value=owner), ops=[ast.Is() | ast.IsNot()], comparators=[ast.Constant(value=None)]):
                return int(not _is_self(owner))
            case ast.UnaryOp(op=ast.Not(), operand=ast.Attribute(value=owner)):
                return int(not _is_self(owner))
        return 0


def _is_self(node: ast.expr) -> bool:
    match node:
        case ast.Name(id="self"):
            return True
    return False


def _own_nodes(function: ast.AST):
    """The function's own nodes: nested functions, lambdas and classes are measured on their own."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.Lambda() | ast.ClassDef():
                continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _literal(node: ast.AST) -> bool:
    match node:
        case ast.Constant(value=str() | int() | float()) if not isinstance(node.value, bool):
            return True
    return False


class DispatchMeasure(Measure, abstract=True):
    """Per function: subjects decided case by case outside the family that owns the cases."""
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    weight = 5
    arms: ClassVar[int] = 3

    @classmethod
    def case_groups(cls, node: ast.AST) -> tuple[frozenset[str], ...]:
        cases: dict[str, set[str]] = collections.defaultdict(set)
        for inner in _own_nodes(node):
            for subject, case in cls.cases(inner):
                cases[subject].add(case)
        return tuple(frozenset(found) for found in cases.values() if len(found) >= cls.arms)

    @classmethod
    def count(cls, node: ast.AST) -> int:
        return len(cls.case_groups(node))

    @classmethod
    @abstractmethod
    def cases(cls, node: ast.AST): ...


class StringDispatch(DispatchMeasure):
    """One subject compared against three or more literals (IMPL-1): a family's cases, spelled outside it."""

    @classmethod
    def cases(cls, node: ast.AST):
        match node:
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                for op, right in zip(ops, comparators):
                    match op, right:
                        case (ast.Eq() | ast.NotEq(), literal) if _literal(literal):
                            yield ast.unparse(left), repr(literal.value)
                        case (ast.In() | ast.NotIn(), ast.Set(elts=items) | ast.Tuple(elts=items) | ast.List(elts=items)):
                            yield from ((ast.unparse(left), repr(item.value)) for item in items if _literal(item))
            case ast.Match(subject=subject, cases=match_cases):
                for case in match_cases:
                    match case.pattern:
                        case ast.MatchValue(value=literal) if _literal(literal):
                            yield ast.unparse(subject), repr(literal.value)


class TypeSwitch(DispatchMeasure):
    """Three or more type checks on one subject (IMPL-3): behaviour chosen outside the classes it belongs to."""

    @classmethod
    def cases(cls, node: ast.AST):
        match node:
            case ast.Call(func=ast.Name(id="isinstance"), args=[subject, kind]):
                yield ast.unparse(subject), ast.unparse(kind)
            case ast.Compare(left=ast.Call(func=ast.Name(id="type"), args=[subject]), comparators=[kind]):
                yield ast.unparse(subject), ast.unparse(kind)
            case ast.Match(subject=subject, cases=match_cases):
                for case in match_cases:
                    match case.pattern:
                        case ast.MatchClass(cls=kind):
                            yield ast.unparse(subject), ast.unparse(kind)


class DispatchArmCount:
    """Capability: count distinct arms, so growing an existing candidate is visible too.

    The same three-arm detection threshold applies. This is a heuristic, not a proof
    of domain ownership or a complete no-new-case guard.
    """
    weight = 1

    @classmethod
    def count(cls, node: ast.AST) -> int:
        return sum(len(group) for group in cls.case_groups(node))


class StringDispatchArms(DispatchArmCount, StringDispatch):
    """Distinct literal arms in the string-dispatch candidates."""


class TypeSwitchArms(DispatchArmCount, TypeSwitch):
    """Distinct type arms in the type-switch candidates."""


class BuiltinHandlerTypeSwitch(BuiltinHandlerDeclarations, TypeSwitch):
    """Primitive MroDispatch arms outside the codec, even one per method.

    Splitting a primitive switch into decorated methods is counted arm by arm.
    AST/domain handlers are a different taxonomy. See the shared collector's
    screening scope; this count does not prove native execution ownership.
    """
    node_types = (ast.Module,)

    @classmethod
    def count(cls, node: ast.AST) -> int:
        return cls.count_module(node)


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


class StringEquality(Measure):
    weight = 1
    node_types = (ast.Compare,)

    @classmethod
    def count(cls, node: ast.AST) -> int:
        match node:
            case ast.Compare(ops=ops, comparators=comparators):
                return sum(1 for op, cmp in zip(ops, comparators)
                           if isinstance(op, (ast.Eq, ast.NotEq)) and _is_string_constant(cmp))
        return 0


class NoneIdentity(Measure):
    weight = 1
    node_types = (ast.Compare,)

    @classmethod
    def count(cls, node: ast.AST) -> int:
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
    for measure in Measure.members():
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
        return {measure.family_name: self.counts[measure] for measure in Measure.members()} | {"code_lines": self.code_lines}


@dataclass(frozen=True)
class ParseOutcome(ABC):
    path: str
    code_lines: int


@dataclass(frozen=True)
class Measured(ParseOutcome):
    tally: Tally


@dataclass(frozen=True)
class Unparsed(ParseOutcome):
    error: str


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
                tally.counts[measure] += measure.count(node)
    return Measured(path, lines, tally)


def interpreter() -> str:
    return sys.version.split()[0]
