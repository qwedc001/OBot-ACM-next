"""Structural QA for locally generated PPTX; not a substitute for slide rendering."""
import json
import re
import posixpath
import sys
import zipfile
from xml.etree import ElementTree as ET

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

def related(z, part, suffix):
    relpart = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    root = ET.fromstring(z.read(relpart))
    result = {}
    for rel in root:
        if rel.attrib.get("Type", "").endswith("/" + suffix):
            if rel.attrib.get("TargetMode") == "External":
                raise ValueError("external structural relationship")
            target = rel.attrib["Target"]
            resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else
                                         posixpath.join(posixpath.dirname(part), target))
            if resolved not in z.namelist() or rel.attrib["Id"] in result:
                raise ValueError("invalid structural relationship")
            result[rel.attrib["Id"]] = resolved
    return result


def verify(filename):
    with zipfile.ZipFile(filename) as z:
        if len(z.infolist()) > 2000 or sum(i.file_size for i in z.infolist()) > 200_000_000:
            raise ValueError("archive exceeds limits")
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        size = pres.find("p:sldSz", NS)
        if abs(int(size.attrib["cx"]) / int(size.attrib["cy"]) - 16 / 9) > .001:
            raise ValueError("not 16:9")
        if len(set(z.namelist())) != len(z.namelist()):
            raise ValueError("duplicate ZIP parts")
        registered = pres.findall("p:sldIdLst/p:sldId", NS)
        relationships = related(z, "ppt/presentation.xml", "slide")
        rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        slides = [relationships[e.attrib[rid]] for e in registered]
        parts = {n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", n)}
        if not slides or len(set(slides)) != len(slides) or set(slides) != parts:
            raise ValueError("no active slides, duplicate registrations, or orphan slide parts")
        fonts = set()
        for name in slides:
            note_parts = list(related(z, name, "notesSlide").values())
            if len(note_parts) != 1:
                raise ValueError(f"missing or duplicate notes relationship: {name}")
            notes = ET.fromstring(z.read(note_parts[0]))
            note_text = " ".join(e.text or "" for e in notes.findall(".//a:t", NS))
            if "[Sources]" not in note_text or "PDF pages" not in note_text:
                raise ValueError(f"missing sources: {name}")
            root = ET.fromstring(z.read(name))
            if root.findall(".//p:ph", NS):
                raise ValueError(f"unexpected placeholder: {name}")
            if root.attrib.get("show", "1") == "0":
                raise ValueError(f"hidden slide: {name}")
            # Root group coordinates are zero by design, not drawable shapes.
            if root.findall(".//p:grpSp", NS):
                raise ValueError(f"unsupported grouped objects: {name}")
            for transform in root.findall(".//p:spPr/a:xfrm", NS):
                off, ext = transform.find("a:off", NS), transform.find("a:ext", NS)
                if off is not None and ext is not None:
                    for origin, length, maximum in [("x", "cx", int(size.attrib["cx"])),
                                                     ("y", "cy", int(size.attrib["cy"]))]:
                        if int(off.attrib[origin]) < 0 or int(ext.attrib[length]) <= 0 or int(off.attrib[origin]) + int(ext.attrib[length]) > maximum + 2:
                            raise ValueError(f"off-canvas: {name}")
            fonts.update(e.attrib["typeface"] for e in root.findall(".//a:latin", NS))
        if fonts != {"MiSans"}:
            raise ValueError(f"unexpected fonts: {fonts}")
        return {"slides": len(slides), "notes_with_sources": len(slides), "ratio": "16:9",
                "fonts": sorted(fonts), "off_canvas": 0, "placeholders": 0,
                "warning": "Render and inspect ALL slides; this does not measure text glyph overflow or semantic accuracy."}

if __name__ == "__main__":
    print(json.dumps(verify(sys.argv[1])))
