"""재생성 정책을 고정한다. LLM을 부르지 않는다.

여기서 정하는 것은 하나다 — **누가 살아남는가.** 잘못 자르면 멀쩡한 손님을 버리고
돈까지 쓴다 (규약 5-4).
"""

from typing import Any

from daily_special.application.regenerate import Partition, partition_by_errors
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.issue import Severity


def _bible() -> ProjectBible:
    def axis(key: str) -> dict[str, Any]:
        return {
            "key": key,
            "label": key,
            "description": key,
            "slider_min": 0,
            "slider_max": 100,
        }

    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling"), named("mild")],
            "axes": [axis("heat"), axis("seasoning")],
            "dietary_constraints": [named("no_meat")],
            "voices": [named("gruff")],
            "situations": [
                {
                    "key": "greet",
                    "label": "인사",
                    "description": "들어올 때",
                    "subject": "none",
                },
            ],
            "economy": {
                "wallet_min": 8,
                "wallet_max": 40,
                "dish_price_min": 6,
                "dish_price_max": 30,
                "ingredient_price_min": 1,
                "ingredient_price_max": 8,
            },
            "generation": {
                "max_ideal_span_ratio": 0.5,
                "min_preferred_axes": 1,
                "min_preferred_needs": 1,
                "max_preferred_needs": 2,
                "min_text_length": 1,
                "max_dish_need_tags": 1,
                "min_dish_ingredients": 2,
                "max_dish_ingredients": 4,
                "max_line_length": 40,
            },
            "scoring": {
                "need_floor": 0.15,
                "axis_tolerance": 25,
                "budget_overrun_ratio": 1.5,
                "dietary_violation_factor": 0.1,
            },
        }
    )


def _guest(guest_id: str = "guest_test_01", **overrides: Any) -> Guest:
    data: dict[str, Any] = {
        "guest_id": guest_id,
        "name": "테스트",
        "title": "시험용",
        "bio": "테스트에만 나온다.",
        "personality": "말이 없다.",
        "voice": "gruff",
        "preferred_needs": ["filling"],
        "ideal_ranges": {"heat": {"low": 40, "high": 60}},
        "dietary": [],
    }
    data.update(overrides)
    return Guest.model_validate(data)


def _partition(guests: list[Guest]) -> Partition[Guest]:
    """정책은 콘텐츠 종류를 모른다. 검사 방법만 받아서 쓴다."""
    bible = _bible()
    return partition_by_errors(
        guests,
        check=lambda guest: check_guest(guest, bible),
        id_of=lambda guest: guest.guest_id,
    )


def test_clean_batch_keeps_everyone() -> None:
    guests = [_guest("guest_a_01"), _guest("guest_b_01")]
    partition = _partition(guests)

    assert partition.kept == guests
    assert partition.rejected == []
    assert partition.issues == []


def test_only_the_offender_is_rejected() -> None:
    """한 명이 틀렸다고 나머지를 되던지지 않는다."""
    good = _guest("guest_a_01")
    bad = _guest("guest_b_01", voice="sarcastic")

    partition = _partition([good, bad])

    assert partition.kept == [good]
    assert partition.rejected == [bad]


def test_warning_alone_does_not_reject() -> None:
    """WARNING은 통과시킨다. ERROR만 재생성을 부르고, 재생성은 돈을 쓴다."""
    warned = _guest(preferred_needs=["filling", "mild", "filling"])
    partition = _partition([warned])

    assert partition.kept == [warned]
    assert partition.rejected == []


def test_duplicate_id_keeps_the_first() -> None:
    """둘 다 버리면 멀쩡한 손님 하나를 이유 없이 잃는다."""
    first = _guest("guest_same_01", name="먼저")
    second = _guest("guest_same_01", name="나중")

    partition = _partition([first, second])

    assert partition.kept == [first]
    assert partition.rejected == [second]


def test_issues_cover_only_the_rejected() -> None:
    """다시 만들지 않을 것을 고치라고 말하면 모델이 엉뚱한 데 힘을 쓴다."""
    warned = _guest("guest_a_01", preferred_needs=["filling", "mild", "filling"])
    bad = _guest("guest_b_01", voice="sarcastic")

    partition = _partition([warned, bad])

    assert [issue.field for issue in partition.issues] == ["voice"]
    assert all(issue.severity is Severity.ERROR for issue in partition.issues)


def test_quality_failure_is_rejected() -> None:
    """문법적으로 완벽하면서 쓸모없는 생성물도 다시 만든다."""
    empty = _guest(ideal_ranges={})
    partition = _partition([empty])

    assert partition.rejected == [empty]


def test_empty_batch_is_fine() -> None:
    partition = _partition([])

    assert partition.kept == []
    assert partition.rejected == []
