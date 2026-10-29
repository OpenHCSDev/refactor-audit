"""Measures of the project's own declarations: stored record formats and standing guards.

Their algorithms are here; what a format or a guard is comes from the measured project."""
from __future__ import annotations

import ast
from collections.abc import Callable
from functools import partial

from .project import Project, StoredFormat
from .ratchet import ChangeMeasure, Compared, Counts, PhaseSiteCount


class UncarriedRecordFormat(ChangeMeasure):
    """A stored record's format changed without its version, or its version without a carry.

    Files in the old format stay on disk, so a change to a versioned record's stored form
    bumps its version, and a bumped version arrives with a new carry step that rewrites the
    old files. The stored form is what the project's writer stores, not the annotation text:
    two representations with the same wire spelling are the same format.
    """

    @staticmethod
    def declares_version(source: bytes) -> bool:
        """Only a module with a class declaring `version` can hold a versioned record; others
        are never imported."""
        for node in ast.walk(ast.parse(source)):
            for item in node.body if isinstance(node, ast.ClassDef) else ():
                match item:
                    case ast.AnnAssign(target=ast.Name(id="version")) | ast.Assign(
                            targets=[ast.Name(id="version")]):
                        return True
        return False

    @classmethod
    def formats(cls, repo: Project, ref: str, root: str,
                changed: set[str]) -> dict[str, StoredFormat]:
        """Each versioned record in the changed modules that declare one, measured by that
        revision's own code; the commit fixes every input, its stamp included."""
        versioned = {path for path, source in repo.sources(ref, changed & repo.python_paths(ref, root))
                     if cls.declares_version(source)}
        if not versioned:
            return {}
        return repo.memo(
            partial(repo.stored_formats, ref, root, versioned),
            cls.__name__, repo.revision(ref), root, *sorted(changed))

    @classmethod
    def counted(cls, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        carried = repo.carried(base, head)
        before = cls.formats(repo, base, root, changed)
        after = cls.formats(repo, head, root, changed)
        uncarried = sum(before[name].version == form.version or not carried
                        for name, form in after.items() if name in before and before[name] != form)
        return {cls.total(): 0}, {cls.total(): uncarried}


class DonePhaseBroken(Compared):
    """A standing guard that holds at the base and fails at the head: a done plan phase is a
    standing guard, so a change re-adding what the phase deleted fails here, one key per site.

    The project declares its guards and reads them; a guard not yet holding at the base is plan
    work in progress and blocks nothing. Evidence that cannot be read here holds at neither
    revision. Each revision is read in one process, so its modules are parsed once for every
    guard, and kept by the commit and the measuring code, which holds the guards' declarations.
    It runs for the source roots that declare the guards."""

    @classmethod
    def parts(cls, repo: Project, base: str, head: str, changed: set[str],
              root: str) -> tuple[Callable[[], object], ...]:
        if root not in repo.guard_roots():
            return ()
        return tuple(partial(cls.failing, repo, repo.revision(ref)) for ref in (base, head))

    @classmethod
    def combine(cls, results: list, repo: Project, base: str, head: str, changed: set[str],
                root: str) -> tuple[Counts, Counts]:
        """A failing pull request pays for reading its sites; a passing one never reads them."""
        if not results:
            return {}, {}
        before, after = results
        head_commit = repo.revision(head)
        broken: Counts = {
            PhaseSiteCount(cls, guard, site): 1
            for guard in sorted(after - before, key=lambda guard: guard.__name__)
            for site in repo.guard_sites(head_commit, guard)
        }
        return dict.fromkeys(broken, 0), broken

    @classmethod
    def failing(cls, repo: Project, commit: str) -> frozenset[type]:
        """Each guard not holding at a commit."""
        return repo.memo(partial(repo.failing_guards, commit),
                                 cls.__name__, commit)
