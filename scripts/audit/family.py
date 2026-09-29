"""Closed families of classes, registered by name.

A family root is declared with ``root=True``. Every concrete member registers under a name
derived from its class name (snake case, minus the family's affix). ``name=`` is only for
spellings an external format dictates (git's status letters, for example). Intermediate
classes that group members declare ``abstract=True`` and do not register.
"""
from __future__ import annotations

import re
from typing import ClassVar, Self


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class UnknownMember(LookupError):
    pass


class Family:
    _registry: ClassVar[dict[str, type["Family"]]]
    _affix: ClassVar[str]
    family_name: ClassVar[str]

    def __init_subclass__(cls, *, root: bool = False, abstract: bool = False, affix: str = "",
                          name: str | None = None, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if root:
            cls._registry = {}
            cls._affix = affix
            return
        if abstract:
            return
        cls.family_name = name or snake_case(cls.__name__.removesuffix(cls._affix))
        clash = cls._registry.get(cls.family_name)
        if clash is not None:
            raise TypeError(f"{cls.__name__} and {clash.__name__} both claim the name {cls.family_name!r}")
        cls._registry[cls.family_name] = cls

    @classmethod
    def members(cls) -> tuple[type[Self], ...]:
        return tuple(member for member in cls._registry.values() if issubclass(member, cls))

    @classmethod
    def members_with(cls, capability: type) -> tuple[type[Self], ...]:
        return tuple(member for member in cls.members() if issubclass(member, capability))

    @classmethod
    def decode(cls, name: str) -> type[Self]:
        member = cls._registry.get(name)
        if member is None or not issubclass(member, cls):
            raise UnknownMember(f"{cls.__name__} has no member named {name!r}")
        return member
