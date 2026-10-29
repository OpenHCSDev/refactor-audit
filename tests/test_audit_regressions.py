"""Behavioral regressions for the audit's measurements and review boundary.

Run with: python -m unittest discover -s skills/refactor-audit/tests -v
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from audit.chain_terms import ChainProfile, TermKind, TypeTerm, PredicateTerm
from audit.dispatch import (
    BuiltinHandlerTypeSwitch, StringDispatch, StringDispatchArms, TypeSwitch, TypeSwitchArms,
)
from audit.census import CensusRun
from audit.measures import ForeignNegation, Measure, Measured, NodeCount
from audit.source_measures import FamilyFlattened, ForeignAbsenceProbe
from audit.repository import Repository
from merge_review import MergeReview

# A repository that declares no project: the census measures every module of it.
CENSUS = CensusRun(Repository(SCRIPTS))


def source_for(cases: int, *, type_switch: bool = False) -> str:
    tests = (f"isinstance(subject, Kind{i})" if type_switch else f"subject == 'case{i}'"
             for i in range(cases))
    return "def choose(subject):\n" + "".join(f"    if {test}: return True\n" for test in tests)


class AuditRegressions(unittest.TestCase):
    def test_negation_is_not_absence_and_explicit_probes_remain_visible(self):
        measured = CENSUS.read("editing.py", """
class EditingMode:
    accepts_text = True
def read(editor, unknown):
    return (not editor.editing_mode.accepts_text, not unknown.flag,
            editor.value is None, editor.value is not None)
