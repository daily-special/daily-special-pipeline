"""대사 생성 서비스를 고정한다. API 키 없이 통과한다.

앞의 셋과 호출 구조가 달라서 검사할 것도 다르다 — **자리마다 한 번씩 부르는가**,
그리고 **호출 횟수가 예측 가능한가.** 대사는 자리가 많아 여기가 곧 비용이다.
"""

from typing import Any

import pytest

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.generate_lines import generate_lines, plan_slots
from daily_special.application.port.llm import Tier
from daily_special.application.schema_builder import build_line_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import has_errors


def _bible() -> ProjectBible:
    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    def situation(key: str, subject: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key, "subject": subject}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling"), named("mild")],
            "axes": [
                {
                    "key": "heat",
                    "label": "heat",
                    "description": "heat",
                    "slider_min": 0,
                    "slider_max": 100,
                }
            ],
            "dietary_constraints": [named("no_meat")],
            "voices": [named("gruff"), named("polite")],
            "situations": [situation("greet", "none"), situation("order", "need")],
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


_COUNTER = {"n": 0}


def _response(bible: ProjectBible, *, bad: bool = False) -> Any:
    """말투 두 개짜리 응답 하나. 매번 다른 문장을 낸다 — 중복 검사에 걸리지 않게."""
    model = build_line_batch_schema(bible).model
    items = []
    for voice in bible.voices:
        _COUNTER["n"] += 1
        items.append(
            {
                "line_id": ("bad-id" if bad else f"line_x_{_COUNTER['n']:03d}"),
                "voice": voice.key,
                "text": f"대사 {_COUNTER['n']}",
            }
        )
    return model.model_validate({"lines": items})


async def test_calls_once_per_slot() -> None:
    """자리 수가 곧 최소 호출 횟수다. 상황을 늘리면 여기가 늘고 그만큼 돈이 든다."""
    bible = _bible()
    slots = plan_slots(bible)
    llm = FakeLlm([_response(bible) for _ in slots])

    result = await generate_lines(llm=llm, bible=bible)

    assert len(slots) == 3, "greet 1자리 + order 2자리(욕구 2종)"
    assert result.call_count == 3
    assert len(result.lines) == 6, "자리마다 말투 2줄"


async def test_slot_plan_expands_the_subject_vocabulary() -> None:
    """상황 어휘를 미리 펼치지 않은 대가로 여기서 펼친다."""
    bible = _bible()
    slots = plan_slots(bible)

    assert (("greet", None)) in [(s.key, subj) for s, subj in slots]
    assert ("order", "filling") in [(s.key, subj) for s, subj in slots]
    assert ("order", "mild") in [(s.key, subj) for s, subj in slots]


async def test_situation_and_subject_are_filled_by_code() -> None:
    """모델에게 묻지 않는다. 부르는 쪽이 이미 아는 값이라 물으면 틀릴 여지만 생긴다."""
    bible = _bible()
    llm = FakeLlm([_response(bible) for _ in plan_slots(bible)])

    result = await generate_lines(llm=llm, bible=bible)
    order_lines = [line for line in result.lines if line.situation == "order"]

    assert {line.subject for line in order_lines} == {"filling", "mild"}
    assert all(line.subject is None for line in result.lines if line.situation == "greet")


async def test_uses_the_quality_tier() -> None:
    bible = _bible()
    llm = FakeLlm([_response(bible) for _ in plan_slots(bible)])

    await generate_lines(llm=llm, bible=bible)

    assert all(call.tier is Tier.QUALITY for call in llm.calls)


async def test_subject_meaning_reaches_the_prompt() -> None:
    """키만 주면 모델이 뜻을 모른다. 욕구 설명이 함께 실려야 한다."""
    bible = _bible()
    llm = FakeLlm([_response(bible) for _ in plan_slots(bible)])

    await generate_lines(llm=llm, bible=bible)
    order_context = next(call.context for call in llm.calls if "원하는 것" in call.context)

    assert "직접 이름 대지 않는다" in order_context


async def test_slots_can_be_narrowed() -> None:
    """대사는 자리가 많아 통째로 다시 뽑는 비용이 크다. 일부만 잘라 넘길 수 있다."""
    bible = _bible()
    only_greet = [slot for slot in plan_slots(bible) if slot[0].key == "greet"]
    llm = FakeLlm([_response(bible)])

    result = await generate_lines(llm=llm, bible=bible, slots=only_greet)

    assert result.call_count == 1
    assert all(line.situation == "greet" for line in result.lines)


async def test_bad_slot_is_retried_whole() -> None:
    """한 자리는 통째로 다시 뽑는다.

    말투별로 한 줄씩이라 부분 재생성은 오히려 말투 대비를 잃게 만든다 —
    남은 줄을 모르는 채 새 줄이 나온다.
    """
    bible = _bible()
    only_greet = [slot for slot in plan_slots(bible) if slot[0].key == "greet"]
    llm = FakeLlm([_response(bible, bad=True), _response(bible)])

    result = await generate_lines(llm=llm, bible=bible, slots=only_greet)

    assert result.call_count == 2
    assert not has_errors(result.issues)


async def test_exhausted_slot_still_returns_lines() -> None:
    bible = _bible()
    only_greet = [slot for slot in plan_slots(bible) if slot[0].key == "greet"]
    llm = FakeLlm([_response(bible, bad=True) for _ in range(4)])

    result = await generate_lines(llm=llm, bible=bible, slots=only_greet, max_regenerations=3)

    assert result.call_count == 4
    assert len(result.lines) == 2, "고쳐지지 않았다고 버렸다"
    assert has_errors(result.issues)


async def test_empty_slots_never_calls_the_model() -> None:
    llm = FakeLlm([])

    with pytest.raises(DomainError, match="채울 자리가 없다"):
        await generate_lines(llm=llm, bible=_bible(), slots=[])

    assert llm.calls == []
