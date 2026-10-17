"""Information areas remain visible on light/dark cards and Peeper output."""

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from framework import __version__
from framework.integration.peeper_worker import add_information_section
from framework.views.footer import COPYRIGHT, PROJECT_URL, CardFooter
from framework.views.help import HelpPixieBackend
from framework.views.profile import ProfilePixieBackend

from .test_help import layout_model


@pytest.mark.parametrize("dark,width", [(False, 1216), (True, 916), (False, 400)])
def test_information_area_measures_wrapping_and_draws_every_line_inside_canvas(dark, width):
    pixie = pytest.importorskip("pixie")
    from easy_pixie import draw_text

    footer = CardFooter(width, dark=dark, detail="配色 #187 Decent")
    image = pixie.Image(width + 64, footer.height + 64)
    image.fill(pixie.parse_color("#14191c" if dark else "#f4f6f8"))
    with patch("easy_pixie.draw_text", wraps=draw_text) as drawing:
        assert footer.draw(image, 32, 32) == image.height - 32
    assert len(drawing.call_args_list) == 3
    texts = [call.args[1].content for call in drawing.call_args_list]
    assert texts == [f"OBot's Next ACM Bot · v{__version__}", PROJECT_URL, COPYRIGHT + " · 配色 #187 Decent"]
    for call in drawing.call_args_list:
        _, text, x, y = call.args
        assert x == 32 and y >= 60 and y + text.height <= image.height - 32


def test_help_every_page_has_readable_information_and_page_number():
    pytest.importorskip("pixie")
    from easy_pixie import draw_text

    model = layout_model((40, 2, 1))
    backend = HelpPixieBackend()
    backend.layout(model)  # Load native syntax helpers before instrumenting.
    with patch("easy_pixie.draw_text", wraps=draw_text) as drawing:
        pages = backend.render_pages(model)
    assert len(pages) > 1
    texts = [call.args[1].content for call in drawing.call_args_list]
    assert texts.count(PROJECT_URL) == len(pages)
    for index in range(1, len(pages) + 1):
        assert COPYRIGHT + f" · 配色 #187 Decent · 第 {index}/{len(pages)} 页" in texts


def test_profile_keeps_information_below_all_content():
    pytest.importorskip("pixie")
    from easy_pixie import draw_text

    import_module("framework.views._profile")
    with patch("easy_pixie.draw_text", wraps=draw_text) as drawing:
        image = ProfilePixieBackend().render(
            dict(
                platform="Codeforces",
                handle="Example",
                rating=1234,
                rank="Pupil",
                metrics=[],
                details=[],
                sections=[dict(title="最近提交", lines=["P100A · AC"])],
            ),
        )
    assert [call.args[1].content for call in drawing.call_args_list][-3:] == [
        f"OBot's Next ACM Bot · v{__version__}",
        PROJECT_URL,
        COPYRIGHT + " · 配色 #008000",
    ]
    final = drawing.call_args_list[-1].args
    assert final[3] + final[1].height < image.height - 32


def test_peeper_adapter_preserves_upstream_credit_and_uses_measured_bottom(monkeypatch):
    pixie = pytest.importorskip("pixie")
    from easy_pixie import draw_text

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "third-party/peeper-board-generator")
    )
    # Upstream returns a point before its final lines. This used to make a naive
    # wrapper overwrite credits; our extension starts after the measured area.
    original = SimpleNamespace(get_columns=lambda: 1, get_height=lambda: 280, render=Mock(return_value=40))
    generator = SimpleNamespace(
        section_copyright=original, _gradient_color=SimpleNamespace(name="#187 Decent")
    )
    add_information_section(generator)
    wrapped = generator.section_copyright
    height = wrapped.get_height()
    image = pixie.Image(1312, height + 64)
    with patch("easy_pixie.draw_text", wraps=draw_text) as drawing:
        bottom = wrapped.render(image, 96, 32)
    original.render.assert_called_once_with(image, 96, 32)
    assert bottom == 32 + height and wrapped.get_columns() == 1
    assert drawing.call_args_list[0].args[3] >= 32 + 280 + 64
    assert PROJECT_URL in [call.args[1].content for call in drawing.call_args_list]


def test_help_parameter_notation_matches_current_metadata_and_wraps():
    pytest.importorskip("pixie")
    from framework.views._help import UsageBlock

    block = UsageBlock("/board history <日期> [配置名]", 260, (221, 244, 248))
    assert len(block.lines) > 1
    spans = [span for line in block.lines for span in line]
    assert [(text.content, required) for text, _, required in spans if required is not None] == [
        ("日期", True),
        ("配置名", False),
    ]
    assert all(sum(width for _, width, _ in line) <= 260 for line in block.lines)