""")
        self.assertIsInstance(measured, Measured)
        self.assertEqual(measured.tally[ForeignAbsenceProbe], 2)
        self.assertEqual(measured.tally[ForeignNegation], 2)
        self.assertEqual(ForeignNegation.weight, 0)
        for expression in ("not editor.editing_mode.accepts_text", "not unknown.flag"):
            with self.subTest(expression=expression):
                self.assertIsNone(ForeignAbsenceProbe.attribute(ast.parse(expression, mode="eval").body))
        own = CENSUS.read("owned.py", "def read(self):\n    return not self.flag, self.value is None\n")
        self.assertEqual(own.tally[ForeignAbsenceProbe], 0)
        self.assertEqual(own.tally[ForeignNegation], 0)

    def test_family_boundary_tracks_real_event_growth_and_member_transport(self):
        fixture = Path(__file__).parent / "fixtures" / "family-boundary"
        provenance = json.loads((fixture / "provenance.json").read_text())
        tallies = []
        for state in ("before", "after"):
            source = (fixture / f"{state}.py").read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(),
                             provenance["sources"][state]["fragment_sha256"])
            outcome = CENSUS.read(provenance["path"], source.decode())
            self.assertIsInstance(outcome, Measured)
            tallies.append(outcome.tally)
        self.assertEqual([t[FamilyFlattened] for t in tallies], [0, 1])
        self.assertEqual((tallies[1] - tallies[0])[FamilyFlattened], 1)
        self.assertEqual((tallies[0] - tallies[1])[FamilyFlattened], -1)
        self.assertEqual(tallies[1].as_record(Measure.members_with(NodeCount))["family_flattened"], 1)

        for body, count in (
            ("emit(member.declared_name)", 1),
            ("emit(member, phase=member.family_name)", 1),
            ("return member.declared_name or other.family_name", 2),
            ("return (member.declared_name, other.family_name)", 2),
            ('emit(f"{member.declared_name}")', 0),
            ("emit(member); return member", 0),
        ):
            with self.subTest(body=body):
                source = "def carry(member, other):\n    " + body + "\n"
                self.assertEqual(CENSUS.read("source.py", source).tally[FamilyFlattened], count)

    def test_family_identity_and_selector_from_real_history(self):
        fixture = Path(__file__).parent / "fixtures" / "wire-identity"
        provenance = json.loads((fixture / "provenance.json").read_text())
        for case in provenance["cases"]:
            with self.subTest(revision=case["revision"], owner=case["class"]):
                source = (fixture / case["fixture"]).read_text()
                self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), case["sha256"])
                measured = CENSUS.read(case["path"], source)
                self.assertEqual(measured.tally[FamilyFlattened], case["expected"])
                if case["fixture"] == "send-selector.py":
                    sites = FamilyFlattened.flattened(ast.parse(source))
                    self.assertEqual([ast.unparse(site) for site in sites], ["self.name == SEND_TOOL"])

    def test_identity_projection_is_not_string_redispatch(self):
        for header, decorator in (("", "property"),
                                  ("from builtins import property as identity", "identity"),
                                  ("import builtins as native", "native.property")):
            source = header + f'''
SEND_TOOL = "comms_send"
class CodingCall:
    @{decorator}
    def name(this) -> str:
        """Wire identity derived from the original declaration."""
        return this.tool.declared_name
'''
            with self.subTest(decorator=decorator):
                self.assertEqual(CENSUS.read("arbitrary.py", source).tally[FamilyFlattened], 0)
                compared = source + '''
def execute(call):
    if call.name == SEND_TOOL:
        return "special behavior"
'''
                sites = FamilyFlattened.flattened(ast.parse(compared))
                self.assertEqual(len(sites), 1)
                self.assertIsInstance(sites[0], ast.Compare)
                self.assertEqual(CENSUS.read("arbitrary.py", compared).tally[FamilyFlattened], 1)
                literal = compared.replace("call.name == SEND_TOOL", 'call.name == "comms_send"')
                self.assertEqual(CENSUS.read("arbitrary.py", literal).tally[FamilyFlattened], 1)

    def test_projection_admission_does_not_hide_other_name_transport(self):
        for body, expected in (("return this.tool.declared_name", 0),
                               ("return this.tool.declared_name or fallback", 1),
                               ("return (this.tool.declared_name,)", 1),
                               ("return other.declared_name", 1),
                               ("emit(this.tool.declared_name)\\n        return this.tool.declared_name", 2)):
            with self.subTest(body=body):
                source = "class Call:\n    @property\n    def name(this):\n        " + body.replace("\\n", "\n") + "\n"
                self.assertEqual(CENSUS.read("arbitrary.py", source).tally[FamilyFlattened], expected)
        shadowed = "property = factory\nclass Call:\n    @property\n    def name(this):\n        return this.tool.declared_name\n"
        self.assertEqual(CENSUS.read("arbitrary.py", shadowed).tally[FamilyFlattened], 1)
        local = 'SEND_TOOL = "comms_send"\ndef read(SEND_TOOL, value):\n    return value == SEND_TOOL\n'
        self.assertEqual(CENSUS.read("arbitrary.py", local).tally[FamilyFlattened], 0)
        numeric = 'LIMIT = 10\ndef read(value):\n    return value == LIMIT\n'
        self.assertEqual(CENSUS.read("arbitrary.py", numeric).tally[FamilyFlattened], 0)
        for expression in ('namespace["declared_name"] == ""',
                           'value.declared_name == ""',
                           'value.name != expected_name',
                           'SEND_TOOL == "comms_send"'):
            with self.subTest(validation=expression):
                source = 'SEND_TOOL = "comms_send"\ndef validate(value, namespace, expected_name):\n    return ' + expression + '\n'
                self.assertEqual(CENSUS.read("arbitrary.py", source).tally[FamilyFlattened], 0)

    def test_builtin_handler_family_tracks_real_421_growth_and_boundary(self):
        fixture = Path(__file__).parent / "fixtures" / "pr421"
        provenance = json.loads((fixture / "provenance.json").read_text())
        tallies = []
        for state in ("before", "after"):
            source = (fixture / f"{state}.py").read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(),
                             provenance["sources"][state]["sha256"])
            measured = CENSUS.read(provenance["sources"][state]["path"], source.decode())
            self.assertIsInstance(measured, Measured)
            tallies.append(measured.tally)
        self.assertEqual([t[BuiltinHandlerTypeSwitch] for t in tallies], [0, 6])
        self.assertEqual((tallies[1] - tallies[0])[BuiltinHandlerTypeSwitch], 6)

        source = '''from .mro_dispatch import handles as cases, MroDispatch
import builtins as native
from builtins import list as Sequence
class Decode(MroDispatch):
    @cases(native.dict, Sequence, str, int, float, bool, tuple)
    def primitive(self, value): pass
    @cases(DomainRecord, ast.Dict)
    async def domain(self, value): pass
'''
        for path, count in (("src/pkg/presentation.py", 7),
                            ("src/pkg/field_codec.py", 0),
                            ("src/pkg/presentation_codec.py", 7)):
            with self.subTest(path=path):
                self.assertEqual(CENSUS.read(path, source).tally[BuiltinHandlerTypeSwitch], count)
        shadowed = source.replace('class Decode', 'dict = DomainRecord\nclass Decode')
        shadowed = shadowed.replace('native.dict', 'dict')
        self.assertEqual(CENSUS.read("source.py", shadowed).tally[BuiltinHandlerTypeSwitch], 6)
        split = '''from .mro_dispatch import handles
class Consumer(MroDispatch):
    @handles(dict)
    def one(self, value): pass
    @handles(list)
    def two(self, value): pass
    @handles(str)
    def three(self, value): pass
'''
        self.assertEqual(CENSUS.read("source.py", split).tally[BuiltinHandlerTypeSwitch], 3)
        self.assertEqual(CENSUS.read("source.py", split.replace('@handles(str)', '@handles(tuple)')).tally[BuiltinHandlerTypeSwitch], 3)
        self.assertEqual(CENSUS.read("source.py", split.replace('@handles(str)', '@handles(str, tuple)')).tally[BuiltinHandlerTypeSwitch], 4)

    def test_dispatch_families_track_growth_removal_and_scope(self):
        for subjects, arms, is_type in ((StringDispatch, StringDispatchArms, False),
                                        (TypeSwitch, TypeSwitchArms, True)):
            with self.subTest(family=subjects.family_name):
                outcomes = [CENSUS.read("example.py", source_for(n, type_switch=is_type))
                            for n in (2, 3, 4)]
                for outcome in outcomes:
                    self.assertIsInstance(outcome, Measured)
                tallies = [outcome.tally for outcome in outcomes]
                self.assertEqual([t[subjects] for t in tallies], [0, 1, 1])
                self.assertEqual([t[arms] for t in tallies], [0, 3, 4])
                self.assertEqual((tallies[2] - tallies[1])[arms], 1)
                self.assertEqual((tallies[0] - tallies[2])[arms], -4)
                repeated = source_for(3, type_switch=is_type) + source_for(1, type_switch=is_type).split("\n", 1)[1]
                self.assertEqual(CENSUS.read("repeated.py", repeated).tally[arms], 3)
                nested = ("def outer(subject):\n    if subject == 'outer': return True\n" +
                          "    " + source_for(3, type_switch=is_type).replace("\n", "\n    ").rstrip() + "\n")
                measured = CENSUS.read("nested.py", nested)
                self.assertIsInstance(measured, Measured)
                self.assertEqual(measured.tally[subjects], 1)
                self.assertEqual(measured.tally[arms], 3)

    def test_chain_profiles_leave_owner_predicates_open(self):
        specimens = (
            ("self.ready and self.running and self.connected and self.current", "flag", "IMPL-10"),
            ("self.is_ready() and self.has_permission() and self.within_budget() and self.is_current()",
             "predicate", "OPEN: inspect predicate ownership"),
            ("not self.is_ready() or not self.is_current() or other.is_ready() or within_budget()",
             "predicate", "OPEN: inspect predicate ownership"),
            ("isinstance(x, A) and isinstance(y, B) and isinstance(z, C) and isinstance(w, D)",
             "type", "BOUND-1"),
        )
        for expression, kind, lead in specimens:
            with self.subTest(expression=expression):
                profile = ChainProfile.of(ast.parse(expression, mode="eval").body)
                self.assertEqual(profile.kinds, ((kind, 4),))
                self.assertEqual(profile.lead, lead)

    def test_term_kinds_share_one_module_and_ties_follow_declarations(self):
        self.assertEqual({kind.__module__ for kind in TermKind.members()}, {TermKind.__module__})
        self.assertLess(TypeTerm.declared_position(), PredicateTerm.declared_position())
        profile = ChainProfile.of(ast.parse('ready() and isinstance(value, str)', mode='eval').body)
        self.assertEqual(profile.kinds, (('type', 1), ('predicate', 1)))

    def test_merge_review_fails_closed_and_reports_valid_growth(self):
        valid = source_for(3)
        for before, after, expected_status in ((valid, "def broken(:\n", 2),
                                                ("def broken(:\n", valid, 2),
                                                (valid, source_for(4), 0)):
            with self.subTest(status=expected_status, before=before), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                # Isolate git identity and hooks from the caller's configuration.
                env = os.environ | {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                                    "GIT_AUTHOR_NAME": "Audit test", "GIT_AUTHOR_EMAIL": "audit@example.invalid",
                                    "GIT_COMMITTER_NAME": "Audit test", "GIT_COMMITTER_EMAIL": "audit@example.invalid"}

                def git(*args: str) -> str:
                    return subprocess.check_output(["git", "-C", directory, *args], env=env,
                                                   stderr=subprocess.STDOUT, text=True).strip()

                git("init", "-q")
                git("config", "core.hooksPath", str(path / "no-hooks"))
                (path / "pkg").mkdir()
                specimen = path / "pkg" / "example.py"
                specimen.write_text(before)
                git("add", ".")
                git("commit", "-qm", "Initial")
                base = git("rev-parse", "HEAD")
                git("checkout", "-qb", "feature")
                specimen.write_text(after)
                git("add", ".")
                git("commit", "-qm", "Change specimen")
                git("checkout", "-q", "--detach", base)
                git("merge", "--no-ff", "-qm", "Merge pull request #1 from test/feature", "feature")
                review = MergeReview.of(CensusRun(Repository(path)), "HEAD", 0, "Test", "pkg")
                result = subprocess.run([sys.executable, str(SCRIPTS / "merge_review.py"),
                                         "--repo", directory, "--root", "pkg", "--since", base, "--rev", "HEAD"],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                if expected_status:
                    self.assertEqual(len(review.unparsed), 1)
                    self.assertEqual(review.unparsed[0].path, "pkg/example.py")
                    self.assertIn("WARNING", result.stderr)
                    self.assertIn("pkg/example.py", result.stderr)
                    self.assertIn("^1..", result.stderr)
                    self.assertIn("rankings withheld", result.stderr)
                    self.assertEqual(result.stdout, "")
                else:
                    self.assertEqual(review.unparsed, ())
                    self.assertEqual(review.change[StringDispatch], 0)
                    self.assertEqual(review.change[StringDispatchArms], 1)
                    self.assertEqual(result.stderr, "")
                    self.assertIn("string_dispatch_arms +1", result.stdout)
                    self.assertIn("verify ownership", result.stdout)


if __name__ == "__main__":
    unittest.main()
