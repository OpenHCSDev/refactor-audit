"""Literal strings standing for an identity the code declares, and type tests against the
code's own classes: both bind by spelling where a typed reference would bind by declaration."""
from __future__ import annotations

import ast
import enum
import importlib
import importlib.resources
import importlib.util
import itertools
import re
import sys
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from metaclass_registry.mro_dispatch import MroDispatch, handles

from .dispatch import TypeTests
from .project import Project
from .ratchet import Counts, InventoryMeasure, PerFileMeasure
from .repository import ChangedPaths


class IdentityReading:
    """How one literal string stands for an identity instead of a typed reference."""


class NamedDeclaration(IdentityReading):
    """The literal spells a class, module or function this repository declares."""


class ComparedKind(IdentityReading):
    """The literal is one arm of a kind decided by comparing spellings."""


class NamedAccess(IdentityReading):
    """The literal names the attribute, module or family member a call reaches by name."""


@dataclass(frozen=True)
class IdentitySite:
    line: int
    spelling: str
    reading: type[IdentityReading]
    target: str


@dataclass(frozen=True)
class ModuleDeclarations:
    """One module's top-level multi-word function names and its classes with their base names."""

    functions: tuple[str, ...]
    classes: tuple[tuple[str, tuple[str, ...]], ...]


class DeclaredPositions(MroDispatch):
    """Where a literal is used as a declaration's name, each position declared by its node."""

    def __init__(self) -> None:
        self.found: list[ast.expr] = []
        self.excluded: list[ast.expr] = []

    @staticmethod
    def called(node: ast.expr) -> str:
        return ast.unparse(node).rpartition(".")[2]

    @classmethod
    def literals(cls, tree: ast.AST) -> Iterator[ast.AST]:
        """The nodes inside a position, so a name spelled there names its declaration."""
        positions = cls()
        for node in ast.walk(tree):
            positions.dispatch_sync(node)
        excluded = {id(part) for expression in positions.excluded for part in ast.walk(expression)}
        return (part for expression in positions.found for part in ast.walk(expression)
                if id(part) not in excluded)


class TypePositions(DeclaredPositions):
    """The expressions where a type is expected, so a one-word class name there names it."""

    type_variable: typing.ClassVar[str] = typing.cast(type, typing.TypeVar).__name__
    annotated: typing.ClassVar[str] = typing.cast(type, Annotated).__name__

    @handles(ast.FunctionDef, ast.AsyncFunctionDef)
    def signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = node.args
        self.found.extend(filter(None, (node.returns, *(argument.annotation for argument in (
            *arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs,
            *filter(None, (arguments.vararg, arguments.kwarg)))))))

    @handles(ast.AnnAssign)
    def annotation(self, node: ast.AnnAssign) -> None:
        self.found.append(node.annotation)

    @handles(ast.ClassDef)
    def bases(self, node: ast.ClassDef) -> None:
        self.found.extend(node.bases)

    @handles(ast.Call)
    def type_variable_bound(self, node: ast.Call) -> None:
        if self.called(node.func) == self.type_variable:
            self.found.extend((*node.args[1:], *(keyword.value for keyword in node.keywords)))

    @handles(ast.Subscript)
    def annotated_metadata(self, node: ast.Subscript) -> None:
        """Annotated[T, ...] declares T; what follows it is metadata, not a type."""
        if self.called(node.value) == self.annotated:
            self.excluded.extend(typing.cast(ast.Tuple, node.slice).elts[1:])


class ModulePositions(DeclaredPositions):
    """Where a string selects a module: an importer's argument, a key of the module table or
    the module an interpreter runs. Anywhere else a module's name is a word, a table or a path
    that happens to share it."""

    importers: typing.ClassVar[frozenset[str]] = frozenset(
        call.__name__ for call in (importlib.import_module, __import__, importlib.util.find_spec,
                                   importlib.resources.files))
    # CPython's command-line option for running a module by name: another program's contract.
    run_module: typing.ClassVar[str] = ast.dump(ast.Constant("-m"))
    module_table: typing.ClassVar[str] = ".".join((sys.__name__, next(
        name for name, value in vars(sys).items() if value is sys.modules)))

    @handles(ast.Call)
    def imported(self, node: ast.Call) -> None:
        if self.called(node.func) in self.importers:
            self.found.extend(node.args[:1])

    @handles(ast.Call)
    def table_method(self, node: ast.Call) -> None:
        if ast.unparse(node.func).rpartition(".")[0] == self.module_table:
            self.found.extend(node.args[:1])

    @handles(ast.Subscript)
    def table_entry(self, node: ast.Subscript) -> None:
        if ast.unparse(node.value) == self.module_table:
            self.found.append(node.slice)

    @handles(ast.Compare)
    def table_membership(self, node: ast.Compare) -> None:
        if self.module_table in map(ast.unparse, node.comparators):
            self.found.append(node.left)

    @handles(ast.Tuple, ast.List)
    def interpreter_module(self, node: ast.Tuple | ast.List) -> None:
        self.found.extend(argument for option, argument in itertools.pairwise(node.elts)
                          if ast.dump(option) == self.run_module)


