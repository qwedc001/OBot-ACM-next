"""Shared inks and background painting; colours come from the upstream picker."""

INK = (23, 26, 34)
MUTED = (71, 79, 89)


def paint_card(image, gradient, *, dark: bool = False):
    """Original OBot diagonal gradient with a white or near-black veil."""
    import pixie
    from easy_pixie import GradientDirection, Loc, draw_gradient_rect, draw_mask_rect

    image.fill(pixie.parse_color("#000000"))
    bounds = Loc(32, 32, image.width - 64, image.height - 64)
    draw_gradient_rect(image, bounds, gradient, GradientDirection.DIAGONAL_LEFT_TO_RIGHT, 96)
    draw_mask_rect(image, bounds, (9, 9, 9, 238) if dark else (255, 255, 255, 178), 96)
