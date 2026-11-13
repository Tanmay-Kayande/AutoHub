"""Reciprocal rank fusion."""

from __future__ import annotations

from autohub.search.service import SearchHit, rrf


def hit(i: int, source: str) -> SearchHit:
    return SearchHit(i, "variant", i, 0, f"doc {i}", {}, None, None, None, 0.0, [source])


def test_items_in_both_lists_rank_first_and_keep_both_sources() -> None:
    semantic = [hit(1, "semantic"), hit(2, "semantic"), hit(3, "semantic")]
    keyword = [hit(3, "keyword"), hit(4, "keyword")]
    fused = rrf([semantic, keyword], k=60)
    assert [h.id for h in fused] == [3, 1, 2, 4]
    assert fused[0].sources == ["keyword", "semantic"]
    assert fused[0].score == round(1 / 63 + 1 / 61, 6)


def test_limit_and_empty_lists() -> None:
    assert rrf([[], []]) == []
    assert len(rrf([[hit(i, "s") for i in range(20)]], limit=5)) == 5
