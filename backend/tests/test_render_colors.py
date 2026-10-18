"""Colour selection stays consistent through measurement, drawing and metadata."""

from dataclasses import asdict
from importlib import import_module
from unittest.mock import Mock, patch

import pytest

from .test_contests import NOW, SAMPLE
from .test_help import layout_model


@pytest.fixture
def gradients():
    pytest.importorskip("pixie")
    from easy_pixie import GradientColor

    return (
        GradientColor(["#4568DC", "#B06AB3"], [0.0, 1.0], "#42 First test gradient"),
        GradientColor(["#FF512F", "#F09819", "#008080"], [0.0, 0.5, 1.0], "#43 Second test gradient"),
    )


@pytest.mark.parametrize("detail", [False, True])
def test_contest_reply_draws_and_labels_the_same_random_gradient(detail, gradients, monkeypatch):
    import easy_pixie

    from framework.views.contests import ContestBackend
    from framework.views.footer import CardFooter
    from framework.views.renderer.pixie import model as source

    # Load source modules before replacing the package export: their module-
    # level imports must not retain a test double after monkeypatch restores it.
    import_module("framework.views.renderer.bindings")
    picker = Mock(side_effect=gradients)
    monkeypatch.setattr(easy_pixie, "pick_gradient_color", picker)
    footers = []
    draw_footer = CardFooter.draw

    def record_footer(self, *args):
        footers.append(self.blocks[-1].content)
        return draw_footer(self, *args)

    monkeypatch.setattr(CardFooter, "draw", record_footer)
    painter = easy_pixie.draw_gradient_rect
    model = dict(title="比赛", now=NOW, items=[asdict(SAMPLE)], detail=detail)
    with (
        patch.object(source, "draw_gradient_rect", wraps=painter) as list_paint,
        patch.object(easy_pixie, "draw_gradient_rect", wraps=painter) as detail_paint,
    ):
        images = [ContestBackend().render(model) for _ in gradients]
    painted = detail_paint if detail else list_paint
    assert picker.call_count == 2  # no extra draws while measuring a reply
    assert [call.args[2] for call in painted.call_args_list] == list(gradients)
    assert all(
        footer.endswith("配色 " + gradient.name) for footer, gradient in zip(footers, gradients, strict=True)
    )
    assert images[0].data != images[1].data
    assert "_gradient" not in model  # input snapshots remain serializable


def test_help_filters_bright_gradient_and_reuses_accepted_accent_on_all_pages(gradients, monkeypatch):
    from easy_pixie import GradientColor, hex_to_color, lighten_color

    from framework.views.help import HelpPixieBackend
    from framework.views.renderer.pixie import render_help as source

    bright = GradientColor(["#000000", "#FFFFFF"], [0.0, 1.0], "#1 Too bright")
    chosen = gradients[0]
    picker = Mock(side_effect=[bright, chosen])
    monkeypatch.setattr(source, "pick_gradient_color", picker)
    monkeypatch.setattr("framework.views.help.MAX_HEIGHT", 2200)
    pages = HelpPixieBackend().layout(layout_model((24, 2, 1)))
    assert len(pages) > 1 and picker.call_count == 2
    accent = lighten_color(hex_to_color(chosen.color_list[-1]), source._ACCENT_LIGHT_RATIO)
    for index, page in enumerate(pages, 1):
        assert page._gradient_color is chosen
        assert (
            page.sections[-1]
            .footer.blocks[-1]
            .content.endswith(f"配色 {chosen.name} · 第 {index}/{len(pages)} 页")
        )
        for category in page.categories:
            assert (category._chip_color.r, category._chip_color.g, category._chip_color.b) == pytest.approx(
                (accent.r, accent.g, accent.b)
            )


@pytest.mark.parametrize("view", ["profile", "pickone", "pickone-preview"])
def test_profile_and_pickone_visual_colours_do_not_follow_random_base_palette(view, gradients, monkeypatch):
    from framework.views.pickone import PickOneBackend
    from framework.views.profile import ProfilePixieBackend
    from framework.views.renderer.pixie import model as source

    monkeypatch.setattr(source, "pick_gradient_color", Mock(side_effect=gradients))
    categories = [dict(label="猫", command_key="cats", keys=["cat"], image_count=3)]
    if view == "profile":
        backend = ProfilePixieBackend()
        model = dict(
            platform="Codeforces", handle="Example", rating=1234, rank="Pupil", metrics=[], details=[]
        )
    elif view == "pickone":
        backend = PickOneBackend()
        model = dict(title="图鉴", items=categories)
    else:
        backend = PickOneBackend()
        model = dict(
            title="猫 · 预览",
            preview=True,
            categories=categories,
            category_key="cats",
            items=[dict(hash_id="0123456789abcdef", likes=1, comments=[], pickup_times=2)],
        )
    # PickOne measures before drawing: reuse each choice for those two instances.
    if view != "profile":
        monkeypatch.setattr(
            source, "pick_gradient_color", Mock(side_effect=[g for g in gradients for _ in range(2)])
        )
    assert backend.render(model).data == backend.render(model).data
