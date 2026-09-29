"""A plan package's documents, and the checks run on it before agents receive it."""
from __future__ import annotations

import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import ClassVar, Iterator, TypeVar

from .family import Family


def anchor(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.lower()).strip().replace(" ", "-")


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    fragment: str


@dataclass(frozen=True)
class Document:
    name: str
    text: str
    link_pattern: ClassVar[re.Pattern] = re.compile(r"\]\(([^)#\s]+\.md)(?:#([a-z0-9-]+))?\)")

    @classmethod
    def claims(cls, name: str) -> bool:
        return False

    @cached_property
    def anchors(self) -> frozenset[str]:
        return frozenset(anchor(h) for h in re.findall(r"^#{1,4} (.+)$", self.text, re.M))

    @cached_property
    def links(self) -> tuple[Link, ...]:
        return tuple(Link(self.name, target, fragment) for target, fragment in self.link_pattern.findall(self.text))


@dataclass(frozen=True)
class SurfaceDocument(Document):
    """A surface receipt, named by its surface ID (S12-typed-tables.md, T1-settings.md)."""
    id_pattern: ClassVar[re.Pattern] = re.compile(r"([A-Z]+\d+[a-z]?)[-_]")

    @classmethod
    def claims(cls, name: str) -> bool:
        return bool(cls.id_pattern.match(name))

    @property
    def surface_id(self) -> str:
        return self.id_pattern.match(self.name).group(1)

    @cached_property
    def declared_abstractions(self) -> frozenset[str] | None:
        header = next((line for line in self.text.splitlines() if line.startswith("**Shared abstractions**")), None)
        return None if header is None else frozenset(re.findall(r"\[(A\d+)\]", header))


@dataclass(frozen=True)
class AbstractionsDocument(Document):
    """The shared-abstractions table: ID, name, builder, wave, users."""
    row_pattern: ClassVar[re.Pattern] = re.compile(r"^\| \[(A\d+)\]\([^)]*\) \|[^|]*\|([^|]*)\|[^|]*\|([^|]*)\|", re.M)

    @classmethod
    def claims(cls, name: str) -> bool:
        return bool(re.search(r"shared.abstractions", name, re.I))

    @cached_property
    def surfaces_by_abstraction(self) -> dict[str, frozenset[str]]:
        return {row.group(1): frozenset(re.findall(r"\b([A-Z]+\d+)\b", row.group(2) + " " + row.group(3)))
                for row in self.row_pattern.finditer(self.text)}


D = TypeVar("D", bound=Document)

DOCUMENT_KINDS: tuple[type[Document], ...] = (AbstractionsDocument, SurfaceDocument)


@dataclass(frozen=True)
class PlanPackage:
    root: Path
    documents: tuple[Document, ...]

    @classmethod
    def load(cls, root: Path) -> "PlanPackage":
        def document(path: Path) -> Document:
            kind = next((kind for kind in DOCUMENT_KINDS if kind.claims(path.name)), Document)
            return kind(path.name, path.read_text())
        return cls(root, tuple(document(path) for path in sorted(root.glob("*.md"))))

    def named(self, name: str) -> Document | None:
        return next((d for d in self.documents if d.name == name), None)

    def of_kind(self, kind: type[D]) -> tuple[D, ...]:
        return tuple(d for d in self.documents if isinstance(d, kind))

    def zip(self, out: Path) -> int:
        ordered = sorted(self.documents, key=lambda d: (d.name != "README.md", d.name))
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for document in ordered:
                archive.write(self.root / document.name, f"{self.root.name}/{document.name}")
        return len(ordered)


class CheckResult(ABC):
    fails: ClassVar[bool] = False

    @abstractmethod
    def render(self, check: str) -> str: ...


class Passed(CheckResult):
    def render(self, check: str) -> str:
        return f"{check}: OK"


@dataclass(frozen=True)
class NeedsReview(CheckResult):
    lines: tuple[str, ...]

    def render(self, check: str) -> str:
        return "\n".join([f"{check}: {len(self.lines)} lines to review", *(f"    {line}" for line in self.lines[:30])])


@dataclass(frozen=True)
class Failed(CheckResult):
    problems: tuple[str, ...]
    fails = True

    def render(self, check: str) -> str:
        return "\n".join([f"{check}: {len(self.problems)} FAILED", *(f"    {p}" for p in self.problems)])


class Check(Family, ABC, root=True, affix="Check"):
    @abstractmethod
    def run(self, package: PlanPackage) -> CheckResult: ...


class LinksCheck(Check):
    """Every relative .md link and #anchor resolves."""

    def run(self, package: PlanPackage) -> CheckResult:
        problems = tuple(self._broken(package))
        return Failed(problems) if problems else Passed()

    @staticmethod
    def _broken(package: PlanPackage) -> Iterator[str]:
        for document in package.documents:
            for link in document.links:
                target = package.named(link.target)
                if target is None:
                    yield f"{link.source}: missing file {link.target}"
                elif link.fragment and link.fragment not in target.anchors:
                    yield f"{link.source}: missing anchor {link.target}#{link.fragment}"


class LeaksCheck(Check):
    """Compatibility language that is not prohibiting compatibility. A heuristic: read each line."""
    suspect: ClassVar[re.Pattern] = re.compile(
        r"backward|compat\w*|legacy|fallback|byte-identical|read identically|keep reading|remain readable"
        r"|for now|follow-up|to be safe|first phase|phase 1", re.I)
    prohibits: ClassVar[re.Pattern] = re.compile(
        r"\b(no|never|not|delete[sd]?|without|revoked|forbid\w*|zero|none|remove[sd]?|ends?|nothing"
        r"|cannot|there is no|instead of|replac\w*)\b|by another name", re.I)

    def run(self, package: PlanPackage) -> CheckResult:
        lines = tuple(f"{d.name}:{number}: {line.strip()[:110]}"
                      for d in package.documents for number, line in enumerate(d.text.splitlines(), 1)
                      if self.suspect.search(line) and not self.prohibits.search(line))
        return NeedsReview(lines) if lines else Passed()


class ConsistencyCheck(Check):
    """The shared-abstractions table and every surface header name the same builders and users."""

    def run(self, package: PlanPackage) -> CheckResult:
        match package.of_kind(AbstractionsDocument):
            case ():
                return Passed()
            case (table, *_):
                pass
        declared = {s.surface_id: s.declared_abstractions for s in package.of_kind(SurfaceDocument)
                    if s.declared_abstractions is not None}
        problems = []
        for abstraction, table_surfaces in table.surfaces_by_abstraction.items():
            expected = table_surfaces & declared.keys()
            headers = frozenset(surface for surface, abstractions in declared.items() if abstraction in abstractions)
            if expected != headers:
                problems.append(f"{abstraction}: table says {sorted(expected)}, headers say {sorted(headers)}")
        return Failed(tuple(problems)) if problems else Passed()


class StyleCheck(Check):
    """Em dashes, which the owner's documents do not use."""

    def run(self, package: PlanPackage) -> CheckResult:
        lines = tuple(f"{d.name}:{n}" for d in package.documents for n, line in enumerate(d.text.splitlines(), 1) if "\u2014" in line)
        return NeedsReview(lines) if lines else Passed()
