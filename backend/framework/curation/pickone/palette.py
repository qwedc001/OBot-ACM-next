"""类别配色：与 Bot 渲染图（render_pick_one.py）保持同一套视觉语言。

Bot 的类别卡片是「彩虹渐变 + 低透明度底色 + 同色系深色文字」：

    _SPECTRUM_START_HUE = 0.0    (红)
    _SPECTRUM_END_HUE   = 285.0  (紫)
    _SPECTRUM_SATURATION = 0.76
    _TINT_LUMINANCE_RANGE = (0.50, 0.66)  # 卡片底色
    _TEXT_LUMINANCE_RANGE = (0.26, 0.36)  # 文字 / 条形

Web 端复刻同一套配色，让网页和 Bot 出图看起来是一个东西。
区别只有一处：Bot 按类别顺序（数量降序）依次取色，新增一个类别会让后面所有
类别换色；Web 端按类别标识做稳定哈希取色，同一个类别永远同一个颜色，
彩虹整体观感不变但颜色不会跳。
"""

from __future__ import annotations

import colorsys
import hashlib

# 与 Bot 完全一致的光谱参数
SPECTRUM_START_HUE = 0.0
SPECTRUM_END_HUE = 285.0
SPECTRUM_SATURATION = 0.76
TINT_LUMINANCE_RANGE = (0.50, 0.66)
TEXT_LUMINANCE_RANGE = (0.26, 0.36)

# 卡片底色单独降低饱和度：Bot 出图是纯色块，0.76 的饱和度放在网页大面积铺开
# 会很吵。文字仍然用 Bot 的 0.76，保证对比度与「同一个色系」的观感。
TINT_SATURATION = 0.46

# 卡片底色与背景
CARD_COLOR = "#f2f2f2"
BACKGROUND_COLOR = "#e8e8e8"


def _luminance(red: float, green: float, blue: float) -> float:
    """与 Bot 的 rgb_luminance 相同的加权公式。"""
    return 0.299 * red + 0.587 * green + 0.114 * blue


def spectrum_ratio_of(img_key: str) -> float:
    """类别标识 -> 光谱上的位置 [0, 1]，稳定且分布均匀。"""
    digest = hashlib.sha1(img_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def hue_color(
    ratio: float,
    luminance_range: tuple[float, float],
    saturation: float = SPECTRUM_SATURATION,
) -> tuple[int, int, int]:
    """在彩虹渐变上按比例取色，并把亮度收进指定区间。

    过亮的色相（黄）等比压暗、过暗的色相（蓝紫）向白色混合提亮；
    区间内的色相保持原样，避免红黄系被压成褐色。与 Bot 的实现逐行对应，
    只有 saturation 可覆盖（卡片底色用更低的饱和度）。
    """
    hue = (SPECTRUM_START_HUE + (SPECTRUM_END_HUE - SPECTRUM_START_HUE) * ratio) / 360.0
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, 1.0)

    low, high = luminance_range
    luminance = _luminance(red, green, blue)
    if luminance > high:
        scale = high / luminance
        red, green, blue = red * scale, green * scale, blue * scale
    elif luminance < low:
        mix = (low - luminance) / (1 - luminance)
        red, green, blue = (
            red + (1 - red) * mix,
            green + (1 - green) * mix,
            blue + (1 - blue) * mix,
        )

    return round(red * 255), round(green * 255), round(blue * 255)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _rgba(rgb: tuple[int, int, int], alpha: float) -> str:
    return f"rgba({rgb[0]}, {rgb[1]}, {rgb[2]}, {alpha:g})"


def accent_of(img_key: str) -> dict[str, str]:
    """给一个类别算出整组配色，前端直接用，不用自己算。"""
    ratio = spectrum_ratio_of(img_key)
    # 底色用低饱和版本，文字仍用 Bot 的饱和度
    tint_rgb = hue_color(ratio, TINT_LUMINANCE_RANGE, saturation=TINT_SATURATION)
    text_rgb = hue_color(ratio, TEXT_LUMINANCE_RANGE)

    # 底色叠在白卡片上，透明度也比 Bot 的 _TINT_ALPHA=26/255 更低一点，
    # 大面积铺开时不至于抢内容
    return {
        "tint": _rgba(tint_rgb, 0.1),
        "tint_strong": _rgba(tint_rgb, 0.18),
        "track": _rgba(tint_rgb, 0.16),
        # 悬停时的柔和投影，用色相本身而不是灰色，卡片才有"发光"感
        "glow": _rgba(tint_rgb, 0.3),
        "ink": _hex(text_rgb),
        "soft_ink": _rgba(text_rgb, 0.72),
    }
