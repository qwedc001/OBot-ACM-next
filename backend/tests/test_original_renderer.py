from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("elapsed,percent", [(0, 0), (1800, 50), (3600, 100), (7200, 100)])
def test_et_counts_elapsed_time_up_and_fills_the_original_index_bar(elapsed, percent):
    pixie = pytest.importorskip("pixie")
    from framework.views.renderer.pixie import render_contest_list as source

    contest = SimpleNamespace(
        now=10000 + elapsed,
        start_time=10000,
        duration=3600,
        phase="正在进行",
        platform="Codeforces",
        abbr="CF100",
        name="Example",
        supplement="Rated",
    )
    item = source._ContestItem(contest, 0, is_running=True)
    assert item.str_ratio.content == f"{percent}% ET"
    image = pixie.Image(1172, item.get_height() + 256)
    with patch.object(source, "draw_mask_rect", wraps=source.draw_mask_rect) as draw:
        item.render(image, 128, 128)
    assert draw.call_args_list[0].args[1].width == item._00_idx_text_width
    if percent:
        assert draw.call_args_list[1].args[1].width == int(item._00_idx_text_width * percent / 100)
    else:
        assert len(draw.call_args_list) == 1  # empty track; no zero-width Pixie mask


def test_original_tips_survive_information_area_replacement():
    pytest.importorskip("pixie")
    from framework.views.renderer.bindings import HelpPageRenderer, contest_renderer, pickone_renderer
    from framework.views.renderer.pixie.render_help import _HelpCategory
    from framework.views.renderer.runtime import Help

    help_renderer = HelpPageRenderer([_HelpCategory("通用", [Help("/help", "说明")], "#4CA1AF")])
    contest = contest_renderer({"title": "今日比赛", "now": 10000, "items": []})
    pickone = pickone_renderer(
        {
            "title": "图鉴",
            "hint": "/来只",
            "items": [
                {"label": "猫", "command_key": "cats", "keys": ["猫"], "image_count": 3},
            ],
        }
    )
    for renderer, expected in (
        (help_renderer, "实线参数必填，虚线参数选填"),
        (contest, "数据源于平台数据爬取/API调用/手动填写，仅供参考"),
        (pickone, "发送 /来只 加类别关键词或别名即可随机获取表情包，支持模糊匹配与多候选挑选."),
    ):
        section = renderer.sections[-1]
        assert section.tips.str_tips_title.content == "Tips:"
        assert section.tips.str_tips_detail.content == expected
        assert section.tips.str_tips_title.font.size == 36
        assert section.tips.str_tips_detail.font.size == 28
        assert section.tips.str_tips_detail.padding_bottom == 64
        from framework.views.renderer import bindings

        with patch.object(bindings, "draw_text", wraps=bindings.draw_text) as drawing:
            renderer.render()
        assert [call.args[1].content for call in drawing.call_args_list] == ["Tips:", expected]
