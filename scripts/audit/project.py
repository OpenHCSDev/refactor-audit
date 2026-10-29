"""The facts a measured repository declares about itself, read by every measure from one place.

A repository's facts are one Project subclass, declared beside the measures in this package;
importing it declares it for the process, and a second declaration fails loud. Every fact is
required: a repository that has none of something says so in its declaration, and a
comparison with no concrete declaration refuses to run. The measures own their algorithms;
the declaration owns only what is true of the repository's own code, derived from that code's
own declarations.
"""
from __future__ import annotations

import inspect
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .repository import Repository


class UndeclaredProject(LookupError):
    """A comparison ran with no repository declaring its facts."""


@dataclass(frozen=True)
class StoredFormat:
    """A versioned record as its writer stores it: its format version, each stored key with
    that field's schema, and the keys every record carries."""

    version: int
    fields: dict[object, object]
    required: list[str]


class Project(ABC):
    """The measured repository's own facts."""

    declared: ClassVar[type[Project]]

    def __init_subclass__(cls, **options: object) -> None:
        super().__init_subclass__(**options)
        if Project.declared is not Project:
            raise TypeError(f"{cls.__name__} and {Project.declared.__name__} both declare the "
                            "measured project")
        Project.declared = cls

    @staticmethod
    def measured() -> type[Project]:
        """The declared project; a comparison with none refuses."""
        if inspect.isabstract(Project.declared):
            raise UndeclaredProject("No repository declares the measured project's facts")
        return Project.declared

    @staticmethod
    @abstractmethod
    def cache_directory() -> Path:
        """Where results that are pure functions of content are kept between runs."""

    @staticmethod
    @abstractmethod
    def source_roots() -> tuple[Path, ...]:
        """The directories of the project's own measuring code; a loaded module under one
        is part of every cached result's key."""

    @staticmethod
    @abstractmethod
    def pool(workers: int) -> Executor:
        """The process pool a comparison runs its parts in. Workers are forked, so they start
        from the measures this process already imported."""

    @staticmethod
    @abstractmethod
    def run(command: tuple[str, ...], *, cwd: Path, env: Mapping[str, str],
            timeout: float) -> subprocess.CompletedProcess[bytes]:
        """One checker or probe process, collected with its output."""

    @staticmethod
    @abstractmethod
    def lock_owners() -> tuple[str, ...]:
        """The modules that own locking; a lock call inside one is the owner's own."""

    @staticmethod
    @abstractmethod
    def lock_functions() -> frozenset[str]:
        """The project's own lock functions, by name."""

    @staticmethod
    @abstractmethod
    def absence_decoders() -> tuple[Callable[..., object], ...]:
        """Decorators marking a decoder that meets absence from outside; its body is the boundary."""

    @staticmethod
    @abstractmethod
    def codec_names() -> frozenset[str]:
        """The project's codec owners, whose decode-of-encode round trip is a codec detour."""

    @staticmethod
    @abstractmethod
    def mechanism_modules() -> frozenset[Path]:
        """The modules that own codec and schema behaviour, by repository path."""

    @staticmethod
    @abstractmethod
    def selecting_calls() -> frozenset[str]:
        """The project's calls that select a family member by its spelled name."""

    @staticmethod
    @abstractmethod
    def stored_formats(repo: Repository, ref: str, root: str,
                       changed: set[str]) -> dict[str, StoredFormat]:
        """Each versioned record in the changed modules, read by that revision's own code."""

    @staticmethod
    @abstractmethod
    def carried(repo: Repository, base: str, head: str) -> bool:
        """Whether the change adds a step that rewrites stored records into their new format."""

    @staticmethod
    @abstractmethod
    def guard_roots() -> frozenset[str]:
        """The source roots that declare the project's standing guards."""

    @staticmethod
    @abstractmethod
    def failing_guards(repo: Repository, commit: str) -> frozenset[type]:
        """Each standing guard that does not hold at a commit."""

    @staticmethod
    @abstractmethod
    def guard_sites(repo: Repository, head: str, guard: type) -> tuple[str, ...]:
        """Where a guard fails at the head, or its description when it names no site."""


Project.declared = Project
