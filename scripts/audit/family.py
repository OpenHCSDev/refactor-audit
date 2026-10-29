"""Closed families of classes, registered by name.

A family root is declared with ``root=True``. Every concrete member registers under a name
derived from its class name (snake case, minus the family's affix). ``name=`` is only for
spellings an external format dictates (git's status letters, for example). A class with an
unimplemented abstract method is an intermediate that groups members, never a member: its
ABC abstractness is the one declaration, read whenever the family is read.
"""
from __future__ import annotations

import inspect
import re
from typing import ClassVar, Self


def snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class UnknownMember(LookupError):
    pass


class Family:
    _declared: ClassVar[list[type["Family"]]]
    _affix: ClassVar[str]
    family_name: ClassVar[str]

    def __init_subclass__(cls, *, root: bool = False, affix: str = "",
                          name: str | None = None, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if root:
            cls._declared = []
            cls._affix = affix
            return
        cls.family_name = name or snake_case(cls.__name__.removesuffix(cls._affix))
        cls._declared.append(cls)

    @classmethod
    def _named(cls) -> dict[str, type[Family]]:
        # ABCMeta records abstract methods after __init_subclass__, so abstractness is read
        # here, when the family is read, and only members claim a name.
        named: dict[str, type[Family]] = {}
        for member in cls._declared:
            if not inspect.isabstract(member):
                clash = named.setdefault(member.family_name, member)
                if clash is not member:
                    raise TypeError(f"{member.__name__} and {clash.__name__} both claim the name "
                                    f"{member.family_name!r}")
        return named

    @classmethod
    def members(cls) -> tuple[type[Self], ...]:
        return tuple(member for member in cls._named().values() if issubclass(member, cls))

    @classmethod
    def members_with(cls, capability: type) -> tuple[type[Self], ...]:
        return tuple(member for member in cls.members() if issubclass(member, capability))

    @classmethod
    def declared_position(cls) -> int:
        return tuple(cls._named().values()).index(cls)

    @classmethod
    def decode(cls, name: str) -> type[Self]:
        member = cls._named().get(name)
        if member is None or not issubclass(member, cls):
            raise UnknownMember(f"{cls.__name__} has no member named {name!r}")
        return member
