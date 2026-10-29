"""agent-comms as a measured project: its facts, each derived from the declaration that owns it
in agent-comms, and the one measure only it has, the type check of its stack clients.

Only agent-comms' ratchet entry imports this module, with agent-comms and its tools on the path.
The rest of the audit package never imports it, so loading the audit from agent-comms creates
no cycle.
"""
from __future__ import annotations

import atexit
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import typing
from collections import Counter
from collections.abc import Callable, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from functools import cache, partial
from pathlib import Path

import agent_comms
import stored_formats
from agent_comms import coordination_schema, field_codec, locked_store, process_claim, store_files, typed_table
from agent_comms.child_process import ForkedProcessPool, SpawnedProcess
from agent_comms.core_stamp import WheelStamp
from agent_comms.declared_family import DeclaredFamily
from agent_comms.field_codec import FieldCodec, boundary_decoder
from agent_comms.installer.carry import STEPS
from agent_comms.phase_evidence import MainTree, PlanReading
from agent_comms.plan_phases import PlanPhase
from agent_comms.repo import Repo
from module_layers import PACKAGE, StackClient

from .checkers import CheckerSite, SideCounts, TypeErrors
from .project import Project, StoredFormat
from .ratchet import BaseSide, Compared, ContentCache, FileCount, HeadSide, Side
from .repository import Repository

SOURCES = Path(inspect.getfile(agent_comms)).parents[1]
REPOSITORY = SOURCES.parent


class AgentCommsProject(Project):
    """agent-comms' own facts, read from the modules that declare them."""

    probe = (
        "import importlib.util, os, sys; sys.path[:0] = sys.argv[2].split(os.pathsep); "
        "spec = importlib.util.spec_from_file_location('stored_formats', sys.argv[1]); "
        "module = sys.modules[spec.name] = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module); "
        "module.main(sys.argv[3:])"
    )
    """The revision's own reader, loaded by file, over only the revision's own source: a tools
    directory on the path would resolve the revision's bare imports into this one's tree."""

    @staticmethod
    def cache_directory() -> Path:
        return Path.home() / ".cache/agent-comms/debt-ratchet"

    @staticmethod
    def source_roots() -> tuple[Path, ...]:
        return (Path(inspect.getfile(stored_formats)).parent, SOURCES)

    @staticmethod
    def pool(workers: int) -> Executor:
        return ForkedProcessPool(workers)

    @staticmethod
    def run(command: tuple[str, ...], *, cwd: Path, env: Mapping[str, str],
            timeout: float) -> subprocess.CompletedProcess[bytes]:
        return SpawnedProcess.run(command, cwd=cwd, env=env, timeout=timeout)

    @staticmethod
    def lock_owners() -> tuple[str, ...]:
        return tuple(Path(inspect.getfile(module)).relative_to(SOURCES).as_posix()
                     for module in (store_files, locked_store, process_claim))

    @staticmethod
    def lock_functions() -> frozenset[str]:
        return frozenset(function.__name__
                         for function in (store_files._store_lock, store_files._async_store_lock))

    @staticmethod
    def absence_decoders() -> tuple[Callable[..., object], ...]:
        return (boundary_decoder,)

    @staticmethod
    def codec_names() -> frozenset[str]:
        return frozenset({FieldCodec.__name__})

    @staticmethod
    def mechanism_modules() -> frozenset[Path]:
        # Our own modules are named by their files; Toad's live in another repository, named by path.
        return frozenset((
            *(Path(inspect.getfile(module)).relative_to(REPOSITORY)
              for module in (field_codec, typed_table, coordination_schema)),
            *(Path("src", "toad", module) for module in ("agent_schema.py", "db.py")),
        ))

    @staticmethod
    def selecting_calls() -> frozenset[str]:
        return frozenset({DeclaredFamily.member_named.__name__})

    @classmethod
    def stored_formats(cls, repo: Repository, ref: str, root: str,
                       changed: set[str]) -> dict[str, StoredFormat]:
        package = Path(root)
        while repo.tracks(ref, str(package / "__init__.py")):
            package = package.parent
        modules = [".".join(Path(path).relative_to(package).with_suffix("").parts)
                   for path in sorted(changed)]
        reader = Path(inspect.getfile(stored_formats)).relative_to(REPOSITORY)
        with tempfile.TemporaryDirectory(prefix="ratchet-formats-") as scratch:
            # Each side runs its own revision's probe script: the head's may import modules
            # that only the head's package declares (#3576 moved field_codec under nominal).
            source = repo.read("archive", ref, "--", str(package), str(reader))
            subprocess.run(["tar", "-x", "-C", scratch], input=source, check=True, timeout=60)
            tree = Path(scratch)
            sources = Compared.source_paths(root, tree)
            for stamp in tree.rglob("package_stamp.py"):
                WheelStamp.of_source(tree / package, repo.revision(ref),
                                     source_paths=sources).write(stamp)
            paths = dict.fromkeys((str(tree / package), *sources))
            result = SpawnedProcess.run(
                (sys.executable, "-c", cls.probe, str(tree / reader),
                 os.pathsep.join(paths), *modules), cwd=scratch,
                env=Compared.environment(root, tree), timeout=300,
            )
        if result.returncode:
            raise ValueError(f"stored formats could not be read at {ref}: {result.stderr.decode()}")
        return {name: StoredFormat(**form) for name, form in json.loads(result.stdout).items()}

    @staticmethod
    def carried(repo: Repository, base: str, head: str) -> bool:
        added = repo.read("diff", "--name-only", "-z", "--diff-filter=A", base, head, "--", str(STEPS))
        return any(Path(path).stem.startswith("carry_") and path.endswith(".py")
                   for path in added.decode().split("\0"))

    @staticmethod
    def guard_roots() -> frozenset[str]:
        declared = Path(inspect.getfile(PlanPhase)).resolve()
        return frozenset({f"{declared.relative_to(REPOSITORY).parts[0]}/"})

    @staticmethod
    @cache
    def reading(repo: Repository, commit: str) -> PlanReading:
        return PlanReading((), MainTree(Repo.of(repo.path), commit))

    @classmethod
    def failing_guards(cls, repo: Repository, commit: str) -> frozenset[type]:
        reading = cls.reading(repo, commit)
        return frozenset(phase for phase in PlanPhase.members_with(PlanPhase)
                         if not phase.done(reading))

    @classmethod
    def guard_sites(cls, repo: Repository, head: str, guard: type) -> tuple[str, ...]:
        reading = cls.reading(repo, head)
        evidence = typing.cast(type[PlanPhase], guard).phase_evidence()
        return tuple(evidence.sites(reading)) or (reading.describe(evidence),)


