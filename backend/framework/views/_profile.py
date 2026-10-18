"""Port of OBot-ACM render_user_card.py (AGPL-3.0).

Native imports are isolated here; this module is loaded only in the serialized
render worker. Layout, rank band and shared metric font sizing follow upstream.
Avatar bytes arrive already downloaded/cropped by AvatarsClient; no network IO.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pixie
from easy_pixie import (
    GradientColor,
    GradientDirection,
    Loc,
    StyledString,
    apply_tint,
    calculate_height,
    calculate_width,
    change_alpha,
    darken_color,
    draw_gradient_rect,
    draw_img,
    draw_mask_rect,
    draw_text,
    hex_to_color,
    lighten_color,
)

from .footer import CardFooter
from .layout import fits

_CONTENT_WIDTH = 1216
_AVATAR_SIZE = 176
_AVATAR_RADIUS = 44
_AVATAR_GAP = 48
_AVATAR_RING = 7
_HANDLE_GAP = 24
_DETAIL_GAP = 28

_CONTENT_COLOR = (0, 0, 0, 208)
_BODY_COLOR = (0, 0, 0, 172)
_MUTED_COLOR = (0, 0, 0, 116)

_BAND_PADDING = 60
_BAND_SIDE_PADDING = 64
_BAND_RADIUS = 48
_RATING_SIZE = 168
_RATING_MIN_SIZE = 108
_RANK_SIZE = 40
_RANK_MIN_SIZE = 28

_CELL_HEIGHT = 168
_CELL_GAP = 32
_CELL_RADIUS = 36
_CELL_SIDE_PADDING = 40
_CELL_VERTICAL_PADDING = 36
_CELL_COLOR = (255, 255, 255, 136)
_CELL_VALUE_SIZE = 56
_CELL_VALUE_MIN_SIZE = 40
_CELL_LABEL_SIZE = 26
_SECTION_PADDING = 88


def _load_avatar(data):
    if not data:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="obot-avatar-") as directory:
            path = Path(directory) / "avatar.png"
            path.write_bytes(data)
            avatar = pixie.read_image(str(path))
        mask = pixie.Mask(_AVATAR_SIZE, _AVATAR_SIZE)
        path = pixie.Path()
        path.rounded_rect(
            0, 0, _AVATAR_SIZE, _AVATAR_SIZE, _AVATAR_RADIUS, _AVATAR_RADIUS, _AVATAR_RADIUS, _AVATAR_RADIUS
        )
        mask.fill_path(path)
        avatar.mask_draw(mask)
        return avatar
    except (pixie.PixieError, OSError):
        return None


def _platform_icon(name, color):
    path = Path(__file__).with_name("assets") / f"{name}.png"
    return apply_tint(pixie.read_image(str(path)), color).resize(36, 36)


def _fit_styled_string(
    content: str, weight: str, max_size: int, min_size: int, max_width: int, **kwargs
) -> StyledString:
    """字号从 max_size 逐级下降，直到文本宽度不超过 max_width"""
    size = max_size
    while size > min_size:
        candidate = StyledString(content, weight, size, **kwargs)
        if calculate_width(candidate) <= max_width:
            return candidate
        size -= 2
    return StyledString(content, weight, min_size, max_width=max_width, **kwargs)


class _IdentitySection:
    """头像与身份信息

    头像与「平台 + handle」两行同高对齐，其余信息另起整行铺满宽度，
    这样左右两侧始终等高，也不会因为补充信息换行把头像挤成半截。
    """

    def __init__(self, info: SimpleNamespace, text_color: pixie.Color, content_width: int):
        self.img_platform = _platform_icon(info.platform_name, text_color)
        self._avatar = _load_avatar(info.avatar_data)
        self._ring_color = change_alpha(hex_to_color(info.accent_color), alpha=88)

        head_width = content_width
        if self._avatar:
            head_width -= _AVATAR_SIZE + _AVATAR_GAP

        self.str_platform = StyledString(
            f"{info.platform_name} ID", "H", 36, font_color=text_color, max_width=head_width
        )
        self.str_handle = StyledString(info.handle, "H", 92, font_color=text_color, max_width=head_width)
        # 补充信息整行铺满宽度，空文本仍会占一行高度，需要显式跳过
        self.str_social = StyledString(
            " · ".join(info.social),
            "B",
            30,
            font_color=_BODY_COLOR,
            line_multiplier=1.3,
            padding_bottom=10,
            max_width=content_width,
        )
        self.str_timeline = StyledString(
            " · ".join(info.timeline),
            "B",
            26,
            font_color=_MUTED_COLOR,
            line_multiplier=1.3,
            max_width=content_width,
        )
        self._detail_rows = [row for row in [self.str_social, self.str_timeline] if row.content]

        # 头像与「平台 + handle」两行等高，两侧上下边缘自然对齐
        self._head_height = calculate_height([self.str_platform, self.str_handle]) + _HANDLE_GAP
        if self._avatar:
            self._head_height = max(_AVATAR_SIZE, self._head_height)

    def get_height(self):
        height = self._head_height
        if len(self._detail_rows) > 0:
            height += _DETAIL_GAP + calculate_height(self._detail_rows)
        return height

    def render(self, img: pixie.Image, x: int, y: int) -> int:
        current_x = x
        if self._avatar:
            avatar_y = y + (self._head_height - _AVATAR_SIZE) // 2
            draw_mask_rect(
                img,
                Loc(
                    x - _AVATAR_RING,
                    avatar_y - _AVATAR_RING,
                    _AVATAR_SIZE + _AVATAR_RING * 2,
                    _AVATAR_SIZE + _AVATAR_RING * 2,
                ),
                self._ring_color,
                _AVATAR_RADIUS + _AVATAR_RING,
            )
            draw_img(img, self._avatar, Loc(x, avatar_y, _AVATAR_SIZE, _AVATAR_SIZE))
            current_x = x + _AVATAR_SIZE + _AVATAR_GAP

        head_text_height = calculate_height([self.str_platform, self.str_handle]) + _HANDLE_GAP
        current_y = y + (self._head_height - head_text_height) // 2
        draw_img(img, self.img_platform, Loc(current_x, current_y + 6, 36, 36))
        current_y = draw_text(img, self.str_platform, current_x + 36 + 16, current_y)
        draw_text(img, self.str_handle, current_x, current_y + _HANDLE_GAP)

        if len(self._detail_rows) > 0:
            current_y = y + self._head_height + _DETAIL_GAP
            for row in self._detail_rows:
                current_y = draw_text(img, row, x, current_y)

        return y + self.get_height()


class _RatingSection:
    """Rating 与段位"""

    def __init__(self, info: SimpleNamespace, text_color: pixie.Color, content_width: int):
        self._width = content_width
        accent = hex_to_color(info.accent_color)
        self._panel_color = change_alpha(accent, alpha=36)
        self._pill_color = change_alpha(accent, alpha=48)

        self.str_label = StyledString("比赛 Rating", "M", 30, font_color=_MUTED_COLOR, padding_bottom=10)
        self.str_rank = _fit_styled_string(
            info.rank,
            "H",
            _RANK_SIZE,
            _RANK_MIN_SIZE,
            self._width // 2,
            font_color=darken_color(accent, 0.5),
        )
        self._rank_text_width = calculate_width(self.str_rank)
        self._rank_text_height = calculate_height(self.str_rank)
        self._pill_width = int(self._rank_text_width) + 56
        self._pill_height = int(self._rank_text_height) + 28
        # Narrow columns move the rank below Rating instead of wrapping digits
        # or squeezing the number against a long badge.
        rating_width = self._width - _BAND_SIDE_PADDING * 2 - self._pill_width - 72
        self._stacked = calculate_width(StyledString(info.rating, "H", _RATING_MIN_SIZE)) > rating_width
        self.str_rating = _fit_styled_string(
            info.rating,
            "H",
            _RATING_SIZE,
            _RATING_MIN_SIZE,
            self._width - _BAND_SIDE_PADDING * 2 if self._stacked else rating_width,
            font_color=text_color,
        )
        self.str_note = StyledString(info.rating_note, "B", 28, font_color=_MUTED_COLOR, max_width=720)
        self._note_text_width = calculate_width(self.str_note)
        self._note_text_height = calculate_height(self.str_note)

        self._left_height = calculate_height([self.str_label, self.str_rating])
        self._right_height = self._pill_height + 22 + (self._note_text_height if info.rating_note else 0)
        # 段位徽章与左侧大号数字顶部对齐，不随字号自适应上下浮动
        self._right_offset = calculate_height(self.str_label) + 28

    def get_height(self):
        if self._stacked:
            return self._left_height + 32 + self._right_height + _BAND_PADDING * 2 - 20
        return max(self._left_height, self._right_offset + self._right_height) + _BAND_PADDING * 2 - 20

    def render(self, img: pixie.Image, x: int, y: int) -> int:
        height = self.get_height()
        draw_mask_rect(img, Loc(x, y, self._width, height), self._panel_color, _BAND_RADIUS)

        current_y = y + _BAND_PADDING
        current_y = draw_text(img, self.str_label, x + _BAND_SIDE_PADDING, current_y)
        draw_text(img, self.str_rating, x + _BAND_SIDE_PADDING - 8, current_y)

        right = x + self._width - _BAND_SIDE_PADDING
        current_y = y + _BAND_PADDING + (self._left_height + 32 if self._stacked else self._right_offset)
        pill_x = x + _BAND_SIDE_PADDING if self._stacked else right - self._rank_text_width - 28
        draw_mask_rect(
            img,
            Loc(pill_x, current_y, self._pill_width, self._pill_height),
            self._pill_color,
            self._pill_height // 2,
        )
        draw_text(
            img, self.str_rank, pill_x + (self._pill_width - self._rank_text_width) // 2, current_y + 14
        )
        current_y += self._pill_height + 22
        if self.str_note.content:
            note_x = x + _BAND_SIDE_PADDING if self._stacked else right - self._note_text_width
            draw_text(img, self.str_note, note_x, current_y)

        return y + height


class _MetricsSection:
    """数据指标格子

    所有数值共用同一字号，宁可整组缩小也不让单个格子变小，避免大小参差；
    格子高度按内容自适应，实在放不下时换行而不是溢出或省略。
    """

    def __init__(self, metrics: list[tuple[str, str]], content_width: int):
        self._cols = max(1, len(metrics)) if len(metrics) <= 4 else (3 if len(metrics) % 3 == 0 else 4)
        self._cell_width = (content_width - _CELL_GAP * (self._cols - 1)) // self._cols if metrics else 0
        inner_width = self._cell_width - _CELL_SIDE_PADDING * 2

        self._value_size = self._pick_value_size([value for _, value in metrics], inner_width)
        self._cells = [
            (
                StyledString(
                    value,
                    "H",
                    self._value_size,
                    font_color=_CONTENT_COLOR,
                    max_width=inner_width,
                    padding_bottom=6,
                ),
                StyledString(label, "B", _CELL_LABEL_SIZE, font_color=_MUTED_COLOR, max_width=inner_width),
            )
            for label, value in metrics
        ]
        self._row_height = max(
            _CELL_HEIGHT,
            max((calculate_height([value, label]) for value, label in self._cells), default=0)
            + _CELL_VERTICAL_PADDING * 2,
        )

    @classmethod
    def _pick_value_size(cls, values: list[str], inner_width: int) -> int:
        size = _CELL_VALUE_SIZE
        while size > _CELL_VALUE_MIN_SIZE:
            if all(calculate_width(StyledString(value, "H", size)) <= inner_width for value in values):
                break
            size -= 2
        return size

    def get_height(self):
        if not self._cells:
            return 0
        rows = (len(self._cells) + self._cols - 1) // self._cols
        return rows * self._row_height + (rows - 1) * _CELL_GAP

    def render(self, img: pixie.Image, x: int, y: int) -> int:
        if not self._cells:
            return y

        for idx, (str_value, str_label) in enumerate(self._cells):
            row, col = divmod(idx, self._cols)
            cell_x = x + col * (self._cell_width + _CELL_GAP)
            cell_y = y + row * (self._row_height + _CELL_GAP)
            draw_mask_rect(
                img, Loc(cell_x, cell_y, self._cell_width, self._row_height), _CELL_COLOR, _CELL_RADIUS
            )

            content_height = calculate_height([str_value, str_label])
            content_y = cell_y + (self._row_height - content_height) // 2
            draw_text(img, str_value, cell_x + _CELL_SIDE_PADDING, content_y)
            draw_text(img, str_label, cell_x + _CELL_SIDE_PADDING, content_y + calculate_height(str_value))

        return y + self.get_height()


class _DetailSection:
    """带标题的补充信息块"""

    def __init__(self, section: SimpleNamespace, accent_color: pixie.Color, content_width: int):
        self._bar_color = change_alpha(accent_color, alpha=108)
        self.str_title = StyledString(section.title, "H", 34, font_color=_CONTENT_COLOR, padding_bottom=26)
        self.str_lines = [
            StyledString(
                line,
                "M",
                30,
                font_color=_BODY_COLOR,
                line_multiplier=1.3,
                padding_bottom=10,
                max_width=content_width - 28,
            )
            for line in section.lines
        ]

    def get_height(self):
        return calculate_height(self.str_title) + calculate_height(self.str_lines)

    def render(self, img: pixie.Image, x: int, y: int) -> int:
        draw_mask_rect(img, Loc(x, y + 8, 6, 30), self._bar_color, 3)
        current_y = draw_text(img, self.str_title, x + 28, y)
        for str_line in self.str_lines:
            current_y = draw_text(img, str_line, x + 28, current_y)

        return y + self.get_height()


class UserCardRenderer:
    def __init__(self, info, content_width=_CONTENT_WIDTH):
        self.info = info
        self._width = content_width
        accent = hex_to_color(info.accent_color)
        self.gradient = GradientColor(["#fcfcfc", lighten_color(accent, 0.3)], [0.0, 1.0], info.accent_color)
        text_color = darken_color(accent, 0.32)
        self.sections = [
            _IdentitySection(info, text_color, content_width),
            _RatingSection(info, text_color, content_width),
            _MetricsSection(info.metrics, content_width),
        ]
        self.sections.extend(_DetailSection(section, accent, content_width) for section in info.sections)
        if info.warnings:
            self.sections.append(
                _DetailSection(SimpleNamespace(title="数据提示", lines=info.warnings), accent, content_width)
            )

    def layout(self):
        sections = [section for section in self.sections if section.get_height()]
        width = 256 + self._width
        footer = CardFooter(self._width, detail="配色 " + self.info.accent_color.upper())
        bottom = 168 + sum(section.get_height() + _SECTION_PADDING for section in sections)
        height = bottom + footer.height + 112
        if not fits(width, height):
            raise ValueError("Profile exceeds image size limit")
        return width, height, [sections], footer, bottom

    def render(self):
        # Match SimpleCardRenderer's outer/container/content paddings without
        # importing the old process-wide Constants or renderer registry.
        width, height, columns, footer, bottom = self.layout()
        image = pixie.Image(width, height)
        image.fill(pixie.parse_color("#000000"))
        bounds = Loc(32, 32, width - 64, height - 64)
        draw_gradient_rect(image, bounds, self.gradient, GradientDirection.DIAGONAL_LEFT_TO_RIGHT, 96)
        draw_mask_rect(image, bounds, (255, 255, 255, 152), 96)
        for column, sections in enumerate(columns):
            y = 168
            for section in sections:
                y = section.render(image, 128 + column * (self._width + 96), y) + _SECTION_PADDING
        footer.draw(image, 128, bottom)
        return image
