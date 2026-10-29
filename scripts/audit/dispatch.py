"""Dispatch outside the family that owns the cases: a subject decided by comparing literals,
by testing types, or by primitive handler arms."""
from __future__ import annotations

import ast
import typing
from abc import abstractmethod
from collections.abc import Iterator

from metaclass_registry.mro_dispatch import MroDispatch, handles

from .handler_declarations import BuiltinHandlerDeclarations
from .measures import NodeCount
from .project import Project
from .ratchet import PerFileMeasure, PerFileOccurrenceMeasure


class MatchArms:
    """The patterns each arm of a ``match`` tests: ``case A() | B() as x:`` tests two."""

    @classmethod
    def alternatives(cls, pattern: ast.pattern) -> Iterator[ast.pattern]:
        match pattern:
            case ast.MatchOr(patterns=patterns):
                for alternative in patterns:
                    yield from cls.alternatives(alternative)
            case ast.MatchAs(pattern=ast.pattern() as inner):
                yield from cls.alternatives(inner)
            case _:
                yield pattern

    @classmethod
    def arms(cls, node: ast.Match) -> Iterator[ast.pattern]:
        for case in node.cases:
            yield from cls.alternatives(case.pattern)


class DispatchCases(MroDispatch):
    """Collect cases of one external Python AST without executing its source."""

    minimum_arms = 3

    def __init__(self) -> None:
        self.cases: dict[str, set[str]] = {}
        self.subjects: dict[str, ast.AST] = {}

    def add(self, subject: ast.AST, case: str) -> None:
        identity = ast.dump(subject)
        self.subjects.setdefault(identity, subject)
        self.cases.setdefault(identity, set()).add(case)

    def decided(self) -> tuple[tuple[ast.AST, frozenset[str]], ...]:
        """Each subject decided case by case, with its cases, screened at the arm minimum."""
        return tuple((self.subjects[identity], frozenset(cases))
                     for identity, cases in self.cases.items() if len(cases) >= self.minimum_arms)

    def groups(self) -> tuple[frozenset[str], ...]:
        return tuple(cases for _, cases in self.decided())

    def read_function(self, function: ast.AST) -> None:
        pending = list(ast.iter_child_nodes(function))
        while pending:
            node = pending.pop()
            # A nested scope's comparisons cannot combine with its parent's.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            self.dispatch_sync(node)
            pending.extend(ast.iter_child_nodes(node))


class LiteralDispatchCases(MatchArms, DispatchCases):
    @staticmethod
    def literal(node: ast.AST) -> bool:
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (str, int, float))
                and not isinstance(node.value, bool))

    @handles(ast.Compare)
    def comparison(self, node: ast.Compare) -> None:
        left = node.left
        for operator, right in zip(node.ops, node.comparators, strict=True):
            match operator, right:
                case (ast.Eq() | ast.NotEq(), literal) if self.literal(literal):
                    self.add(left, repr(literal.value))
                case (ast.In() | ast.NotIn(), ast.Set(elts=items) | ast.Tuple(elts=items) | ast.List(elts=items)):
                    for item in items:
                        if self.literal(item):
                            self.add(left, repr(item.value))
            left = right

    @handles(ast.Match)
    def match_cases(self, node: ast.Match) -> None:
        for arm in self.arms(node):
            match arm:
                case ast.MatchValue(value=value) if self.literal(value):
                    self.add(node.subject, repr(value.value))


