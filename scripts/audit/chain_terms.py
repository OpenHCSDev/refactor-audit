"""What each term of a boolean chain tests, and so which clean form the chain needs.

These syntactic kinds suggest ownership questions, not semantic diagnoses. Absence,
comparison, flag and literal shapes lead to catalog patterns to inspect. Calls may
already query the right owner; they are not evidence of private lifecycle flags.
Kinds are tried from the most specific to the least; verify every target in the source.
"""
from __future__ import annotations

import ast
import collections
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from .family import Family


def _operand(term: ast.expr) -> ast.expr:
    match term:
        case ast.UnaryOp(op=ast.Not(), operand=inner):
            return inner
    return term


class TermKind(Family, ABC, root=True, affix="Term"):
    priority: ClassVar[int]
    pattern: ClassVar[str | None]
    clean_form: ClassVar[str]

    @classmethod
    @abstractmethod
    def matches(cls, term: ast.expr) -> bool: ...

    @staticmethod
    def of(term: ast.expr) -> type["TermKind"]:
        target = _operand(term)
        return next(kind for kind in sorted(TermKind.members(), key=lambda kind: kind.priority) if kind.matches(target))


class TypeTerm(TermKind):
    priority = 10
    pattern = "BOUND-1"
    clean_form = "decode once at the boundary into a type; the checks disappear"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Call(func=ast.Name(id="isinstance")) | ast.Compare(left=ast.Call(func=ast.Name(id="type"))):
                return True
        return False


def _rooted_at_self(node: ast.expr) -> bool:
    match node:
        case ast.Name(id="self"):
            return True
        case ast.Attribute(value=inner) | ast.Subscript(value=inner) | ast.Call(func=ast.Attribute(value=inner)):
            return _rooted_at_self(inner)
    return False


class AbsenceTerm(TermKind):
    """`x is None`, or the truthiness of another object's attribute: absence encoding a state."""
    priority = 20
    pattern = "IDEN-3"
    clean_form = "the owner answers with one property or state; its optional and empty fields stop being probed"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Compare(ops=[ast.Is() | ast.IsNot()], comparators=[ast.Constant(value=None)]):
                return True
            case ast.Attribute() if not _rooted_at_self(term):
                return True
        return False


class FlagTerm(TermKind):
    """The truthiness of this object's own attribute: an implicit lifecycle kept as flags."""
    priority = 25
    pattern = "IMPL-10"
    clean_form = "an explicit lifecycle state that answers the question; the flags go"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Attribute() if _rooted_at_self(term):
                return True
        return False


class SameValueTerm(TermKind):
    """Two computed values compared: an identity or a captured snapshot, compared piece by piece."""
    priority = 30
    pattern = "IDEN-1"
    clean_form = "one identity or snapshot value compared once (a == b); its parts stop being compared by hand"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Compare(ops=[ast.Eq() | ast.NotEq() | ast.Is() | ast.IsNot()], left=left, comparators=[right]):
                return not isinstance(left, ast.Constant) and not isinstance(right, ast.Constant)
        return False


class PredicateTerm(TermKind):
    """A predicate call: its declaration, not its spelling, determines ownership."""
    priority = 40
    pattern = None
    clean_form = "inspect the predicate declarations; existing owner queries may already be correct"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Call():
                return True
        return False


class LiteralTerm(TermKind):
    priority = 50
    pattern = "IMPL-1"
    clean_form = "a family whose members own the case; the literals go"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        match term:
            case ast.Compare(comparators=[ast.Constant(value=str() | int() | float())]):
                return True
            case ast.Compare(ops=[ast.In() | ast.NotIn()], comparators=[ast.Set() | ast.Tuple() | ast.List()]):
                return True
        return False


class RuleTerm(TermKind):
    priority = 90
    pattern = "IMPL-14"
    clean_form = "a rule family; the failed rule names itself"

    @classmethod
    def matches(cls, term: ast.expr) -> bool:
        return True


@dataclass(frozen=True)
class ChainProfile:
    kinds: tuple[tuple[str, int], ...]

    @classmethod
    def of(cls, chain: ast.BoolOp) -> "ChainProfile":
        counts = collections.Counter(TermKind.of(term) for term in chain.values)
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].priority))
        return cls(tuple((kind.family_name, count) for kind, count in ordered))

    @property
    def dominant(self) -> type[TermKind]:
        return TermKind.decode(self.kinds[0][0])

    @property
    def lead(self) -> str:
        return self.dominant.pattern or "OPEN: inspect predicate ownership"

    def render(self) -> str:
        return ", ".join(f"{name} {count}" for name, count in self.kinds)