@dataclass(frozen=True)
class DeclaredSpellings:
    """Every spelling a revision's production Python declares as a name.

    Classes count under their own name, src modules under their dotted path, and modules and
    top-level functions under a multi-word name; a one-word module or function name is also an
    ordinary word, so a literal equal to it is not taken as naming it. A one-word class name
    (Read, Open, Claimed) is an ordinary word too, so a literal equal to it names the class
    only where a type is expected: an annotation, a return, a class base or a type variable's
    bound. A module's name names it only where a string selects a module: an importer's
    argument or a key of ``sys.modules``; elsewhere it is a word, a table or a path that shares
    the spelling. Enum members are the declared boundary spellings of another program's
    contract, so literals in an enum's body are the one place such a spelling may appear.
    """

    names: dict[str, str]
    enums: frozenset[str]
    unmistakable: dict[str, str]
    """The names a literal names wherever it appears: all but one-word classes and modules."""
    modules: dict[str, str]

    boundary_enums: typing.ClassVar[frozenset[str]] = frozenset(
        kind.__name__ for kind in (enum.Enum, enum.StrEnum, enum.IntEnum, enum.Flag, enum.IntFlag))
    named_calls: typing.ClassVar[frozenset[str]] = frozenset(
        call.__name__ for call in (getattr, setattr, hasattr, delattr, __import__,
                                   importlib.import_module))
    kind_spelling: typing.ClassVar[re.Pattern[str]] = re.compile(r"[A-Za-z_][\w.-]*")
    class_word: typing.ClassVar[re.Pattern[str]] = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+")
    package_marker: typing.ClassVar[str] = Path(importlib.__file__).name
    entry_guard: typing.ClassVar[str] = ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body)

    @staticmethod
    def multiword(name: str) -> bool:
        return name.strip("_").count("_") > 0

    @classmethod
    def one_word_class(cls, name: str) -> bool:
        return len(cls.class_word.findall(name)) == 1

    @staticmethod
    def dotted_name(module: Path, packages: set[Path]) -> str:
        """The import name of a module inside a package, from the package directories above it."""
        top = module.parent
        if top not in packages:
            return ""
        while top.parent in packages:
            top = top.parent
        parts = module.relative_to(top.parent).parts
        return ".".join(parts[:-1] if module.name == Path(DeclaredSpellings.package_marker).stem else parts)

    @classmethod
    def import_names(cls, paths: typing.Collection[str]) -> dict[str, str]:
        """Each path's dotted import name inside a package; empty outside every package."""
        packages = {Path(path).parent for path in paths if Path(path).name == cls.package_marker}
        return {path: cls.dotted_name(Path(path).with_suffix(""), packages) for path in paths}

    @classmethod
    def declarations(cls, repo: Project, path: str, source: bytes) -> ModuleDeclarations:
        tree = ast.parse(source, path)
        functions = (node.name for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        classes = (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
        return ModuleDeclarations(
            tuple(filter(cls.multiword, functions)),
            tuple((node.name, tuple(base.id if isinstance(base, ast.Name) else base.attr
                                    for base in node.bases
                                    if isinstance(base, (ast.Name, ast.Attribute))))
                  for node in classes))

    @classmethod
    def read(cls, modules: dict[str, ModuleDeclarations]) -> DeclaredSpellings:
        """The spellings declared by the modules, taken in the dictionary's order."""
        names: dict[str, str] = {}
        bases: dict[str, set[str]] = {}
        dotted_names = cls.import_names(modules)
        for path, declared in modules.items():
            module = Path(path).with_suffix("")
            if cls.multiword(module.stem):
                names[module.stem] = f"module {path}"
            if dotted := dotted_names[path]:
                names[dotted] = f"module {path}"
            for function in declared.functions:
                names.setdefault(function, f"function {path}:{function}")
            for name, parents in declared.classes:
                names[name] = f"class {path}:{name}"
                bases.setdefault(name, set()).update(parents)
        enums = set(cls.boundary_enums)
        while grown := {name for name, parents in bases.items() if parents & enums} - enums:
            enums |= grown
        ordinary = set(filter(cls.one_word_class, (
            name for name, target in names.items() if target.startswith("class "))))
        functions = {function for declared in modules.values() for function in declared.functions}
        selected = {name: target for name, target in names.items()
                    if target.startswith("module ") and name not in functions}
        positioned = ordinary | selected.keys()
        return cls(names, frozenset(enums),
                   {name: target for name, target in names.items() if name not in positioned},
                   selected)

    def exempt(self, tree: ast.AST) -> set[int]:
        """Literals no reader takes as identity: prose, interpolation pieces, class-header
        options, enum boundary declarations and the interpreter's entry-point guard."""
        exempt: set[int] = set()
        for node in ast.walk(tree):
            match node:
                case ast.Module(body=[ast.Expr(value=ast.Constant() as prose), *_]) | ast.ClassDef(
                        body=[ast.Expr(value=ast.Constant() as prose), *_]) | ast.FunctionDef(
                        body=[ast.Expr(value=ast.Constant() as prose), *_]) | ast.AsyncFunctionDef(
                        body=[ast.Expr(value=ast.Constant() as prose), *_]):
                    exempt.add(id(prose))
            match node:
                case ast.JoinedStr():
                    exempt.update(map(id, ast.walk(node)))
                case ast.ClassDef(name=name) if name in self.enums:
                    exempt.update(map(id, ast.walk(node)))
                case ast.ClassDef(keywords=keywords):
                    exempt.update(id(part) for keyword in keywords for part in ast.walk(keyword))
                case ast.Compare() if ast.dump(node) == self.entry_guard:
                    exempt.update(map(id, ast.walk(node)))
        return exempt

    def sites(self, repo: Project, source: bytes, filename: str) -> list[IdentitySite]:
        tree = ast.parse(source, filename)
        exempt = self.exempt(tree)
        found: dict[int, IdentitySite] = {}
        selecting_calls = repo.selecting_calls()

        def record(literal: ast.AST, reading: type[IdentityReading], target: str) -> None:
            if (isinstance(literal, ast.Constant) and isinstance(literal.value, str)
                    and id(literal) not in exempt):
                found.setdefault(id(literal), IdentitySite(literal.lineno, literal.value, reading, target))

        for node in ast.walk(tree):
            match node:
                case ast.Call(func=ast.Name(id=call) | ast.Attribute(attr=call), args=arguments):
                    if call in self.named_calls:
                        position = int(call not in {__import__.__name__, importlib.import_module.__name__})
                        for argument in arguments[position:position + 1]:
                            record(argument, NamedAccess, f"{call} by name")
                    if call in selecting_calls:
                        for argument in arguments:
                            record(argument, NamedAccess, f"family member selected by {call}")
                case ast.Compare(left=left, ops=operators, comparators=comparators):
                    for operator, right in zip(operators, comparators, strict=True):
                        match operator, right:
                            case ast.Eq() | ast.NotEq(), _:
                                for side in (left, right):
                                    if self.kind(side):
                                        record(side, ComparedKind, "kind compared by spelling")
                            case ast.In() | ast.NotIn(), ast.Set(elts=items) | ast.Tuple(
                                    elts=items) | ast.List(elts=items):
                                for item in filter(self.kind, items):
                                    record(item, ComparedKind, "kind compared by spelling")
                        left = right
                case ast.MatchValue(value=value) if self.kind(value):
                    record(value, ComparedKind, "kind matched by spelling")
        for spellings, nodes in ((self.unmistakable, ast.walk(tree)),
                                 (self.names, TypePositions.literals(tree)),
                                 (self.modules, ModulePositions.literals(tree))):
            for node in nodes:
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value in spellings):
                    record(node, NamedDeclaration, spellings[node.value])
        return sorted(found.values(), key=lambda site: site.line)

    def kind(self, node: ast.AST) -> bool:
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and self.kind_spelling.fullmatch(node.value) is not None)


