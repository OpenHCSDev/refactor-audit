"""Syntactic measures: each counts one written pattern in a file's syntax, never by running it."""
from __future__ import annotations

import ast
import collections
import copy
import dataclasses
import enum
import fcntl
import inspect
import tokenize
import types
import typing
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from metaclass_registry.mro_dispatch import MroDispatch, handles

from .findings import Package, ParsedModule, RawShape
from .identity import TypePositions
from .lexical import DecoratorBinding, ModuleScope
from .measures import (ForeignAbsenceSyntax, ForeignAttributeProbe, Headline, Measure,
                       MechanismOwners, NodeCount, PatternMeasure)
from .project import Project
from .ratchet import (CleanChangedLines, LocatedSource, NamePosition, OccurrenceMeasure,
                      PerFileMeasure, PerFileOccurrenceMeasure, RosterPosition)
from .repository import ChangedSource


class MetadataKeys(ast.NodeVisitor):
    """The Python AST owns dispatch for literal and opaque metadata keys."""

    def generic_visit(self, node: ast.AST) -> int:
        return 1

    def visit_Dict(self, node: ast.Dict) -> int:
        return sum(typing.cast(int, self.visit(key)) if key else 1 for key in node.keys)

    def visit_Constant(self, node: ast.Constant) -> int:
        return int(isinstance(node.value, str))


