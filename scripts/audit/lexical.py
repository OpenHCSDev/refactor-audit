"""The one lexical owner: each node's enclosing scope, the names each scope binds and the nodes
that declare them, and what a spelling is bound to, all read from one walk of the AST."""
from __future__ import annotations

import ast
import builtins
import copy
import typing
from dataclasses import dataclass
from pathlib import Path


class ScopeNames(ast.NodeVisitor):
    """Each node's enclosing class and function names, dotted, through the AST's own dispatch,
    with the names each node binds where it stands and the nodes that declare them: a class or
    function, an import alias, an annotated name. A star import binds ``*``."""

    def __init__(self) -> None:
        self.found: dict[ast.AST, str] = {}
        self.scope = ""
        self.binds: dict[ast.AST, tuple[str, ...]] = {}
        self.declares: set[ast.AST] = set()

    @classmethod
    def of(cls, tree: ast.AST) -> ScopeNames:
        names = cls()
        names.visit(tree)
        return names

    def bindings(self, scope: str) -> dict[str, list[ast.AST]]:
        """Every binding of each name in one lexical scope, in walk order."""
        found: dict[str, list[ast.AST]] = {}
        for node, names in self.binds.items():
            if self.found[node] == scope:
                for name in names:
                    found.setdefault(name, []).append(node)
        return found

    def generic_visit(self, node: ast.AST) -> None:
        self.found[node] = self.scope
        super().generic_visit(node)

    def visit_named(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.found[node] = outer = self.scope
        self.binds[node] = (node.name,)
        self.declares.add(node)
        self.scope = f"{outer}.{node.name}"
        super().generic_visit(node)
        self.scope = outer

    def visit_alias(self, node: ast.alias) -> None:
        self.binds[node] = (node.asname or node.name.split(".")[0],)
        self.declares.add(node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        match node.ctx:
            case ast.Store() | ast.Del():
                self.binds[node] = (node.id,)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        match node.target:
            case ast.Name():
                self.declares.add(node.target)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self.binds[node] = (node.name,) if node.name else ()
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self.binds[node] = (node.name,) if node.name else ()
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self.binds[node] = (node.name,) if node.name else ()
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self.binds[node] = (node.rest,) if node.rest else ()
        self.generic_visit(node)

    def visit_TypeVar(self, node: ast.TypeVar) -> None:
        self.binds[node] = (node.name,)
        self.generic_visit(node)

    def visit_ParamSpec(self, node: ast.ParamSpec) -> None:
        self.binds[node] = (node.name,)
        self.generic_visit(node)

    def visit_TypeVarTuple(self, node: ast.TypeVarTuple) -> None:
        self.binds[node] = (node.name,)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.binds[node] = tuple(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.binds[node] = tuple(node.names)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.visit_named(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_named(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.visit_named(node)


class NominalProof:
    """What an expression's resolved owners prove it is; only two proved identities with
    the same owners and structure are the same."""

    def same(self, other: NominalProof) -> bool:
        return False

    def matches(self, key: str) -> bool:
        return False


class UnprovedIdentity(NominalProof):
    """A reference the binding facts could not resolve: equal to nothing, itself included."""


@dataclass(frozen=True)
class ProvedIdentity(NominalProof):
    key: str

    def same(self, other: NominalProof) -> bool:
        return other.matches(self.key)

    def matches(self, key: str) -> bool:
        return key == self.key


@dataclass(frozen=True)
class DecoratorBinding:
    """The spellings one module binds to a declared name (a decorator, a field factory, a
    class), read from its module-level imports and declarations.

    A name of the same spelling declared or imported from anywhere else marks nothing, and
    neither does a spelling the module binds twice, conditionally or by star import.
    """

    names: frozenset[str]

    @classmethod
    def of(cls, decorator: typing.Callable[..., object], tree: ast.AST,
           filename: str) -> DecoratorBinding:
        return cls.named(decorator.__module__, decorator.__name__, tree, filename)

    @staticmethod
    def module_of(filename: str) -> tuple[str, ...]:
        """The dotted module a source path imports as; ``src`` is the import root."""
        parts = Path(filename).with_suffix("").parts
        return parts[1:] if parts[:1] == ("src",) else parts

    @classmethod
    def named(cls, owner: str, name: str, tree: ast.AST, filename: str,
              scope: str = "") -> DecoratorBinding:
        """The spellings binding ``owner.name`` in the module at ``filename``, as read from
        ``scope``: a spelling whose root that scope rebinds names something else there."""
        module = cls.module_of(filename)
        package = module[:-1]
        names: set[str] = set()
        body = typing.cast(ast.Module, tree).body
        for bound, (declaration, statement) in cls.declarations(tree, "", body).items():
            match statement, declaration:
                case ast.ClassDef() | ast.FunctionDef() | ast.AsyncFunctionDef(), _ if (
                        ".".join(module) == owner and bound == name):
                    names.add(name)
                case ast.Import(), ast.alias(name=imported, asname=alias) if imported == owner:
                    names.add(f"{alias or imported}.{name}")
                case ast.ImportFrom(module=source, level=level), ast.alias(name=imported):
                    base = package[:len(package) - level + 1] if level else ()
                    resolved = ".".join((*base, source) if source else base)
                    if f"{resolved}.{imported}" == owner:
                        names.add(f"{bound}.{name}")
                    if resolved == owner and imported == name:
                        names.add(bound)
        hidden = cls.bindings(tree, scope).keys() if scope else frozenset()
        return cls(frozenset(spelling for spelling in names if spelling.split(".")[0] not in hidden))

    @staticmethod
    def bindings(tree: ast.AST, scope: str) -> dict[str, list[ast.AST]]:
        """Every binding of each name in one lexical module or class scope, as ScopeNames
        decodes it; bindings inside nested functions and classes are theirs, not this scope's."""
        return ScopeNames.of(tree).bindings(scope)

    @staticmethod
    def declarations(tree: ast.AST, scope: str,
                     body: list[ast.stmt]) -> dict[str, tuple[ast.AST, ast.stmt]]:
        """Each name the scope binds exactly once, by a declaring node that is one of its body's
        statements or such a statement's direct child, with that statement. A star import
        leaves no name proved."""
        names = ScopeNames.of(tree)
        bindings = names.bindings(scope)
        if "*" in bindings:
            return {}
        owners = {child: statement for statement in body
                  for child in (statement, *ast.iter_child_nodes(statement))}
        return {name: (node, owners[node]) for name, (node, *others) in bindings.items()
                if not others and node in names.declares and node in owners}

    @classmethod
    def identity(cls, tree: ast.Module, filename: str, scope: str, expression: ast.expr) -> NominalProof:
        """The expression with every name and attribute reference replaced by the module and
        name that own it, as evaluated in ``scope``: an imported name is its source module's
        export, a declared class its own module's, a builtin one the module never binds. A name
        the scope itself binds, or any reference these declarations cannot resolve, proves
        nothing."""
        module = cls.module_of(filename)
        package = module[:-1]
        declared = cls.declarations(tree, "", tree.body)
        bound = cls.bindings(tree, "")
        enclosing = cls.bindings(tree, scope)
        unresolved: list[str] = []

        def owner(name: str) -> str:
            match declared.get(name) if name not in enclosing else ():
                case ast.alias(name=imported, asname=alias), ast.Import():
                    return imported if alias else imported.split(".")[0]
                case ast.alias(name=imported), ast.ImportFrom(module=source, level=level):
                    base = package[:len(package) - level + 1] if level else ()
                    return ".".join((*base, *(source.split(".") if source else ()), imported))
                case _, ast.ClassDef():
                    return ".".join((*module, name))
            if name not in bound and name not in enclosing and name in vars(builtins):
                return f"builtins.{name}"
            unresolved.append(name)
            return name

        class Owned(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.Name:
                return ast.Name(id=owner(node.id))

            def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
                match ast.unparse(node).split("."):
                    case [root, *attributes] if root.isidentifier() and all(map(str.isidentifier, attributes)):
                        return ast.Name(id=".".join((owner(root), *attributes)))
                return self.generic_visit(node)

        owned = Owned().visit(copy.deepcopy(expression))
        return UnprovedIdentity() if unresolved else ProvedIdentity(ast.dump(owned))

    def marks(self, decorator: ast.expr) -> bool:
        return ast.unparse(decorator) in self.names