class TypeTests(MatchArms, MroDispatch):
    """Every test of a value's type in Python syntax: isinstance, issubclass, ``type(x) is``,
    ``x is C``, ``x in (A, B)`` and class patterns. A reader decides what a test means."""

    instance_checks: typing.ClassVar[frozenset[str]] = frozenset(
        check.__name__ for check in (isinstance, issubclass))
    exact: typing.ClassVar[str] = type.__name__

    @abstractmethod
    def tested(self, subject: ast.expr, kind: ast.expr, at: ast.expr | ast.pattern) -> None:
        """One test of ``subject`` against ``kind``, spelled at ``at``."""

    @handles(ast.Call)
    def instance_check(self, node: ast.Call) -> None:
        match node:
            case ast.Call(func=ast.Name(id=check), args=[subject, kind],
                          keywords=[]) if check in self.instance_checks:
                self.tested(subject, kind, node)

    @handles(ast.Compare)
    def comparison(self, node: ast.Compare) -> None:
        match node:
            case ast.Compare(left=ast.Call(func=ast.Name(id=called), args=[subject]),
                             comparators=[kind]) if called == self.exact:
                self.tested(subject, kind, node)
            case ast.Compare(left=subject, ops=[ast.Is()],
                             comparators=[ast.Name() | ast.Attribute() as kind]):
                self.tested(subject, kind, node)
            case ast.Compare(left=subject, ops=[ast.In()], comparators=[ast.Tuple(elts=kinds)]) if (
                    kinds and all(isinstance(kind, (ast.Name, ast.Attribute)) for kind in kinds)):
                for kind in kinds:
                    self.tested(subject, kind, node)

    @handles(ast.Match)
    def match_cases(self, node: ast.Match) -> None:
        for arm in self.arms(node):
            match arm:
                case ast.MatchClass(cls=kind):
                    self.tested(node.subject, kind, arm)


class TypeDispatchCases(TypeTests, DispatchCases):
    def tested(self, subject: ast.expr, kind: ast.expr, at: ast.expr | ast.pattern) -> None:
        self.add(subject, ast.dump(kind))


class DispatchMeasure(PerFileOccurrenceMeasure, NodeCount):
    """Per function: subjects decided case by case outside the family that owns the cases,
    screened at three arms, with per-file growth isolation.

    These are candidates, not a proof that an external taxonomy is ours. Review
    ownership at each reported site; no exception roster or second registry.
    """

    weight = 5
    node_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    @classmethod
    @abstractmethod
    def collector(cls) -> DispatchCases: ...

    @classmethod
    def decided(cls, node: ast.AST) -> tuple[tuple[ast.AST, frozenset[str]], ...]:
        """One function's subjects decided case by case; the overlay lists these too."""
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ()
        collector = cls.collector()
        collector.read_function(node)
        return collector.decided()

    @classmethod
    def groups(cls, node: ast.AST) -> tuple[frozenset[str], ...]:
        return tuple(cases for _, cases in cls.decided(node))

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        return len(cls.groups(node))


class StringDispatch(DispatchMeasure):
    """One subject compared against three or more literals (IMPL-1): a family's cases, spelled
    outside it."""

    @classmethod
    def collector(cls) -> DispatchCases:
        return LiteralDispatchCases()


class TypeSwitch(DispatchMeasure):
    """Three or more type tests on one subject (IMPL-3): behaviour chosen outside the classes it
    belongs to."""

    @classmethod
    def collector(cls) -> DispatchCases:
        return TypeDispatchCases()


class DispatchArms:
    """Capability: count distinct arms through the same collector, so growing an existing
    candidate is visible too. The same three-arm threshold applies; this is a screen, not a
    proof of domain ownership or a complete no-new-case guard."""

    weight = 1

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        return sum(len(group) for group in cls.groups(node))


class StringDispatchArms(DispatchArms, StringDispatch):
    """Distinct literal arms in the string-dispatch candidates."""


class TypeSwitchArms(DispatchArms, TypeSwitch):
    """Distinct type arms in the type-switch candidates."""


class BuiltinHandlerTypeSwitch(BuiltinHandlerDeclarations, PerFileMeasure, NodeCount):
    """Primitive MroDispatch arms outside the codec, even one per method; per-file counts
    prevent moving primitive debt.

    Splitting a primitive switch into decorated methods is counted arm by arm. AST/domain
    handlers are a different taxonomy. See the shared collector's screening scope; this count
    does not prove native execution ownership.
    """

    weight = 5
    node_types = (ast.Module,)

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        return cls.count_module(node)

    @classmethod
    def count(cls, repo: Project, source: bytes, filename: str) -> int:
        if not cls.admits(repo, filename):
            return 0
        return cls.count_module(cls.tree(source, filename))
