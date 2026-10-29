"""The measured project and the Git checkout the ratchet's tests compare.

FixtureProject declares every fact a project declares, each the answer for a fixture
repository that has none of the thing, except the codec and the member-selecting call the
measures' tests read. Its cache lives for one test session, so a run with an unavailable
checker never supplies another run's findings."""
from __future__ import annotations

import atexit
import json
import multiprocessing
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, ProcessPoolExecutor
from pathlib import Path

from audit.project import Project, StoredFormat
from audit.ratchet import Compared, compare
from audit.repository import Repository

CACHE = Path(tempfile.mkdtemp(prefix="ratchet-test-cache-"))
atexit.register(shutil.rmtree, CACHE, True)

# Git identity and configuration of the fixture commits, isolated from the caller's.
IDENTITY = ("-c", "user.name=Audit test", "-c", "user.email=audit@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")


class FixtureProject(Project):
    @staticmethod
    def cache_directory() -> Path:
        return CACHE / "measure-cache"

    @staticmethod
    def source_roots() -> tuple[Path, ...]:
        return ()

    @staticmethod
    def pool(workers: int) -> Executor:
        return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork"))

    @staticmethod
    def run(command: tuple[str, ...], *, cwd: Path, env: Mapping[str, str],
            timeout: float) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(command, cwd=cwd, env=env, timeout=timeout, capture_output=True)

    @staticmethod
    def lock_owners() -> tuple[str, ...]:
        return ()

    @staticmethod
    def lock_functions() -> frozenset[str]:
        return frozenset()

    @staticmethod
    def absence_decoders() -> tuple[Callable[..., object], ...]:
        return ()

    @staticmethod
    def codec_names() -> frozenset[str]:
        return frozenset({"FieldCodec"})

    @staticmethod
    def mechanism_modules() -> frozenset[Path]:
        return frozenset()

    @staticmethod
    def selecting_calls() -> frozenset[str]:
        return frozenset({"member_named"})

    @staticmethod
    def stored_formats(repo: Repository, ref: str, root: str,
                       changed: set[str]) -> dict[str, StoredFormat]:
        return {}

    @staticmethod
    def carried(repo: Repository, base: str, head: str) -> bool:
        return False

    @staticmethod
    def guard_roots() -> frozenset[str]:
        return frozenset()

    @staticmethod
    def failing_guards(repo: Repository, commit: str) -> frozenset[type]:
        return frozenset()

    @staticmethod
    def guard_sites(repo: Repository, head: str, guard: type) -> tuple[str, ...]:
        return ()


class Checkout:
    """A fresh Git repository whose files live under one source root."""

    def __init__(self, path: Path, root: str):
        self.path = path
        self.root = root
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        return subprocess.check_output(
            ["git", *IDENTITY, "-C", str(self.path), *args], text=True, timeout=10
        ).strip()

    def commit(self, files: dict[str, str | None]) -> str:
        for name, source in files.items():
            path = self.path / self.root / name
            if source is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
        self.git("add", "--all")
        self.git("commit", "-qm", "test", "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def compare(self, base: str, head: str, *measured: type[Compared]) -> tuple[int, dict]:
        """The comparison's exit status and JSON report, taking only the named measures'
        measurements, with a narrow pool so parallel test workers keep their width."""
        result = compare(Repository(self.path), base, head, self.root, 2, measured)
        return int(result.failed), json.loads(json.dumps(result.report()))
