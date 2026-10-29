"""The measured project and the Git checkout the ratchet's tests compare.

FixtureProject declares every fact a project declares, each the answer for a fixture
repository that has none of the thing, except the codec module and the member-selecting call
the measures' tests read. Its cache lives for one test session, so a run with an unavailable
checker never supplies another run's findings."""
from __future__ import annotations

import atexit
import json
import multiprocessing
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import Executor, ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from audit.project import Project, StoredFormat
from audit.ratchet import Compared, compare

CACHE = Path(tempfile.mkdtemp(prefix="ratchet-test-cache-"))
atexit.register(shutil.rmtree, CACHE, True)

# Git identity and configuration of the fixture commits, isolated from the caller's.
IDENTITY = ("-c", "user.name=Audit test", "-c", "user.email=audit@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null")


@dataclass(frozen=True)
class FixtureProject(Project[object]):
    """A fixture repository whose sources live under one root."""

    root: str = ""

    def cache_directory(self) -> Path:
        return CACHE / "measure-cache"

    def source_roots(self) -> tuple[Path, ...]:
        return ()

    def pool(self, workers: int) -> Executor:
        return ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork"))

    def run(self, command: tuple[str, ...], *, cwd: Path, env: dict[str, str],
            timeout: float) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(command, cwd=cwd, env=env, timeout=timeout, capture_output=True)

    def lock_owners(self) -> tuple[str, ...]:
        return ()

    def lock_functions(self) -> frozenset[str]:
        return frozenset()

    def absence_decoders(self) -> tuple[Callable[..., object], ...]:
        return ()

    def mechanism_modules(self) -> frozenset[Path]:
        return frozenset({Path(self.root, "field_codec.py")})

    def selecting_calls(self) -> frozenset[str]:
        return frozenset({"member_named"})

    def stored_formats(self, ref: str, root: str, changed: set[str]) -> dict[str, StoredFormat]:
        return {}

    def carried(self, base: str, head: str) -> bool:
        return False

    def guard_roots(self) -> frozenset[str]:
        return frozenset()

    def failing_guards(self, commit: str) -> frozenset[type[object]]:
        return frozenset()

    def guard_sites(self, head: str, guard: type[object]) -> tuple[str, ...]:
        return ()


# The project the measures' own tests count source in, outside any checkout.
PROJECT = FixtureProject(Path())


class Checkout:
    """A fresh Git repository whose files live under one source root."""

    def __init__(self, path: Path, root: str):
        self.path = path
        self.root = root
        self.project = FixtureProject(path, root)
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
        result = compare(self.project, base, head, self.root, 2, measured)
        return int(result.failed), json.loads(json.dumps(result.report()))
