"""Rating-aware profile view; native drawing stays in the render worker."""

from ..clients.renderer import PixieBackend
from ..profiles import clean

CF_RANKS = (
    (1200, "#808080"),
    (1400, "#008000"),
    (1600, "#03a89e"),
    (1900, "#0000ff"),
    (2100, "#aa00aa"),
    (2300, "#ff8c00"),
    (2400, "#bbbb00"),
    (float("inf"), "#ff0000"),
)
NK_RANKS = (
    (700, "#b4b4b4"),
    (1100, "#c177e7"),
    (1500, "#5ea1f4"),
    (2000, "#25bb9b"),
    (2400, "#ffd700"),
    (2800, "#ff8800"),
    (float("inf"), "#ff020a"),
)
ATC_COLORS = ("#808080", "#804000", "#008000", "#00c0c0", "#0000ff", "#c0c000", "#ff8000", "#ff0000")


def profile_accent(platform: str, rating: int | None, rank: str) -> str:
    """Exact legacy thresholds; AtCoder's badge represents the highest rank."""
    if platform == "AtCoder":
        if rank == "King":
            return "#ff0000"
        parts = rank.split()
        if len(parts) == 2 and parts[0].isdigit():
            level = int(parts[0])
            if parts[1] == "Kyu" and 1 <= level <= 10:
                return ATC_COLORS[(10 - level) // 2]
            if parts[1] == "Dan" and 1 <= level <= 10:
                return ATC_COLORS[min(7, 5 + (level - 1) // 2)]
        return ATC_COLORS[min(7, max(0, rating or 0) // 400)]
    bands = CF_RANKS if platform == "Codeforces" else NK_RANKS
    return next(color for ceiling, color in bands if (rating or 0) < ceiling)


class ProfilePixieBackend:
    @staticmethod
    def draw(model):
        from .renderer.bindings import InformationSection
        from .renderer.pixie.render_user_card import UserCardInfo, UserCardRenderer, UserCardSection

        platform = model["platform"]
        rank = model["rank"]
        if platform == "牛客" and rank in {"ACM", "团队"}:
            rank = next(
                label
                for ceiling, label in zip(
                    (700, 1100, 1500, 2000, 2400, 2800, float("inf")),
                    ("#灰", "#紫", "#蓝", "#青", "#黄", "#橙", "#红"),
                    strict=True,
                )
                if (model["rating"] or 0) < ceiling
            )
        info = UserCardInfo(
            platform_name="NowCoder" if platform == "牛客" else platform,
            handle=clean(model["handle"], 64),
            accent_color=profile_accent(platform, model["rating"], model["rank"]),
            rating=str(model["rating"]) if model["rating"] is not None else "Unrated",
            rank=clean(rank, 64),
            rating_note=clean(model.get("rating_note", "")),
            avatar_data=model.get("avatar"),
            social=[clean(value) for value in model["details"][:6]],
            timeline=[clean(value) for value in model.get("timeline", ())[:4]],
            metrics=[(clean(label, 32), clean(value, 60)) for label, value in model["metrics"][:8]],
            sections=[
                UserCardSection(
                    title=clean(section["title"], 32),
                    lines=[clean(line, 140) for line in section["lines"][:5]],
                )
                for section in [
                    *model.get("sections", ())[:6],
                    *([{"title": "数据提示", "lines": model["warnings"]}] if model.get("warnings") else []),
                ]
            ],
        )
        renderer = UserCardRenderer(info)
        renderer.sections[-1] = InformationSection(width=1216, detail="配色 " + info.accent_color.upper())
        return renderer.render()

    def render(self, model):
        import pixie

        try:
            return PixieBackend(self.draw).render(model)
        except pixie.PixieError as error:
            raise ValueError("Profile drawing unavailable") from error
