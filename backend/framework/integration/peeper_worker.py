"""One render process imports the pinned submodule; no network or credentials."""

import contextlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ..paths import PROJECT_ROOT

MAX_RENDER_PIXELS = 64_000_000
MAX_OUTPUT_PIXELS = 24_000_000
MAX_OUTPUT_EDGE = 8192


class RenderTooLarge(Exception):
    pass


def add_information_section(generator):
    """Extend the upstream copyright section, without patching the submodule.

    Peeper measures and draws this object via its public section interface.
    Keeping its original section preserves generator credits and data notices;
    measuring the additional area also includes it in canvas/pixel limits.
    This runs only in the isolated drawing process, before output downscaling.
    """
    from module.board import misc

    from ..views.footer import CardFooter

    original = generator.section_copyright
    footer = CardFooter(
        misc._CONTENT_WIDTH - 2 * misc._SIDE_PADDING,
        detail="配色 " + generator._gradient_color.name,
    )

    class CopyrightWithInformation:
        def get_columns(self):
            return original.get_columns()

        def get_height(self):
            return original.get_height() + 64 + footer.height

        def render(self, image, x, y):
            # Upstream's return value stops before its last information line;
            # use its measured extent so our divider cannot cover that line.
            original.render(image, x, y)
            return footer.draw(image, x, y + original.get_height() + 64)

    generator.section_copyright = CopyrightWithInformation()


def render_image(generator):
    """Preflight the pinned renderer's canvas, then scale without dropping rows."""
    from module.board import misc

    sections = [generator.section_title, generator.section_content, generator.section_copyright]
    columns = max(section.get_columns() for section in sections)
    width = misc._CONTENT_WIDTH * columns + misc._COLUMN_PADDING * (columns - 1) + 64
    height = (
        sum(section.get_height() for section in sections)
        + misc._SECTION_PADDING * (len(sections) - 1)
        + misc._TOP_PADDING
        + misc._BOTTOM_PADDING
        + 64
    )
    if width <= 0 or height <= 0 or width * height > MAX_RENDER_PIXELS:
        raise RenderTooLarge
    image = generator.render()
    scale = min(
        1,
        MAX_OUTPUT_EDGE / max(image.width, image.height),
        math.sqrt(MAX_OUTPUT_PIXELS / (image.width * image.height)),
    )
    if scale < 1:
        image = image.resize(max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image


def install_clock(timezone):
    """Keep the upstream renderer's date helpers explicit, also on Windows."""
    from module import submission, utils
    from module.board import misc, model

    zone = ZoneInfo(timezone)

    class BoardDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz or zone)

        @classmethod
        def fromtimestamp(cls, timestamp, tz=None):
            return super().fromtimestamp(timestamp, tz or zone)

        @classmethod
        def strptime(cls, value, format):
            return super().strptime(value, format).replace(tzinfo=zone)

    utils.datetime = misc.datetime = model.datetime = BoardDatetime
    submission.time = SimpleNamespace(localtime=lambda value: datetime.fromtimestamp(value, zone).timetuple())


def main():
    source = PROJECT_ROOT / "third-party" / "peeper-board-generator"
    sys.path.insert(0, str(source))
    model = json.load(sys.stdin)
    os.environ["TZ"] = model["timezone"]
    if hasattr(time, "tzset"):
        time.tzset()
    from module.board.misc import MiscBoardGenerator
    from module.config import Config

    install_clock(model["timezone"])

    with tempfile.TemporaryDirectory(prefix="obot-peeper-") as temporary:
        root = Path(temporary)
        (root / "data").mkdir()
        shutil.copyfile(source / "data" / "tips.json", root / "data" / "tips.json")
        for snapshot in model["snapshots"]:
            path = root / "data" / f"board-{snapshot['day']}.json"
            path.write_text(json.dumps(snapshot["payload"], ensure_ascii=False), encoding="utf-8")
            os.utime(path, (snapshot["captured_at"], snapshot["captured_at"]))
        config = Config(
            str(root),
            {
                "id": "board",
                "handler": "Hydro",
                "board_name": model["name"],
                "show_unrated": model.get("show_unrated", True),
            },
        )
        with contextlib.redirect_stdout(sys.stderr):
            generator = MiscBoardGenerator(
                config,
                model["mode"],
                str(source / "data" / "logo.png"),
                verdict=model["verdict"],
                separate_columns=True,
                date_string=model["day"],
            )
            add_information_section(generator)
            image = render_image(generator)
            path = root / "output.png"
            image.write_file(str(path))
        sys.stdout.buffer.write(path.read_bytes())


if __name__ == "__main__":
    try:
        main()
    except RenderTooLarge:
        sys.stdout.write(json.dumps({"error": "image_too_large"}))
        sys.exit(2)
    except Exception as error:
        sys.stdout.write(json.dumps({"error": "render_failed", "exception": type(error).__name__}))
        sys.exit(1)
