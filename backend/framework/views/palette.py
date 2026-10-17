"""Numbered uiGradients palettes, matching Peeper/easy-pixie 0.0.10.

Numbers refer to the upstream catalogue. Keep these snapshots stable: catalogue
reordering must not silently recolor cards or change their printed identifier.
"""

from dataclasses import dataclass

INK = (23, 26, 34)
MUTED = (71, 79, 89)


@dataclass(frozen=True)
class CardPalette:
    number: int
    name: str
    colors: tuple[str, str]

    @property
    def label(self) -> str:
        return f"#{self.number} {self.name}"


DECENT = CardPalette(187, "Decent", ("#4CA1AF", "#C4E0E5"))


def paint_card(image, palette: CardPalette, *, dark: bool = False):
    """Original OBot diagonal gradient with a white or near-black veil."""
    import pixie
    from easy_pixie import GradientColor, GradientDirection, Loc, draw_gradient_rect, draw_mask_rect

    image.fill(pixie.parse_color("#000000"))
    bounds = Loc(32, 32, image.width - 64, image.height - 64)
    gradient = GradientColor(list(palette.colors), [0.0, 1.0], palette.label)
    draw_gradient_rect(image, bounds, gradient, GradientDirection.DIAGONAL_LEFT_TO_RIGHT, 96)
    draw_mask_rect(image, bounds, (9, 9, 9, 238) if dark else (255, 255, 255, 178), 96)
