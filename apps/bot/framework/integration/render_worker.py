"""One isolated drawing job. This module never initializes NoneBot or reads env files.

The pickle is an internal parent-created file in a private temporary directory,
not a web/upload format. Only registered, importable backends cross this boundary.
Models may include avatar/thumbnail bytes, so JSON would require extra encoding.
No native Pixie object, user identity, database session or credential is passed.
"""

import json
import pickle
import sys
from pathlib import Path


def main(directory):
    work = Path(directory)
    try:
        with (work / "input.pickle").open("rb") as handle:
            backend, model, multiple, max_bytes = pickle.load(handle)
        images = (
            backend.render_pages(model)
            if multiple and hasattr(backend, "render_pages")
            else (backend.render(model),)
        )
        pages = []
        for index, image in enumerate(images):
            if not image.data or len(image.data) > max_bytes:
                raise ValueError("Image exceeds artifact budget")
            (work / f"{index}.png").write_bytes(image.data)
            pages.append({"mime": image.mime, "width": image.width, "height": image.height})
        if not pages:
            raise ValueError("Renderer returned no pages")
        result = {"pages": pages}
    except (ImportError, OSError, ValueError) as error:
        # Propagate only a known error category; text/models can contain private data.
        result = {
            "error": "ImportError"
            if isinstance(error, ImportError)
            else "OSError"
            if isinstance(error, OSError)
            else "ValueError"
        }
    (work / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1])
