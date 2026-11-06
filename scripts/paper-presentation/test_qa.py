import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
import qa


class QATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        spec = {"paper": {"title": "Test paper", "pages": 1}, "slides": [
            {"kind": "title", "title": "Paper", "subtitle": "Authors", "pages": [1]}]}
        src = self.root / "spec.json"
        src.write_text(json.dumps(spec))
        self.deck = self.root / "deck.pptx"
        subprocess.run(["node", str(Path(__file__).with_name("build.mjs")), str(src), str(self.deck)],
                       check=True, capture_output=True)

    def mutate(self, part, change):
        with zipfile.ZipFile(self.deck) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        root = ET.fromstring(parts[part])
        change(root)
        parts[part] = ET.tostring(root)
        bad = self.root / "bad.pptx"
        with zipfile.ZipFile(bad, "w") as z:
            for n, data in parts.items():
                z.writestr(n, data)
        return bad

    def test_actual_builder(self):
        self.assertEqual(qa.verify(self.deck)["slides"], 1)

    def test_no_active_slides(self):
        def change(root):
            root.find("p:sldIdLst", qa.NS).clear()
        with self.assertRaises(ValueError):
            qa.verify(self.mutate("ppt/presentation.xml", change))

    def test_missing_notes_relation(self):
        def change(root):
            for item in list(root):
                if item.attrib.get("Type", "").endswith("/notesSlide"):
                    root.remove(item)
        with self.assertRaises(ValueError):
            qa.verify(self.mutate("ppt/slides/_rels/slide1.xml.rels", change))

    def test_bounds(self):
        def change(root):
            root.find(".//p:spPr/a:xfrm/a:off", qa.NS).set("x", "-1")
        with self.assertRaises(ValueError):
            qa.verify(self.mutate("ppt/slides/slide1.xml", change))

    def test_ratio(self):
        def change(root):
            root.find("p:sldSz", qa.NS).set("cx", "10")
        with self.assertRaises(ValueError):
            qa.verify(self.mutate("ppt/presentation.xml", change))

if __name__ == "__main__":
    unittest.main()
