from itertools import combinations

import pytest

from framework.views.layout import split_columns


@pytest.mark.parametrize(
    "heights,count,gap", [([5, 1, 1, 5], 2, 0), ([2, 7, 3, 4, 1], 3, 2), ([3] * 7, 4, 1)]
)
def test_ordered_columns_reach_best_height_without_reordering(heights, count, gap):
    columns = split_columns(list(range(len(heights))), count, heights.__getitem__, gap)
    assert [item for column in columns for item in column] == list(range(len(heights)))
    assert len(columns) <= count and all(columns)

    def tallest(groups):
        return max(sum(heights[i] for i in group) + gap * (len(group) - 1) for group in groups)

    # Exhaustive partitions are a small independent oracle for the balancing rule.
    best = min(
        tallest([range(start, end) for start, end in zip((0, *cuts), (*cuts, len(heights)), strict=True)])
        for size in range(count)
        for cuts in combinations(range(1, len(heights)), size)
    )
    assert tallest(columns) == best


def test_empty_and_sparse_columns_do_not_reserve_empty_space():
    assert split_columns([], 4, int) == []
    assert split_columns([2, 3], 4, int) == [[2], [3]]
    assert split_columns([2, 3], 0, int) == [[2, 3]]
