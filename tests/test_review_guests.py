"""검토 계층을 고정한다.

**이 층은 돈을 쓴다.** 그래서 여기서 가장 중요한 테스트는 "무엇을 잡아내는가"가 아니라
**언제 부르지 않는가**와 **실패해도 생성물이 죽지 않는가**다.
"""

from typing import Any

import pytest

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.port.llm import Tier
from daily_special.application.review import ReviewFinding, ReviewResponse, Verdict
from daily_special.application.review_guests import review_guests
from daily_special.common.errors import LlmError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest
from daily_special.domain.issue import Severity, has_errors


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


def _pair() -> list[Guest]:
    return [_guest("guest_a_01"), _guest("guest_b_01")]


def _finding(**overrides: Any) -> ReviewFinding:
    data: dict[str, Any] = {
        "verdict": Verdict.OVERLAP,
        "subject_ids": ["guest_a_01", "guest_b_01"],
        "reason": "둘 다 무뚝뚝한 정찰병이다.",
        "suggestion": "한쪽을 다른 직업으로 바꾼다.",
    }
    data.update(overrides)
    return ReviewFinding.model_validate(data)


class _FailingLlm:
    """언제나 실패하는 어댑터. 검토가 죽어도 생성물이 사는지 보기 위한 것."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate[T: Any](self, **kwargs: Any) -> T:
        self.calls += 1
        raise LlmError("연결이 되지 않는다")


# ---------------------------------------------------------------- 부르지 않는 경우


async def test_guests_with_errors_are_not_sent() -> None:
    """규칙이 깨진 생성물은 3층에 넘기지 않는다 (규약 5-1).

    어차피 다시 만들 것이라 호출만 낭비된다.
    """
    bible = _bible()
    broken = [_guest("guest_a_01", voice="sarcastic"), _guest("guest_b_01", voice="sarcastic")]
    llm = FakeLlm([])

    result = await review_guests(llm=llm, bible=bible, guests=broken)

    assert result.call_count == 0
    assert llm.calls == []


async def test_single_guest_is_not_reviewed() -> None:
    """비교할 상대가 없으면 겹침을 볼 수 없다. 남는 판정은 값이 적다."""
    llm = FakeLlm([])

    result = await review_guests(llm=llm, bible=_bible(), guests=[_guest()])

    assert result.call_count == 0
    assert result.issues == []


async def test_empty_batch_is_not_reviewed() -> None:
    llm = FakeLlm([])

    result = await review_guests(llm=llm, bible=_bible(), guests=[])

    assert result.call_count == 0


async def test_only_clean_guests_reach_the_model() -> None:
    """깨진 손님은 걸러지고 나머지만 간다."""
    bible = _bible()
    guests = [
        _guest("guest_a_01"),
        _guest("guest_b_01", voice="sarcastic"),
        _guest("guest_c_01"),
    ]
    llm = FakeLlm([ReviewResponse(findings=[])])

    await review_guests(llm=llm, bible=bible, guests=guests)

    context = llm.calls[0].context
    assert "guest_a_01" in context
    assert "guest_c_01" in context
    assert "guest_b_01" not in context, "규칙이 깨진 손님을 유료 검토에 넘겼다"


# ---------------------------------------------------------------- 판정


async def test_uses_the_fast_tier() -> None:
    """창작이 아니라 비교다. 추론에 돈을 쓸 자리가 아니다 (규약 4-3)."""
    llm = FakeLlm([ReviewResponse(findings=[])])

    await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert llm.calls[0].tier is Tier.FAST


async def test_clean_batch_yields_no_issues() -> None:
    """지적이 매번 나오면 아무도 읽지 않게 되고, 그 순간 이 검토는 값어치를 잃는다."""
    llm = FakeLlm([ReviewResponse(findings=[])])

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert result.issues == []
    assert result.call_count == 1


async def test_finding_becomes_a_warning_never_an_error() -> None:
    """검토가 지목했다고 자동으로 버리지 않는다 (규약 5-4).

    WARNING이면 has_errors가 False라 재생성이 걸리지 않는다. 규약이 코드로 지켜진다.
    """
    llm = FakeLlm([ReviewResponse(findings=[_finding()])])

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert len(result.issues) == 1
    assert result.issues[0].severity is Severity.WARNING
    assert not has_errors(result.issues)


async def test_issue_carries_verdict_reason_and_suggestion() -> None:
    """판정만 있으면 사람이 무엇을 해야 할지 모른다 (규약 5-1)."""
    llm = FakeLlm([ReviewResponse(findings=[_finding()])])

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())
    message = result.issues[0].message

    assert "overlap" in message
    assert "둘 다 무뚝뚝한 정찰병이다." in message
    assert "한쪽을 다른 직업으로 바꾼다." in message


async def test_issue_points_at_the_first_named_guest() -> None:
    llm = FakeLlm([ReviewResponse(findings=[_finding(subject_ids=["guest_b_01"])])])

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert result.issues[0].field == "items[1]"


async def test_finding_about_an_unknown_guest_still_survives() -> None:
    """검토가 없는 손님을 지목하면 그 지적은 근거가 없다.

    그렇다고 버리지는 않는다 — 검토가 헛것을 봤다는 사실 자체가 신호다.
    """
    llm = FakeLlm([ReviewResponse(findings=[_finding(subject_ids=["guest_ghost_99"])])])

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert result.issues[0].field == "items"
    assert "대상 불명" in result.issues[0].message


# ---------------------------------------------------------------- 실패


async def test_review_failure_does_not_kill_the_output() -> None:
    """7단계의 완료 조건.

    검토는 보조 장치다. 여기서 예외를 올리면 이미 만들어진 멀쩡한 생성물까지 잃는다.
    """
    llm = _FailingLlm()

    result = await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert result.issues[0].severity is Severity.WARNING
    assert "검토를 하지 못했다" in result.issues[0].message
    assert llm.calls == 1


async def test_review_failure_is_not_retried_here() -> None:
    """전송 재시도는 어댑터의 층이다 (규약 5-3). 두 곳에서 세면 추적할 수 없다."""
    llm = _FailingLlm()

    await review_guests(llm=llm, bible=_bible(), guests=_pair())

    assert llm.calls == 1


def test_review_context_requires_guests() -> None:
    """빈 목록으로 프롬프트를 만드는 것은 호출 측의 잘못이다."""
    from daily_special.application.prompt import build_guest_review_context
    from daily_special.common.errors import DomainError

    with pytest.raises(DomainError, match="검토할 손님이 없다"):
        build_guest_review_context([])
