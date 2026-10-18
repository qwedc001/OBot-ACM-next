"""Content-height defaults around the pinned Peeper renderer's section API.

Upstream separate_columns only increases width when a ranking has 32+ rows.
Keep its row splitting and drawing, but let long collections of short sections
use two or three columns as well. This adapter never modifies the submodule.
"""

MAX_ASPECT_RATIO = 2


def canvas_size(generator):
    from module.board import misc

    sections = [generator.section_title, generator.section_content, generator.section_copyright]
    columns = max(section.get_columns() for section in sections)
    return (
        misc._CONTENT_WIDTH * columns + misc._COLUMN_PADDING * (columns - 1) + 64,
        sum(section.get_height() for section in sections)
        + misc._SECTION_PADDING * (len(sections) - 1)
        + misc._TOP_PADDING
        + misc._BOTTOM_PADDING
        + 64,
    )


def apply_default_columns(generator):
    """Choose the fewest extra columns that shorten an over-tall image.

    Reconstruct the content bundle so upstream recalculates section positions;
    changing only get_columns after construction would leave stale positions.
    Existing multi-column ranking sections retain their original span and order.
    """
    from module.board import misc
    from module.board.model import MultiColumnRenderableSection

    class ContentColumns(MultiColumnRenderableSection):
        def __init__(self, content, columns):
            self.columns = columns
            super().__init__(
                content.config,
                content.section_bundle,
                misc._CONTENT_WIDTH,
                misc._SECTION_PADDING,
                misc._COLUMN_PADDING,
            )
            # Upstream's overflow strategy sends remaining sections back to
            # column zero. Assign each section to the shortest contiguous span
            # instead; its existing draw/measure methods share these positions.
            heights = [0] * columns
            for index, section in enumerate(self.section_bundle):
                span = section.get_columns()
                column = min(range(columns - span + 1), key=lambda start: max(heights[start : start + span]))
                bottom = max(heights[column : column + span]) + section.get_height() + misc._SECTION_PADDING
                self._sections_col_id[index] = column
                heights[column : column + span] = [bottom] * span

        def get_columns(self):
            return self.columns

    original = best = generator.section_content
    width, best_height = canvas_size(generator)
    if best_height <= width * MAX_ASPECT_RATIO:
        return
    for columns in range(original.get_columns() + 1, 4):
        generator.section_content = ContentColumns(original, columns)
        width, height = canvas_size(generator)
        if height < best_height:
            best, best_height = generator.section_content, height
            if height <= width * MAX_ASPECT_RATIO:
                break
    generator.section_content = best
