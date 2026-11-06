#!/usr/bin/env python3
"""Bounded, local-only PDF inspection and provenance-preserving PNG crops.

Requires PyMuPDF; its license must be reviewed before redistribution
or deployment. No network access or subprocesses are used.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any, Sequence

import pymupdf as fitz

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 200
MAX_PAGE_POINTS = 4096
MAX_INSPECT_PIXELS = 100_000_000
MAX_CROP_PIXELS = 16_000_000
INSPECT_SCALE = 2
CROP_SCALE = 4


class PDFError(ValueError):
    """Invalid input or an exceeded resource limit."""


def _local_path(value: str | os.PathLike[str]) -> Path:
    raw = os.fspath(value)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
        raise PDFError("Only local filesystem paths are accepted, not URLs or URIs")
    return Path(raw).expanduser().absolute()


def _open_pdf(value: str | os.PathLike[str]) -> tuple[Path, fitz.Document]:
    source = _local_path(value).resolve()
    if not stat.S_ISREG(source.stat().st_mode):
        raise PDFError("PDF input must be a regular local file")
    with source.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise PDFError("PDF input must be a regular local file")
        if os.fstat(stream.fileno()).st_size > MAX_PDF_BYTES:
            raise PDFError(f"PDF exceeds {MAX_PDF_BYTES} bytes (50 MiB)")
        data = stream.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise PDFError(f"PDF exceeds {MAX_PDF_BYTES} bytes (50 MiB)")
    try:
        document = fitz.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise PDFError(f"Cannot open PDF: {exc}") from exc
    try:
        # Checking the trailer also rejects encryption with an empty user
        # password, which MuPDF may automatically decrypt on opening.
        encrypted = document.xref_get_key(-1, "Encrypt")[0] != "null"
        if document.needs_pass or document.is_encrypted or encrypted:
            raise PDFError("Encrypted PDFs are not accepted")
        if not 1 <= document.page_count <= MAX_PAGES:
            raise PDFError(f"PDF must contain 1 to {MAX_PAGES} pages")
        for page in document:
            rect = page.rect
            dimensions = (rect.width, rect.height)
            if any(not math.isfinite(x) or x <= 0 or x > MAX_PAGE_POINTS for x in dimensions):
                raise PDFError(f"Page {page.number + 1} dimensions must be positive and <= {MAX_PAGE_POINTS} pt")
        return source, document
    except Exception:
        document.close()
        raise


def _pixel_dimensions(rect: fitz.Rect, scale: int) -> tuple[int, int]:
    bounds = (rect * fitz.Matrix(scale, scale)).irect
    return bounds.width, bounds.height


def _write_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def inspect_pdf(input_pdf: str | os.PathLike[str], output_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Extract text and render every page into a newly, exclusively made directory.

    Validation and pixel-budget preflight precede directory creation. An I/O or
    renderer failure can leave a partial directory, which is never reused.
    """
    source, document = _open_pdf(input_pdf)
    try:
        output = _local_path(output_dir)
        sizes = [_pixel_dimensions(page.rect, INSPECT_SCALE) for page in document]
        if sum(width * height for width, height in sizes) > MAX_INSPECT_PIXELS:
            raise PDFError(f"Inspection exceeds {MAX_INSPECT_PIXELS} total render pixels")
        output.mkdir(exist_ok=False)
        pages = []
        for page, (width, height) in zip(document, sizes):
            number = page.number + 1
            image = output / f"page-{number:04d}.png"
            text_file = output / f"page-{number:04d}.json"
            entry = {
                "page": number,
                "width": page.rect.width,
                "height": page.rect.height,
                "text": page.get_text("text"),
                "text_file": str(text_file),
                "image": str(image),
                "scale": INSPECT_SCALE,
                "render_width": width,
                "render_height": height,
            }
            pixmap = page.get_pixmap(matrix=fitz.Matrix(INSPECT_SCALE, INSPECT_SCALE), alpha=False)
            with image.open("xb") as stream:
                stream.write(pixmap.tobytes("png"))
            _write_json(text_file, entry)
            pages.append(entry)
        index = {"source": str(source), "page_count": document.page_count, "pages": pages}
        _write_json(output / "index.json", index)
        return index
    finally:
        document.close()


def crop_pdf(
    input_pdf: str | os.PathLike[str],
    page_number: int,
    left: float,
    top: float,
    right: float,
    bottom: float,
    output_png: str | os.PathLike[str],
) -> dict[str, Any]:
    """Render a contained rectangle at 4x, using one-based page numbering.

    Coordinates are PDF points in PyMuPDF's displayed (rotated) page space,
    top-left origin. Page-edge equality is allowed; clipping is never used to
    rescue an out-of-bounds rectangle. The output is exclusively created.
    """
    source, document = _open_pdf(input_pdf)
    try:
        if isinstance(page_number, bool) or not isinstance(page_number, int) or not 1 <= page_number <= document.page_count:
            raise PDFError(f"Page must be an integer from 1 to {document.page_count}")
        coordinates = (left, top, right, bottom)
        try:
            finite = all(math.isfinite(value) for value in coordinates)
        except TypeError as exc:
            raise PDFError("Rectangle coordinates must be finite numbers") from exc
        if not finite:
            raise PDFError("Rectangle coordinates must be finite numbers")
        page = document[page_number - 1]
        bounds = page.rect
        if not (bounds.x0 <= left < right <= bounds.x1 and bounds.y0 <= top < bottom <= bounds.y1):
            raise PDFError("Rectangle must have positive area and be entirely within the page")
        rectangle = fitz.Rect(*coordinates)
        width, height = _pixel_dimensions(rectangle, CROP_SCALE)
        if width <= 0 or height <= 0 or width * height > MAX_CROP_PIXELS:
            raise PDFError(f"Crop exceeds {MAX_CROP_PIXELS} render pixels or has no rendered area")
        output = _local_path(output_png)
        if output.suffix.lower() != ".png":
            raise PDFError("Crop output must have a .png extension")
        # Lexical existence is checked too, so dangling symlinks cannot be used
        # as destinations. The exclusive open below is the actual race guard.
        if os.path.lexists(output):
            raise FileExistsError(f"Output already exists: {output}")
        pixmap = page.get_pixmap(matrix=fitz.Matrix(CROP_SCALE, CROP_SCALE), clip=rectangle, alpha=False)
        png = pixmap.tobytes("png")
        with output.open("xb") as stream:
            stream.write(png)
        return {"source": str(source), "page": page_number, "rectangle": list(coordinates), "image": str(output)}
    finally:
        document.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_command = commands.add_parser("inspect", help="Extract text and 2x page PNGs into a fresh directory")
    inspect_command.add_argument("input_pdf", metavar="INPUT.pdf")
    inspect_command.add_argument("output_dir", metavar="OUTPUT_DIR")
    crop_command = commands.add_parser("crop", help="Create an exclusive 4x PNG and print its source ledger")
    crop_command.add_argument("input_pdf", metavar="INPUT.pdf")
    crop_command.add_argument("page", type=int, metavar="PAGE")
    for name in ("left", "top", "right", "bottom"):
        crop_command.add_argument(name, type=float, metavar=name[0].upper())
    crop_command.add_argument("output_png", metavar="OUTPUT.png")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_pdf(args.input_pdf, args.output_dir)
        else:
            result = crop_pdf(args.input_pdf, args.page, args.left, args.top, args.right, args.bottom, args.output_png)
    except (PDFError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
