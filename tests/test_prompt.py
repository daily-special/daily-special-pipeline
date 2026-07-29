"""프롬프트 조립을 고정한다.

여기서 지키는 것은 주로 **무엇이 없는가**다. 어휘는 스키마의 필드 설명이 나르므로
프롬프트가 같은 말을 다시 하면 토큰을 두 번 내고, 더 나쁘게는 둘이 어긋난다.
"""

from typing import Any

import pytest

from daily_special.application.prompt import build_guest_context, build_guest_instruction
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible


def _bible() -> ProjectBible:
    def axis(key: str, label: str) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "description": f"{label} 설명",
            "slider_min": 0,
            "slider_max": 100,
        }

    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": f"{key}에 대한 긴 설명 문장"}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling")],
            "axes": [axis("heat", "불 세기"), axis("seasoning", "간")],
            "dietary_constraints": [named("no_meat")],
            "voices": [named("gruff")],
            "generation": {
                "max_ideal_span_ratio": 0.5,
                "min_preferred_axes": 1,
                "min_preferred_needs": 1,
                "max_preferred_needs": 1,
                "min_text_length": 1,
            },
            "scoring": {
                "need_floor": 0.15,
                "axis_tolerance": 25,
                "budget_overrun_ratio": 1.5,
                "dietary_violation_factor": 0.1,
            },
        }
    )


def test_instruction_carries_the_setting() -> None:
    """세계관은 스키마가 표현할 수 없다. 프롬프트가 나른다."""
    instruction = build_guest_instruction()
    assert "길드" in instruction
    assert "코지" in instruction


def test_instruction_demands_distinctness() -> None:
    """배치로 뽑는 유일한 이유가 서로 안 겹치게 하는 것이다. 그 요구가 빠지면 배치가 무의미하다."""
    assert "겹치지 않게" in build_guest_instruction()


def test_instruction_demands_identity_number_coherence() -> None:
    """규칙 검증이 잡을 수 없는 것 — 사연과 수치가 어긋나는 것은 여기서만 막는다."""
    assert "앞뒤로 맞아야" in build_guest_instruction()


def test_context_does_not_repeat_the_vocabulary() -> None:
    """어휘와 그 설명은 스키마 필드 설명이 나른다. 두 곳에 실으면 어긋난다."""
    context = build_guest_context(_bible(), count=4)
    assert "긴 설명 문장" not in context


def test_context_states_the_count() -> None:
    assert "4명" in build_guest_context(_bible(), count=4)


def test_context_names_the_axes() -> None:
    """축 이름은 있어야 한다 — 취향을 어디에 배분할지가 손님을 또렷하게 만든다."""
    context = build_guest_context(_bible(), count=2)
    assert "불 세기" in context
    assert "간" in context


@pytest.mark.parametrize("count", [0, -1])
def test_non_positive_count_is_a_domain_error(count: int) -> None:
    """설정 오류도 생성물 문제도 아닌 호출 측의 잘못이다."""
    with pytest.raises(DomainError, match="1 이상"):
        build_guest_context(_bible(), count=count)