class StringIdentity(InventoryMeasure[ModuleDeclarations]):
    """Literal strings standing for something the code declares, per production file.

    A class, module or function named by its spelling, a kind decided by comparing spellings,
    and an attribute, module or family member reached by a literal name all bind by text, so a
    rename or a second writer of the same spelling breaks them silently. The spellings and the
    measured files are every production Python path, not only --root: the target of a spelling
    and its use can sit in any tree, as tools/cutover's step predecessors did.
    """

    @classmethod
    def inventory(cls, repo: Project, ref: str, root: str) -> frozenset[str]:
        return repo.production_paths(ref)

    reading = DeclaredSpellings.declarations

    @classmethod
    def measured(cls, repo: Project, ref: str, base: str, head: str,
                 readings: dict[str, ModuleDeclarations], root: str) -> Counts:
        spellings = DeclaredSpellings.read(readings)
        selected = readings.keys() & ChangedPaths.of(repo, base, head).touched
        return {cls.at(path): len(spellings.sites(repo, source, path))
                for path, source in repo.sources(ref, selected)}

    align = PerFileMeasure.align


class OwnTypeTests(TypeTests):
    """The classes one module tests a value against, each reported by the final name it spells."""

    def __init__(self, foreign: frozenset[str]) -> None:
        self.foreign = foreign
        self.found: list[tuple[int, str]] = []

    def tested(self, subject: ast.expr, kind: ast.expr, at: ast.expr | ast.pattern) -> None:
        for spelled in kind.elts if isinstance(kind, ast.Tuple) else (kind,):
            match spelled:
                case ast.Name(id=name) | ast.Attribute(attr=name) if (
                        ast.unparse(spelled).partition(".")[0] not in self.foreign):
                    self.found.append((at.lineno, name))

    @staticmethod
    def foreign_bindings(tree: ast.Module, ours: frozenset[str]) -> frozenset[str]:
        """Names this module binds from another program's modules: ``ast`` in ``ast.Import``."""
        bound: set[str] = set()
        for node in ast.walk(tree):
            match node:
                case ast.Import(names=names):
                    bound.update((alias.asname or alias.name).partition(".")[0] for alias in names
                                 if alias.name.partition(".")[0] not in ours)
                case ast.ImportFrom(module=module, level=0, names=names) if (
                        (module or "").partition(".")[0] not in ours):
                    bound.update(alias.asname or alias.name for alias in names)
        return frozenset(bound)

    @classmethod
    def of(cls, source: bytes, path: str, ours: frozenset[str]) -> list[tuple[int, str]]:
        tree = ast.parse(source, path)
        tests = cls(cls.foreign_bindings(tree, ours))
        for node in ast.walk(tree):
            tests.dispatch_sync(node)
        return tests.found


