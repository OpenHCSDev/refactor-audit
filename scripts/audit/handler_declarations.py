"""Canonical syntactic primitive-handler census, shared by audit and ratchet.

This collector never imports or executes the inspected module. Imported names
and aliases are read once from its AST. It is a screening count, not a native
execution proof or a complete dynamic Python name resolver.
"""
from __future__ import annotations

import ast
from pathlib import PurePath


class BuiltinHandlerDeclarations:
    """The codec boundary and primitive arm taxonomy have one declaration owner."""
    builtin_types = (dict, list, str, int, float, bool, tuple)

    @classmethod
    def admits(cls, path: str) -> bool:
        # Only the established wire codec owns primitive discrimination.
        # Naming another module *_codec.py does not admit a bypass.
        return PurePath(path).name != "field_codec.py"

    @classmethod
    def count_module(cls, node: ast.Module) -> int:
        bindings: dict[str, str] = {}
        for statement in node.body:
            match statement:
                case ast.ImportFrom(module=module, names=names) if module:
                    for alias in names:
                        bindings[alias.asname or alias.name] = f"{module}.{alias.name}"
                case ast.Import(names=names):
                    for alias in names:
                        bindings[alias.asname or alias.name.split('.')[0]] = (
                            alias.name if alias.asname else alias.name.split('.')[0])
                case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                    bindings[name] = ""
                case ast.Assign(targets=targets) | ast.AnnAssign(target=targets):
                    for target in targets if isinstance(targets, list) else (targets,):
                        for child in ast.walk(target):
                            if isinstance(child, ast.Name):
                                bindings[child.id] = ""

        def qualified(expression: ast.AST) -> str:
            match expression:
                case ast.Name(id=name):
                    return bindings.get(name, f"builtins.{name}")
                case ast.Attribute(value=owner, attr=name):
                    return f"{qualified(owner)}.{name}"
            return ""

        primitives = {f"builtins.{kind.__name__}" for kind in cls.builtin_types}
        total = 0
        for function in ast.walk(node):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                match decorator:
                    case ast.Call(func=handler, args=arguments):
                        if qualified(handler).rsplit('.', 2)[-2:] == ['mro_dispatch', 'handles']:
                            total += sum(qualified(argument) in primitives for argument in arguments)
        return total

