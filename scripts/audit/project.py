"""The measured project: a repository and the facts it declares about its own code.

A comparison measures one Project instance, passed to every measure as the repository it
reads, so each measure asks the project it measures and nothing else; there is no process-wide
declaration. Every fact is required: a repository that has none of something says so in its
declaration. The measures own their algorithms; the declaration owns only what is true of the
repository's own code, derived from that code's own declarations, and lives beside that code.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import os
import pickle
import subprocess
import sys
import tempfile
import time
import types
import typing
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .repository import Repository

AUDIT_SCRIPTS = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class StoredFormat:
    """A versioned record as its writer stores it: its format version, each stored key with
    that field's schema, and the keys every record carries."""

    version: int
    fields: dict[object, object]
    required: list[str]


class ContentCache(ABC):
    """Capability: results that are pure functions of content, stored under a digest of that
    content, of the measuring code and of the environment its checkers run in. The key is the
    content, so an entry never goes stale: any change to what produced it is a different key.
    Batch 3579 went red on 170 Toad errors nobody wrote, because the key held mypy's version but
    not Textual's commit, and a git dependency moves without changing its version."""

    retention_days: typing.ClassVar[int] = 14

    @abstractmethod
    def cache_directory(self) -> Path:
        """Where results that are pure functions of content are kept between runs."""

    @abstractmethod
    def source_roots(self) -> tuple[Path, ...]:
        """The directories of the project's own measuring code; a loaded module under one
        is part of every cached result's key."""

    def beside(self, use: str) -> Path:
        """A directory a checker keeps its own analysis in, next to the cache."""
        directory = self.cache_directory()
        return directory.with_name(f"{directory.name}-{use}")

    def measuring_sources(self) -> tuple[Path, ...]:
        """Every module loaded in this process under the project's own source roots, plus the
        audit package: measuring subprocesses import it from AUDIT_SCRIPTS, so sys.modules
        alone would miss a change to it. Sorted."""
        roots = self.source_roots()
        origins = (Path(origin).resolve() for module in list(sys.modules.values())
                   if isinstance(module, types.ModuleType)
                   and (origin := module.__dict__.get("__file__")))
        loaded = {path for path in origins if any(path.is_relative_to(root) for root in roots)}
        audited = {path.resolve() for path in (AUDIT_SCRIPTS / "audit").rglob("*.py")}
        return tuple(sorted(loaded | audited))

    @cached_property
    def code(self) -> bytes:
        """The measuring code (see measuring_sources), and every installed distribution: the
        checkers and everything they read, such as the stack client's Textual. A git install
        keeps its version across commits, so its recorded origin (direct_url.json) is digested
        too. A comparison reads it once, after the measures' imports and before its workers
        start, so the set is the one that ran and every worker carries it."""
        digest = hashlib.sha256(f"{sys.version}\0{__name__}".encode())
        for installed in sorted(f"{dist.name}={dist.version}\0{dist.read_text('direct_url.json')}"
                                for dist in importlib.metadata.distributions()):
            digest.update(b"\0" + installed.encode())
        for path in self.measuring_sources():
            digest.update(b"\0" + str(path).encode() + b"\0" + path.read_bytes())
        return digest.digest()

    def cached(self, *parts: str) -> Path:
        key = hashlib.sha256(b"\0".join((self.code, *(part.encode() for part in parts))))
        identity = key.hexdigest()
        return self.cache_directory() / identity[:2] / identity

    def stored(self, *parts: str) -> tuple[object, ...]:
        """The stored result for these parts, as a one-item tuple, or an empty one."""
        path = self.cached(*parts)
        if not path.is_file():
            return ()
        os.utime(path)  # the mtime records the last use, which is what pruning reads
        return (pickle.loads(path.read_bytes()),)

    def prune(self) -> None:
        """Delete the entries neither written nor read for retention_days."""
        horizon = time.time() - self.retention_days * 86400
        for path in self.cache_directory().glob("*/*"):
            if path.stat().st_mtime < horizon:
                path.unlink(missing_ok=True)

    def store[Value](self, value: Value, *parts: str) -> Value:
        path = self.cached(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
            staged.write(pickle.dumps(value))
        os.replace(staged.name, path)
        return value

    def memo[Value](self, compute: Callable[[], Value], *parts: str) -> Value:
        for value in self.stored(*parts):
            return typing.cast(Value, value)
        return self.store(compute(), *parts)


@dataclass(frozen=True)
class Project[Guard](ContentCache, Repository, ABC):
    """The measured repository and its own facts. Guard is the class of its standing guards."""

    @abstractmethod
    def pool(self, workers: int) -> Executor:
        """The process pool a comparison runs its parts in. Workers are forked, so they start
        from the measures this process already imported."""

    @abstractmethod
    def run(self, command: tuple[str, ...], *, cwd: Path, env: Mapping[str, str],
            timeout: float) -> subprocess.CompletedProcess[bytes]:
        """One checker or probe process, collected with its output."""

    @abstractmethod
    def lock_owners(self) -> tuple[str, ...]:
        """The modules that own locking; a lock call inside one is the owner's own."""

    @abstractmethod
    def lock_functions(self) -> frozenset[str]:
        """The project's own lock functions, by name."""

    @abstractmethod
    def absence_decoders(self) -> tuple[Callable[..., object], ...]:
        """Decorators marking a decoder that meets absence from outside; its body is the boundary."""

    @abstractmethod
    def mechanism_modules(self) -> frozenset[Path]:
        """The modules that own codec and schema behaviour, by repository path."""

    def owns_mechanism(self, path: str) -> bool:
        """Only the exact repository paths of the mechanism modules own it; nested package
        tails do not."""
        return Path(path) in self.mechanism_modules()

    @abstractmethod
    def selecting_calls(self) -> frozenset[str]:
        """The project's calls that select a family member by its spelled name."""

    @abstractmethod
    def stored_formats(self, ref: str, root: str, changed: set[str]) -> dict[str, StoredFormat]:
        """Each versioned record in the changed modules, read by that revision's own code."""

    @abstractmethod
    def carried(self, base: str, head: str) -> bool:
        """Whether the change adds a step that rewrites stored records into their new format."""

    @abstractmethod
    def guard_roots(self) -> frozenset[str]:
        """The source roots that declare the project's standing guards."""

    @abstractmethod
    def failing_guards(self, commit: str) -> frozenset[type[Guard]]:
        """Each standing guard that does not hold at a commit."""

    @abstractmethod
    def guard_sites(self, head: str, guard: type[Guard]) -> tuple[str, ...]:
        """Where a guard fails at the head, or its description when it names no site."""
