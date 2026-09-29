"""Aggregates of parse outcomes, and the tables that render them."""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterable

from .measures import Headline, Measure, Measured, ParseOutcome, Tally, Unparsed, interpreter


@dataclass(frozen=True)
class Census:
    measured: tuple[Measured, ...]
    unparsed: tuple[Unparsed, ...]

    @classmethod
    def of(cls, outcomes: Iterable[ParseOutcome]) -> "Census":
        outcomes = tuple(outcomes)
        return cls(tuple(o for o in outcomes if isinstance(o, Measured)),
                   tuple(o for o in outcomes if isinstance(o, Unparsed)))

    @property
    def tally(self) -> Tally:
        return sum((o.tally for o in self.measured), Tally())

    def coverage_warning(self) -> str:
        lost = sum(o.code_lines for o in self.unparsed)
        total = lost + sum(o.code_lines for o in self.measured)
        return (f"WARNING: {len(self.unparsed)} files ({lost} of {total} code lines) did not parse under Python "
                f"{interpreter()} and were NOT measured. Re-run with the project's Python.")

    def render_coverage(self) -> str:
        return self.coverage_warning() if self.unparsed else ""

    def render_top(self, n: int) -> str:
        ranked = sorted(self.measured, key=lambda o: -o.tally.score())[:n]
        weighted = ", ".join(f"{m.family_name} x{m.weight}" for m in Measure.members() if m.weight)
        lines = [f"Top {n} files by weighted debt ({weighted}):"]
        lines += [f"  {o.tally.score():>6}  {o.path}  (code lines {o.tally.code_lines:+})" for o in ranked]
        return "\n".join(lines) if ranked else ""

    def record(self) -> dict[str, object]:
        return {"tally": self.tally.as_record(),
                "files": {o.path: o.tally.as_record() for o in self.measured},
                "unparsed": [o.path for o in self.unparsed]}


@dataclass(frozen=True)
class DensityTable:
    columns: tuple[tuple[str, Tally], ...]
    minimum_lines: ClassVar[int] = 500

    def render(self) -> str:
        if any(tally.code_lines < self.minimum_lines for _, tally in self.columns):
            return (f"(densities omitted: under {self.minimum_lines} net code lines, "
                    "a ratio of changes to net lines is meaningless)")
        header = "per 1,000 code lines".ljust(24) + "".join(name.rjust(14) for name, _ in self.columns)
        rows = [measure.family_name.ljust(24) + "".join(f"{tally.density(measure):14.2f}" for _, tally in self.columns)
                for measure in Measure.members()]
        headline = " + ".join(m.family_name for m in Measure.members_with(Headline))
        rows.append("HEADLINE".ljust(24) + "".join(f"{tally.headline():14.1f}" for _, tally in self.columns) + f"   ({headline})")
        rows.append("code lines".ljust(24) + "".join(f"{tally.code_lines:14}" for _, tally in self.columns))
        return "\n".join([header, *rows])


@dataclass(frozen=True)
class CountTable:
    label: str
    tally: Tally

    def render(self) -> str:
        rows = [f"  {measure.family_name:<24}{self.tally[measure]:>+8}" for measure in Measure.members()]
        return "\n".join([f"change in counts ({self.label}): read these first when code was deleted as well as added",
                          *rows, f"  {'code lines':<24}{self.tally.code_lines:>+8}"])
