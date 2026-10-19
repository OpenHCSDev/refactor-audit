"""Preserved synced-skill checks run through the existing Check family."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit.plan_package import Check, Document, HedgeCheck, NeedsReview, Passed, PlanPackage


class PlanPackageChecks(unittest.TestCase):
    def test_hedges_are_review_leads_not_automatic_failures(self):
        package = PlanPackage(Path("unused"), (
            Document("README.md", "Not independently verified. Scope is stated here."),
            Document("S1-owner.md", "The owner stores the record.\nA provisional finding.\nDo not claim this proves latency."),
        ))
        self.assertIn(HedgeCheck, Check.members())
        result = HedgeCheck().run(package)
        self.assertIsInstance(result, NeedsReview)
        self.assertFalse(result.fails)
        self.assertEqual(result.lines, (
            "S1-owner.md:2: A provisional finding.",
            "S1-owner.md:3: Do not claim this proves latency.",
        ))
        clean = PlanPackage(Path("unused"), package.documents[:1])
        self.assertIsInstance(HedgeCheck().run(clean), Passed)