class StringFieldMetadata(PerFileOccurrenceMeasure):
    """Dataclass instructions belong to typed annotations, not metadata dictionaries.

    Imported aliases are resolved at this Python boundary. Dynamic rebinding is
    outside this syntactic guard's proof scope, as with the existing import guards.
    """

    @staticmethod
    def sites(repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        binding = DecoratorBinding.of(dataclasses.field, ModuleScope(tree, filename))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and binding.marks(node.func):
                yield node

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        descriptor: types.MemberDescriptorType = inspect.getattr_static(
            dataclasses.Field, "metadata"
        )
        metadata = descriptor.__name__
        return sum(
            typing.cast(int, MetadataKeys().visit(keyword.value))
            for keyword in typing.cast(ast.Call, node).keywords
            if keyword.arg == metadata
        )


class PlainStrIdAnnotation(PerFileOccurrenceMeasure):
    """ID-shaped fields and parameters retain their nominal kind instead of plain str."""

    @staticmethod
    def sites(repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        for node in ast.walk(tree):
            match node:
                case ast.AnnAssign(
                    target=ast.Name(id=name) | ast.Attribute(attr=name), annotation=annotation
                ) | ast.arg(arg=name, annotation=annotation):
                    if not name.endswith(("_id", "nonce", "lookup", "generation")):
                        continue
                case _:
                    continue
            while True:
                match annotation:
                    case ast.Name(id=str.__name__):
                        yield node
                        break
                    case ast.BinOp(left=inner, op=ast.BitOr(), right=ast.Constant(value=None)) \
                            | ast.BinOp(left=ast.Constant(value=None), op=ast.BitOr(), right=inner):
                        annotation = inner
                    case ast.Subscript(
                        value=ast.Name(id=TypePositions.annotated)
                              | ast.Attribute(attr=TypePositions.annotated),
                        slice=ast.Tuple(elts=[inner, *_]),
                    ):
                        annotation = inner
                    case _:
                        break

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        return 1


class OwnInstanceWrites(ast.NodeVisitor):
    """A method body's object.__setattr__ calls on its own instance; nested scopes own theirs."""

    def __init__(self, instance: str) -> None:
        self.instance = instance
        self.found: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """A nested scope's writes are its own."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """A nested scope's writes are its own."""

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """A nested scope's writes are its own."""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """A nested scope's writes are its own."""

    def visit_Call(self, node: ast.Call) -> None:
        written = [*map(ast.unparse, node.args[:1])]
        if FrozenFieldWrite.occurrences(node) and written == [self.instance]:
            self.found.append(node)
        self.generic_visit(node)


class FrozenFieldWrite(CleanChangedLines, PerFileOccurrenceMeasure):
    """object.__setattr__ writes past a frozen class; only the class's own __post_init__ may
    write its own instance, because there the instance is still being constructed."""

    construction: typing.ClassVar[str] = "__post_init__"

    @classmethod
    def sites(cls, repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        constructing = {id(write) for node in ast.walk(tree) for write in cls.constructing(node)}
        return (node for node in ast.walk(tree) if id(node) not in constructing)

    @classmethod
    def constructing(cls, node: ast.AST) -> Iterator[ast.Call]:
        match node:
            case ast.ClassDef(body=members):
                for member in members:
                    match member:
                        case ast.FunctionDef(name=FrozenFieldWrite.construction,
                                             args=ast.arguments(args=[instance, *_]), body=body):
                            writes = OwnInstanceWrites(instance.arg)
                            for statement in body:
                                writes.visit(statement)
                            yield from writes.found

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case ast.Call(func=ast.Attribute(value=ast.Name(id="object"), attr="__setattr__")):
                return 1
        return 0


class BannedVocabulary(CleanChangedLines, PerFileOccurrenceMeasure):
    words: typing.ClassVar[tuple[str, ...]] = ("admission", "admit", "custody", "receipt")

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        match node:
            case (ast.Name(id=name) | ast.Attribute(attr=name)
                  | ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name)
                  | ast.ClassDef(name=name) | ast.arg(arg=name)
                  | ast.keyword(arg=str(name))):
                return int(any(word in name.casefold() for word in cls.words))
        return 0

    position = NamePosition()

    @classmethod
    def prose_lines(cls, source: ChangedSource) -> frozenset[int]:
        docstrings: set[int] = set()
        for node in ast.walk(source.tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                match node.body:
                    case [ast.Expr(value=ast.Constant(value=str()) as value), *_]:
                        docstrings.update(range(value.lineno, value.end_lineno + 1))
        found: set[int] = set()
        for token in source.tokens:
            if token.type == tokenize.COMMENT or (
                token.type == tokenize.STRING and token.start[0] in docstrings
            ):
                for line, text in enumerate(token.string.splitlines(), token.start[0]):
                    if any(word in text.casefold() for word in cls.words):
                        found.add(line)
        return frozenset(found)

    @classmethod
    def lines(cls, repo: Project, source: bytes, filename: str) -> list[int]:
        module = ChangedSource(filename, source, frozenset(), b"")
        return sorted([*super().lines(repo, source, filename), *cls.prose_lines(module)])

    @classmethod
    def changed_sites(cls, repo: Project, source: ChangedSource, departed: Counter[tuple[str, str]],
                      extracted: Counter[ast.AST]) -> list[int]:
        prose = cls.prose_lines(source) & source.lines
        return [*super().changed_sites(repo, source, departed, extracted), *prose]


class NoneSpelling(MroDispatch):
    """One node's spellings of absence as None, never evaluated code."""

    def __init__(self) -> None:
        self.found = 0

    @staticmethod
    def absent(node: ast.AST) -> bool:
        match node:
            case ast.Constant(value=None):
                return True
        return False

    @handles(ast.Compare)
    def identity(self, node: ast.Compare) -> None:
        left = node.left
        for operator, right in zip(node.ops, node.comparators, strict=True):
            match operator:
                case ast.Is() | ast.IsNot():
                    self.found += self.absent(left) or self.absent(right)
            left = right

    @handles(ast.BinOp)
    def union(self, node: ast.BinOp) -> None:
        match node:
            case ast.BinOp(op=ast.BitOr(), left=left, right=right):
                self.found += self.absent(left) or self.absent(right)

    @handles(ast.Subscript)
    def typing_spelling(self, node: ast.Subscript) -> None:
        match node.value, node.slice:
            case ast.Name(id="Optional") | ast.Attribute(attr="Optional"), _:
                self.found += 1
            case ast.Name(id="Union") | ast.Attribute(attr="Union"), ast.Tuple(elts=members):
                self.found += any(self.absent(member) for member in members)

    @handles(ast.keyword)
    def default(self, node: ast.keyword) -> None:
        match node:
            case ast.keyword(arg="default", value=value):
                self.found += self.absent(value)


class NoneAbsence(CleanChangedLines, PerFileOccurrenceMeasure):
    """Absence spelled as None: ``is None`` tests, annotations that allow None, ``default=None``.

    Absence belongs to a declared null member that answers for itself. A boundary that meets
    None from outside, a decoder reading it or an encoder writing it, declares itself with one
    of the project's absence boundary decorators; its body is not counted.
    """

    @classmethod
    def sites(cls, repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        module = ModuleScope(tree, filename)
        markers = [DecoratorBinding.of(boundary, module) for boundary in repo.absence_boundaries()]
        pending = [tree]
        while pending:
            node = pending.pop()
            match node:
                case ast.FunctionDef(decorator_list=decorators) | ast.AsyncFunctionDef(
                        decorator_list=decorators) if any(marker.marks(decorator)
                                                          for marker in markers
                                                          for decorator in decorators):
                    continue
            yield node
            pending.extend(ast.iter_child_nodes(node))

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        spelling = NoneSpelling()
        spelling.dispatch_sync(node)
        return spelling.found


class NoneIdentity(Measure, NodeCount):
    """``is None`` and ``is not None`` tests, read by NoneSpelling's own comparison arm."""

    weight = 1
    node_types = (ast.Compare,)

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        spelling = NoneSpelling()
        match node:
            case ast.Compare():
                spelling.identity(node)
        return spelling.found


class RawRecordShapes(PerFileMeasure):
    """Reuse the overlay's raw-shape detector for every production file."""

    @classmethod
    def count(cls, repo: Project, source: bytes, filename: str) -> int:
        text = source.decode()
        module = ParsedModule(filename, text, cls.tree(source, filename), filename)
        package = Package(repo, "", filename, (module,), ())
        return sum(1 for facts in package.functions[filename]
                   for _finding in RawShape.from_facts(facts, package))


class FunctionLocalImport(PerFileMeasure, LocatedSource):
    """Imports executed inside a function, including nested local classes."""

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0
            self.found: list[int] = []

        def generic_visit(self, node: ast.AST) -> None:
            nested = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            self.depth += nested
            if self.depth and isinstance(node, (ast.Import, ast.ImportFrom)):
                self.found.append(node.lineno)
            super().generic_visit(node)
            self.depth -= nested

    @classmethod
    def lines(cls, repo: Project, source: bytes, filename: str) -> list[int]:
        collector = cls.Collector()
        collector.visit(cls.tree(source, filename))
        return collector.found


class EnumDeclaration(PerFileOccurrenceMeasure, NodeCount):
    """Classes built on the standard enum types: a family whose members cannot own their
    behaviour, so every case is decided by a switch outside it. A subclass of a project's own
    enum is counted where that enum is declared."""

    node_types = (ast.ClassDef,)
    bases: typing.ClassVar[frozenset[str]] = frozenset(
        name for name, value in vars(enum).items()
        if isinstance(value, type) and issubclass(value, enum.Enum))

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case ast.ClassDef(bases=bases):
                return int(any(ast.unparse(base).rpartition(".")[2] in EnumDeclaration.bases
                               for base in bases))
        return 0


class ForeignFieldSpellings(PerFileOccurrenceMeasure):
    """Literal identifier-spelling rosters used as loop inputs."""

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case (ast.For(iter=ast.Tuple(elts=items))
                  | ast.AsyncFor(iter=ast.Tuple(elts=items))
                  | ast.comprehension(iter=ast.Tuple(elts=items))):
                return int(len(items) > 1 and all(
                    isinstance(item, ast.Constant) and isinstance(item.value, str)
                    and item.value.isidentifier() for item in items
                ))
        return 0

    position = RosterPosition()


class BroadExceptDefault(PerFileOccurrenceMeasure):
    broad_names: typing.ClassVar[frozenset[str]] = frozenset({"Exception", "BaseException"})

    @classmethod
    def broad(cls, node: ast.AST | None, *, names: frozenset[str] | None = None) -> bool:
        names = cls.broad_names if names is None else names
        match node:
            case None:
                return True
            case ast.Name(id=name) | ast.Attribute(attr=name):
                return name in names
            case ast.Tuple(elts=items):
                return any(cls.broad(item, names=names) for item in items)
        return False

    @staticmethod
    def default(node: ast.AST | None) -> bool:
        match node:
            case None | ast.Constant(value=None | False | 0 | ""):
                return True
            case ast.List(elts=[]) | ast.Tuple(elts=[]) | ast.Set(elts=[]) | ast.Dict(keys=[]):
                return True
        return False

    @classmethod
    def returns_default(cls, node: ast.AST) -> bool:
        match node:
            case ast.Return(value=value):
                return cls.default(value)
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef() | ast.Lambda():
                return False
        return any(cls.returns_default(child) for child in ast.iter_child_nodes(node))

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        return int(isinstance(node, ast.ExceptHandler) and cls.broad(node.type)
                   and any(cls.returns_default(statement) for statement in node.body))


class BroadExcept(PatternMeasure):
    """A handler that catches every error: no type, or one of BroadExceptDefault's names."""

    weight = 2
    node_types = (ast.ExceptHandler,)

    @classmethod
    def matches(cls, node: ast.AST) -> bool:
        match node:
            case ast.ExceptHandler(type=caught):
                return BroadExceptDefault.broad(caught)
        return False


class BroadSwallow(CleanChangedLines, BroadExceptDefault):
    """A broad handler without a raise hides the failure, whatever it returns."""

    broad_names = frozenset({
        "Exception", "BaseException", "ValueError", "RuntimeError", "OSError", "TypeError",
        "KeyError", "AttributeError", "LookupError",
    })

    suppressed_names = broad_names | {"ConnectionError", "TimeoutError"}

    @classmethod
    def suppresses(cls, node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="suppress") | ast.Attribute(attr="suppress"), args=arguments):
                return any(cls.broad(argument, names=cls.suppressed_names) for argument in arguments)
        return False

    @staticmethod
    def carries(handler: ast.ExceptHandler) -> bool:
        """Keep a typed outcome or hand the original failure to a reporting owner.

        A reporting method must run unconditionally and cannot turn the error into
        a returned default. Formatting the exception for a log is not this transfer.
        """
        match handler:
            case ast.ExceptHandler(name=str(bound)):
                constructed = any(
                    isinstance(name, ast.Name) and name.id == bound
                    for child in ast.walk(handler)
                    for argument in BroadSwallow.constructor_arguments(child)
                    for name in ast.walk(argument))
                returns_value = any(BroadSwallow.returns_replacement(node)
                                    for node in ast.walk(handler))
                reported = any(
                    isinstance(argument, ast.Name) and argument.id == bound
                    for statement in handler.body
                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Attribute)
                    for argument in (*statement.value.args,
                                     *(keyword.value for keyword in statement.value.keywords)))
                return constructed or (reported and not returns_value)
        return False

    @staticmethod
    def returns_replacement(node: ast.AST) -> bool:
        """A bare return ends a notification; a returned value replaces the failure."""
        match node:
            case ast.Return(value=None | ast.Constant(value=None)):
                return False
            case ast.Return():
                return True
        return False

    @staticmethod
    def constructor_arguments(node: ast.AST) -> list[ast.expr]:
        """The arguments of a call to a capitalized name: a class constructor."""
        match node:
            case ast.Call(func=ast.Name(id=callee) | ast.Attribute(attr=callee),
                          args=args, keywords=keywords) if callee[:1].isupper():
                return [*args, *(keyword.value for keyword in keywords)]
        return []

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        match node:
            case ast.With(items=items) | ast.AsyncWith(items=items):
                return sum(cls.suppresses(item.context_expr) for item in items)
            case ast.ExceptHandler(type=error):
                return int(cls.broad(error) and not cls.carries(node) and not any(
                    isinstance(child, ast.Raise) for child in ast.walk(node)))
        return 0


class FamilyFlattened(PerFileMeasure, NodeCount):
    """Screen name transport and string redispatch, not declaration-owned identity.

    A pure property projecting its receiver's declaration name supplies identity,
    not behavior. Consumers comparing strings to choose behavior are still sites.
    Module context distinguishes those roles and admits unique literal bindings;
    dynamic/imported values remain outside this syntactic collector's proof scope.
    The census and the ratchet take one count: the module as FlatteningProjection reads it,
    plus its codec detours, in every module but the project's mechanism modules.
    """
    node_types = (ast.Module,)
    weight = 3
    names: ClassVar[frozenset[str]] = frozenset({"declared_name", "family_name"})

    @classmethod
    def _flattened(cls, node: ast.AST | None) -> int:
        match node:
            case ast.Attribute(attr=attr) if attr in cls.names:
                return 1
            case ast.BoolOp(values=values) | ast.Tuple(elts=values):
                return sum(cls._flattened(v) for v in values)
        return 0

    @classmethod
    def identity_return(cls, function: ast.FunctionDef, properties: set[str], builtins: set[str]) -> ast.Return | None:
        if len(function.decorator_list) != 1:
            return None
        decorator = function.decorator_list[0]
        if not (isinstance(decorator, ast.Name) and decorator.id in properties
                or isinstance(decorator, ast.Attribute) and decorator.attr == "property"
                and isinstance(decorator.value, ast.Name) and decorator.value.id in builtins):
            return None
        parameters = (*function.args.posonlyargs, *function.args.args)
        body = function.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        if len(parameters) != 1 or len(body) != 1 or not isinstance(body[0], ast.Return):
            return None
        value = body[0].value
        if not isinstance(value, ast.Attribute) or value.attr not in cls.names:
            return None
        owner = value.value
        while isinstance(owner, ast.Attribute):
            owner = owner.value
        return body[0] if isinstance(owner, ast.Name) and owner.id == parameters[0].arg else None

    @classmethod
    def flattened(cls, node: ast.AST) -> tuple[ast.AST, ...]:
        if not isinstance(node, ast.Module):
            return ()
        nodes = tuple(ast.walk(node))
        # Only unique, literal module declarations are admitted as string values.
        # Writes/parameters/imports in any scope conservatively keep a name open.
        writes = collections.Counter(item.id for item in nodes
                                     if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)))
        writes.update(item.arg for item in nodes if isinstance(item, ast.arg))
        writes.update(item.name for item in nodes if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        for item in nodes:
            if isinstance(item, (ast.Import, ast.ImportFrom)):
                writes.update(alias.asname or alias.name.split('.')[0] for alias in item.names)
        strings = {}
        for statement in node.body:
            match statement:
                case ast.Assign(targets=[ast.Name(id=name)], value=ast.Constant(value=str() as value)) | ast.AnnAssign(target=ast.Name(id=name), value=ast.Constant(value=str() as value)):
                    if writes[name] == 1:
                        strings[name] = value
        properties = {"property"} if not writes["property"] else set()
        builtins = set()
        for statement in node.body:
            if isinstance(statement, ast.ImportFrom) and statement.module == "builtins":
                properties.update(alias.asname or alias.name for alias in statement.names
                                  if alias.name == "property" and writes[alias.asname or alias.name] == 1)
            elif isinstance(statement, ast.Import):
                builtins.update(alias.asname or alias.name for alias in statement.names
                                if alias.name == "builtins" and writes[alias.asname or alias.name] == 1)
        identities = {returned for item in nodes
                      if isinstance(item, ast.FunctionDef)
                      and (returned := cls.identity_return(item, properties, builtins)) is not None}

        def string_value(value: ast.AST) -> str | None:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
            if isinstance(value, ast.Name) and value.id in strings:
                return strings[value.id]
            return None

        found = []
        for item in nodes:
            match item:
                case ast.Call(args=args, keywords=keywords):
                    found.extend([item] * (sum(cls._flattened(a) for a in args) + sum(cls._flattened(k.value) for k in keywords)))
                case ast.Return(value=value) if item not in identities:
                    found.extend([item] * cls._flattened(value))
                case ast.Compare(left=left, ops=operators, comparators=rights):
                    for operator, right in zip(operators, rights, strict=True):
                        left_string, right_string = string_value(left), string_value(right)
                        # Presence validation and comparisons of two known values
                        # do not choose a named behavior for a dynamic subject.
                        if (isinstance(operator, (ast.Eq, ast.NotEq))
                                and left_string != "" and right_string != ""
                                and ((left_string is None) != (right_string is None))):
                            found.append(item)
                        left = right
        return tuple(found)

    @classmethod
    def admits(cls, repo: MechanismOwners, path: str) -> bool:
        return not repo.owns_mechanism(path)

    @classmethod
    def occurrences(cls, node: ast.AST) -> int:
        # The projection rewrites the module it reads, and the census and the syntax passes
        # share one parsed tree, so it reads a copy.
        projection = FlatteningProjection()
        return len(cls.flattened(projection.visit(copy.deepcopy(node)))) + projection.round_trips

    @classmethod
    def count(cls, repo: Project, source: bytes, filename: str) -> int:
        return cls.occurrences(cls.tree(source, filename)) if cls.admits(repo, filename) else 0


class FlatteningProjection(ast.NodeTransformer):
    """A module as FamilyFlattened reads it. A declaration's own identity name read on itself
    is identity, not transport; the interpreter's entry guard names no family; and a codec's
    decode of its own encode is a detour, counted once and removed."""

    def __init__(self) -> None:
        self.owners: list[str] = []
        self.round_trips = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self.owners.append(node.name)
        try:
            return self.generic_visit(node)
        finally:
            self.owners.pop()

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if (node.attr in FamilyFlattened.names and isinstance(node.value, ast.Name)
                and node.value.id in {"cls", "self", *self.owners[-1:]}):
            return ast.copy_location(ast.Constant(value=None), node)
        return self.generic_visit(node)

    def visit_Module(self, node: ast.Module) -> ast.AST:
        # `python -m` runs a module through this guard; it names the interpreter, not a family.
        for statement in node.body:
            match statement:
                case ast.If(test=ast.Compare(left=ast.Name(id="__name__"), ops=[ast.Eq()],
                                             comparators=[ast.Constant(value="__main__")])):
                    statement.test = ast.copy_location(ast.Constant(value=None), statement.test)
        return self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> ast.AST:
        match node:
            case ast.Call(
                func=ast.Attribute(value=ast.Name(id=decoder), attr="decode"),
                args=[ast.Name(id="str"), ast.Call(
                    func=ast.Attribute(value=ast.Name(id=encoder), attr="encode"),
                    args=[_], keywords=[],
                )], keywords=[],
            ) if decoder == encoder:
                self.round_trips += 1
                return ast.copy_location(ast.Constant(value=None), node)
        return self.generic_visit(node)


class TypeIdentity(OccurrenceMeasure, NodeCount, Headline):
    """Each ``type(x) is`` or ``is not`` comparison: an identity test of a type (IMPL-2)."""

    weight = 3
    node_types = (ast.Compare,)

    @staticmethod
    def is_type_call(node: ast.AST) -> bool:
        match node:
            case ast.Call(func=ast.Name(id="type"), args=[_], keywords=[]):
                return True
            case _:
                return False

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        if not isinstance(node, ast.Compare):
            return 0
        return sum(
            isinstance(operator, (ast.Is, ast.IsNot))
            and (TypeIdentity.is_type_call(left) or TypeIdentity.is_type_call(right))
            for left, operator, right in zip(
                [node.left, *node.comparators[:-1]], node.ops, node.comparators, strict=True
            )
        )


class LongBooleanChain(OccurrenceMeasure, NodeCount, Headline):
    """A chain of four or more terms joined by one boolean operator."""

    weight = 5
    node_types = (ast.BoolOp,)
    minimum_terms: typing.ClassVar[int] = 4

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case ast.BoolOp(values=values):
                return int(len(values) >= LongBooleanChain.minimum_terms)
        return 0


class BooleanChainTerms(PerFileOccurrenceMeasure, NodeCount):
    """Terms in chains of four or more: an eleven-term chain weighs eleven, not one (IMPL-14)."""

    weight = 1
    node_types = (ast.BoolOp,)

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case ast.BoolOp(values=values) if len(values) >= LongBooleanChain.minimum_terms:
                return len(values)
        return 0


class ForeignAbsenceProbe(ForeignAbsenceSyntax, ForeignAttributeProbe, PerFileOccurrenceMeasure):
    """Explicit foreign None comparisons (IDEN-3), not boolean negation."""

    weight = 1
    node_types = (ast.Compare,)


class CodecSubclass(PerFileOccurrenceMeasure, NodeCount):
    """Classes adapting a codec for one type (TIME-9); such a type should declare its own wire
    form. A sealed codec's own guard also resolves aliases."""

    weight = 3
    node_types = (ast.ClassDef,)

    @staticmethod
    def codec_base(node: ast.AST) -> bool:
        match node:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                return name.endswith("Codec")
        return False

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        match node:
            case ast.ClassDef(bases=bases):
                return int(any(CodecSubclass.codec_base(base) for base in bases))
        return 0


class StoreLockCalls(PerFileOccurrenceMeasure):
    """Lock calls outside the store modules that own locking. A store's guard is declared on
    its family (plans/STORE-GUARDS-PLAN.md), so every lock a call site takes by hand restates
    which lock guards which write; the count only falls, file by file, to zero."""

    qualified: typing.ClassVar[frozenset[tuple[str, str]]] = frozenset(
        {(fcntl.__name__, fcntl.flock.__name__)})

    @classmethod
    def sites(cls, repo: Project, tree: ast.AST, filename: str) -> Iterator[ast.AST]:
        """The lock calls, none inside a module that owns locking."""
        if filename.endswith(repo.lock_owners()):
            return
        functions = repo.lock_functions()
        for node in ast.walk(tree):
            match node:
                case ast.Call(func=ast.Attribute(value=ast.Name(id=owner), attr=name)) if (
                        (owner, name) in cls.qualified):
                    yield node
                case ast.Call(func=ast.Attribute(attr=name)) | ast.Call(
                        func=ast.Name(id=name)) if name in functions:
                    yield node

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        return 1


class UnannotatedParameter(PerFileOccurrenceMeasure):
    """Every function parameter declares its type; self and cls name their owner."""

    @staticmethod
    def occurrences(node: ast.AST) -> int:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return 0
        arguments = node.args
        return sum(
            argument.annotation is None and argument.arg not in ("self", "cls")
            for argument in (
                *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
                *filter(None, (arguments.vararg, arguments.kwarg)),
            )
        )
