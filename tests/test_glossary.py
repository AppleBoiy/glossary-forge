import unittest
from common import Project, load_script

gf = load_script("glossary")


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        self.p = Project("mods")

    def tearDown(self):
        self.p.close()

    def test_mine_and_check(self):
        code, out = self.p.run(gf, "mine", "--min-count", "2")
        self.assertEqual(code, 0, out)
        rows = gf.load_glossary(self.p.root / "l10n/glossary.csv")
        pairs = {r["source"]: r["target"] for r in rows}
        self.assertEqual(pairs.get("Moon Pear"), "ลูกแพร์จันทรา")      # from the name key
        self.assertTrue(all(r["status"] == "candidate" for r in rows))
        code, out = self.p.run(gf, "check")
        self.assertIn("Moon Pear", out)                               # "ต้นแพร์" in Mira.Rain is inconsistent
        code, out = self.p.run(gf, "report")
        self.assertTrue((self.p.root / "l10n/glossary.html").exists())

    def test_export_tbx(self):
        self.p.run(gf, "mine", "--min-count", "2")
        self.p.run(gf, "add", "Moon Pear", "ลูกแพร์จันทรา", "--type", "item")
        code, out = self.p.run(gf, "export", "--format", "tbx")
        tbx = (self.p.root / "l10n/glossary.tbx").read_text()
        self.assertIn("<term>Moon Pear</term>", tbx)
        self.assertIn("ลูกแพร์จันทรา", tbx)


if __name__ == "__main__":
    unittest.main()
