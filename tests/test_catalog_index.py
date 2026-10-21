"""README.md's file list and pattern index are the rung files' headings, never a hand edit."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from audit.catalog import Catalog


class CatalogIndex(unittest.TestCase):
    def test_readme_is_generated_from_rung_headings(self):
        catalog = Catalog()
        self.assertEqual(catalog.readme.read_text(encoding="utf-8"), catalog.rendered())

    def test_every_rung_file_is_declared(self):
        catalog = Catalog()
        on_disk = {path.name for path in catalog.root.glob("*.md")} - {catalog.readme.name}
        self.assertEqual(on_disk, set(catalog.rungs))


if __name__ == "__main__":
    unittest.main()