@dataclass(frozen=True)
class ClientFileCount(FileCount):
    """Type errors in one stack client file, which the client's own code cannot add: both sides
    check the same client commit."""

    def grew(self, grown: int) -> str:
        return (f"grew {self.text} +{grown}: this change removes or changes an agent_comms name "
                "that client file uses; run mypy on it to see which")


class ClientTypeErrors(SideCounts):
    """Mypy errors in each stack client file that imports agent_comms, checked against each
    revision's package. Both sides read the same client commit, so the client's own errors cancel
    and an error the head adds is a name or signature the client uses that the change took away:
    #3110 deleted TurnState.settles as unused while Toad's conversation_turn.py:262 called it, and
    only the running app found it."""

    finding = TypeErrors.finding

    @staticmethod
    @cache
    def clients(repo: Repository, head: str) -> tuple[StackClient, ...]:
        """The head's stack clients, cloned once per run and read by both sides; a revision
        declaring no stack has none."""
        if not repo.tracks(head, "stack/pyproject.toml"):
            return ()
        into = Path(tempfile.mkdtemp(prefix="ratchet-clients-"))
        atexit.register(shutil.rmtree, into, True)
        (into / "stack").mkdir()
        (into / "stack/pyproject.toml").write_bytes(repo.read("show", f"{head}:stack/pyproject.toml"))
        return StackClient.fetched(into / "stack", into)

    @staticmethod
    def selected(client: StackClient) -> list[str]:
        return sorted({entry.path for entry in client.imports
                       if entry.module.partition(".")[0] == PACKAGE})

    @classmethod
    def findings(cls, repo: Repository, ref: str, client: StackClient,
                 side: type[Side]) -> tuple[type[Side], tuple[CheckerSite, ...]]:
        commit = Repository(client.checkout).revision("HEAD")
        tree = repo.git("rev-parse", f"{ref}:src").strip()
        return side, ContentCache.memo(partial(cls.checked, repo, ref, client, side),
                                       cls.__name__, client.name, commit, tree)

    @classmethod
    def checked(cls, repo: Repository, ref: str, client: StackClient,
                side: type[Side]) -> tuple[CheckerSite, ...]:
        selected = cls.selected(client)
        cache = ContentCache.beside("mypy-clients") / client.name / side.family_name
        with tempfile.TemporaryDirectory(prefix="ratchet-client-") as scratch:
            subprocess.run(["tar", "-x", "-C", scratch], input=repo.read("archive", ref, "src"),
                           check=True, timeout=60)
            # The client checks under its own configuration, from its checkout.
            result = SpawnedProcess.run(
                (sys.executable, "-m", *TypeErrors.mypy(
                    cache, "--follow-imports=silent", "--ignore-missing-imports", *selected)),
                cwd=client.checkout, timeout=600,
                env={**os.environ, "MYPYPATH": os.pathsep.join(
                    (str(Path(scratch, "src")), str(client.checkout / "src")))},
            )
        output = cls.checker_output(result, f"{client.name} at {ref}")
        return tuple(CheckerSite(f"{client.name}/{match['path']}", int(match["line"]),
                                 match["message"])
                     for match in cls.finding.finditer(output) if match["path"] in selected)

    @classmethod
    def parts(cls, repo: Repository, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        # Only a change to the package the clients import can change what they see.
        if not any(path.startswith(f"src/{PACKAGE}/") for path in changed):
            return ()
        return tuple(partial(cls.findings, repo, side.ref(base, head), client, side)
                     for client in cls.clients(repo, head)
                     if cls.selected(client)
                     for side in Side.sides())

    @classmethod
    def measured(cls, found: dict[type[Side], Counter[str]], changed: set[str]) -> list[str]:
        """The client files with an error on either side; changed paths are the package's."""
        return sorted(found[BaseSide].keys() | found[HeadSide].keys())

    @classmethod
    def at(cls, path: str) -> ClientFileCount:
        return ClientFileCount(cls, path)
