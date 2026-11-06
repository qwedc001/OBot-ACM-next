"""Generated-PDF-only tests; run with unittest discovery in this directory."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

_SPEC = importlib.util.spec_from_file_location("paper_presentation_pdf", Path(__file__).with_name("pdf.py"))
pdf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pdf)


class PDFTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.make_pdf("paper.pdf", pages=2)

    def make_pdf(self, name, *, pages=1, width=200, height=300, rotation=0, user_password=None):
        path = self.root / name
        with fitz.open() as document:
            for index in range(pages):
                page = document.new_page(width=width, height=height)
                page.insert_text((20, 30), f"Generated page {index + 1}")
                page.draw_rect(fitz.Rect(30, 40, 80, 90), color=(1, 0, 0), fill=(1, 0, 0))
                page.set_rotation(rotation)
            options = {}
            if user_password is not None:
                options = dict(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner-secret", user_pw=user_password)
            document.save(path, **options)
        return path

    def test_inspect_text_images_and_index(self):
        output = self.root / "fresh output"
        index = pdf.inspect_pdf(self.source, output)
        self.assertEqual(index["source"], str(self.source.resolve()))
        self.assertEqual(index["page_count"], 2)
        self.assertEqual(json.loads((output / "index.json").read_text()), index)
        self.assertEqual(len(list(output.iterdir())), 5)
        for number, entry in enumerate(index["pages"], 1):
            self.assertEqual(entry["page"], number)
            self.assertIn(f"Generated page {number}", entry["text"])
            self.assertEqual(json.loads(Path(entry["text_file"]).read_text()), entry)
            pixmap = fitz.Pixmap(entry["image"])
            self.assertEqual((pixmap.width, pixmap.height), (400, 600))
            self.assertEqual((entry["render_width"], entry["render_height"]), (400, 600))

    def test_inspect_exclusive_directory_even_if_empty(self):
        output = self.root / "existing"
        output.mkdir()
        with self.assertRaises(FileExistsError):
            pdf.inspect_pdf(self.source, output)
        self.assertEqual(list(output.iterdir()), [])

    def test_crop_png_dimensions_and_ledger(self):
        output = self.root / "figure.png"
        ledger = pdf.crop_pdf(self.source, 2, 10, 20, 50, 70, output)
        self.assertEqual(ledger, {"source": str(self.source.resolve()), "page": 2,
                                  "rectangle": [10, 20, 50, 70], "image": str(output)})
        self.assertTrue(output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        pixmap = fitz.Pixmap(str(output))
        self.assertEqual((pixmap.width, pixmap.height), (160, 200))

    def test_crop_all_page_edges_allowed(self):
        output = self.root / "full.png"
        pdf.crop_pdf(self.source, 1, 0, 0, 200, 300, output)
        self.assertEqual((fitz.Pixmap(str(output)).width, fitz.Pixmap(str(output)).height), (800, 1200))

    def test_rotated_page_uses_display_coordinates(self):
        source = self.make_pdf("rotated.pdf", rotation=90)
        output = self.root / "rotated-output.png"
        pdf.crop_pdf(source, 1, 250, 0, 300, 100, output)
        pixmap = fitz.Pixmap(str(output))
        self.assertEqual((pixmap.width, pixmap.height), (200, 400))

    def test_invalid_pages(self):
        for number in (0, -1, 3, True, 1.5):
            with self.subTest(number=number), self.assertRaises(pdf.PDFError):
                pdf.crop_pdf(self.source, number, 0, 0, 20, 20, self.root / "bad.png")
        self.assertFalse((self.root / "bad.png").exists())

    def test_invalid_rectangles(self):
        rectangles = [(-1, 0, 20, 20), (0, -1, 20, 20), (0, 0, 201, 20),
                      (0, 0, 20, 301), (20, 0, 10, 20), (0, 20, 20, 10),
                      (0, 0, 0, 20), (0, 0, 20, 0), (float("nan"), 0, 20, 20),
                      (0, 0, float("inf"), 20), (0, float("-inf"), 20, 20),
                      ("bad", 0, 20, 20)]
        for rectangle in rectangles:
            with self.subTest(rectangle=rectangle), self.assertRaises(pdf.PDFError):
                pdf.crop_pdf(self.source, 1, *rectangle, self.root / "bad.png")
        self.assertFalse((self.root / "bad.png").exists())

    def test_crop_never_overwrites(self):
        output = self.root / "existing.png"
        output.write_bytes(b"preserve me")
        with self.assertRaises(FileExistsError):
            pdf.crop_pdf(self.source, 1, 0, 0, 20, 20, output)
        self.assertEqual(output.read_bytes(), b"preserve me")

    def test_dangling_output_symlinks_rejected(self):
        target = self.root / "absent.png"
        output = self.root / "link.png"
        output.symlink_to(target)
        with self.assertRaises(FileExistsError):
            pdf.crop_pdf(self.source, 1, 0, 0, 20, 20, output)
        self.assertFalse(target.exists())
        directory = self.root / "link-dir"
        directory.symlink_to(self.root / "absent-dir")
        with self.assertRaises(FileExistsError):
            pdf.inspect_pdf(self.source, directory)
        self.assertFalse((self.root / "absent-dir").exists())

    def test_encrypted_pdf_rejected_including_empty_password(self):
        for password in ("secret", ""):
            source = self.make_pdf(f"encrypted-{len(password)}.pdf", user_password=password)
            with self.subTest(password=password):
                with self.assertRaisesRegex(pdf.PDFError, "Encrypted"):
                    pdf.inspect_pdf(source, self.root / "encrypted-output")
                with self.assertRaisesRegex(pdf.PDFError, "Encrypted"):
                    pdf.crop_pdf(source, 1, 0, 0, 20, 20, self.root / "encrypted.png")
        self.assertFalse((self.root / "encrypted-output").exists())

    def test_invalid_and_nonregular_input(self):
        bad = self.root / "bad.pdf"
        bad.write_bytes(b"not a PDF")
        with self.assertRaises(pdf.PDFError):
            pdf.inspect_pdf(bad, self.root / "bad-output")
        with self.assertRaises(pdf.PDFError):
            pdf.inspect_pdf(self.root, self.root / "directory-output")
        with self.assertRaises(FileNotFoundError):
            pdf.inspect_pdf(self.root / "missing.pdf", self.root / "missing-output")

    def test_input_byte_limit(self):
        source = self.make_pdf("large.pdf")
        with source.open("r+b") as stream:
            stream.truncate(pdf.MAX_PDF_BYTES + 1)
        with self.assertRaisesRegex(pdf.PDFError, "50 MiB"):
            pdf.inspect_pdf(source, self.root / "oversized")
        self.assertFalse((self.root / "oversized").exists())

    def test_page_count_limit(self):
        source = self.make_pdf("many-pages.pdf", pages=201)
        with self.assertRaisesRegex(pdf.PDFError, "200 pages"):
            pdf.inspect_pdf(source, self.root / "many-output")

    def test_dimension_limit(self):
        for width, height in ((4097, 200), (200, 4097)):
            source = self.make_pdf(f"dimensions-{width}.pdf", width=width, height=height)
            with self.assertRaisesRegex(pdf.PDFError, "4096 pt"):
                pdf.inspect_pdf(source, self.root / "huge-output")

    def test_inspect_total_pixel_preflight(self):
        with patch.object(pdf, "MAX_INSPECT_PIXELS", 479_999):
            with self.assertRaisesRegex(pdf.PDFError, "total render pixels"):
                pdf.inspect_pdf(self.source, self.root / "pixel-output")
        self.assertFalse((self.root / "pixel-output").exists())

    def test_crop_pixel_limit_and_fractional_rounding(self):
        source = self.make_pdf("big-page.pdf", width=2000, height=2000)
        # Geometric area is below 16M; outward pixel rounding is above it.
        with self.assertRaisesRegex(pdf.PDFError, "16000000"):
            pdf.crop_pdf(source, 1, 0.1, 0.1, 1000.05, 1000.05, self.root / "too-big.png")
        self.assertFalse((self.root / "too-big.png").exists())

    def test_remote_paths_and_wrong_output_format(self):
        for remote in ("https://example.test/paper.pdf", "file:///tmp/paper.pdf", "ftp://example.test/paper.pdf"):
            with self.subTest(remote=remote), self.assertRaisesRegex(pdf.PDFError, "local"):
                pdf.inspect_pdf(remote, self.root / "remote-output")
        with self.assertRaisesRegex(pdf.PDFError, "local"):
            pdf.crop_pdf(self.source, 1, 0, 0, 20, 20, "https://example.test/output.png")
        with self.assertRaisesRegex(pdf.PDFError, ".png"):
            pdf.crop_pdf(self.source, 1, 0, 0, 20, 20, self.root / "wrong.jpg")

    def test_crop_cli_stdout_is_json_ledger(self):
        output = self.root / "cli.png"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = pdf.main(["crop", str(self.source), "1", "10", "20", "50", "70", str(output)])
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(json.loads(stdout.getvalue()), {"source": str(self.source.resolve()), "page": 1,
                                                        "rectangle": [10, 20, 50, 70], "image": str(output)})

    def test_cli_inspect_and_failure(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = pdf.main(["inspect", str(self.source), str(self.root / "cli-inspect")])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["page_count"], 2)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = pdf.main(["crop", str(self.source), "0", "0", "0", "10", "10", str(self.root / "bad-cli.png")])
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("error:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