class OwnTypeTest(InventoryMeasure[ModuleDeclarations]):
    """Type tests against a class this repository declares, per changed file under --root.

    A test of our own class re-derives at the call site what the class's method already
    answers, so every one counts, a single arm included: the fix is a method on the base and an
    override in the member. Foreign values are dispatched once by MroDispatch at the decoder,
    which tests their foreign types, never ours. The declared classes are every production
    file's, since a tested class and its test can sit in different trees.
    """

    @classmethod
    def inventory(cls, repo: Project, ref: str, root: str) -> frozenset[str]:
        return repo.production_paths(ref)

    reading = DeclaredSpellings.declarations

    @classmethod
    def measured(cls, repo: Project, ref: str, base: str, head: str,
                 readings: dict[str, ModuleDeclarations], root: str) -> Counts:
        declared = {name for module in readings.values() for name, _ in module.classes}
        # A script outside every package, such as one in tools, is imported by its stem.
        ours = frozenset((dotted or Path(path).stem).partition(".")[0]
                         for path, dotted in DeclaredSpellings.import_names(readings).items())
        selected = {path for path in readings.keys() & ChangedPaths.of(repo, base, head).touched
                    if path.startswith(root)}
        return {cls.at(path): sum(name in declared for _, name in
                                              OwnTypeTests.of(source, path, ours))
                for path, source in repo.sources(ref, selected)}

    align = PerFileMeasure.align
