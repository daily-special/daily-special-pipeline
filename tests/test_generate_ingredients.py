"""재료 생성 서비스를 고정한다. API 키 없이 통과한다.

손님 생성과 같은 구조라 여기서 검사할 것은 **구조가 콘텐츠 종류를 타지 않는가**다 —
5·6단계에서 세운 층을 재료에 그대로 썼는데 정말 그대로 도는지.
"""

from typing import Any

import pytest

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.generate_ingredients import generate_ingredients
from daily_special.application.port.llm import Tier
from daily_special.application.schema_builder import build_ingredient_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import Severity, has_errors


def _bible() -> ProjectBible:
    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling")],
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
                "max_preferred_needs": 1,
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


def _item(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ingredient_id": "ingredient_test_a",
        "name": "시험 재료",
        "kind": "fresh",
        "description": "테스트에만 나온다.",
        "base_price": 3,
        "dietary_conflicts": [],
    }
    data.update(overrides)
    return data


def _response(bible: ProjectBible, *items: dict[str, Any]) -> Any:
    return build_ingredient_batch_schema(bible).model.model_validate({"ingredients": list(items)})


def _pair(bible: ProjectBible) -> Any:
    """검증을 통과하는 최소 배치 — 두 종류가 다 있어야 한다."""
    return _response(
        bible,
        _item(ingredient_id="ingredient_a", kind="fresh"),
        _item(ingredient_id="ingredient_b", kind="preserved"),
    )


async def test_generates_from_one_call() -> None:
    bible = _bible()
    llm = FakeLlm([_pair(bible)])

    result = await generate_ingredients(llm=llm, bible=bible, count=2)

    assert len(result.ingredients) == 2
    assert result.call_count == 1
    assert result.issues == []


async def test_uses_the_quality_tier() -> None:
    bible = _bible()
    llm = FakeLlm([_pair(bible)])

    await generate_ingredients(llm=llm, bible=bible, count=2)

    assert llm.calls[0].tier is Tier.QUALITY


async def test_result_is_contract_shaped() -> None:
    """되접기까지가 서비스의 일이다. 부르는 쪽은 동적 스키마를 몰라도 된다."""
    bible = _bible()
    llm = FakeLlm([_pair(bible)])

    result = await generate_ingredients(llm=llm, bible=bible, count=2)

    assert result.ingredients[0].ingredient_id == "ingredient_a"
    assert result.ingredients[0].base_price == 3


async def test_rule_violation_is_reported_not_discarded() -> None:
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(base_price=99))])

    result = await generate_ingredients(llm=llm, bible=bible, count=1, max_regenerations=0)

    assert len(result.ingredients) == 1, "생성물을 버렸다"
    assert has_errors(result.issues)


async def test_only_the_offender_is_regenerated() -> None:
    """배치 전체가 아니라 어긴 것만 다시 만든다 — 손님과 같은 정책이다."""
    bible = _bible()
    llm = FakeLlm(
        [
            _response(
                bible,
                _item(ingredient_id="ingredient_a", kind="fresh"),
                _item(ingredient_id="ingredient_b", kind="preserved", base_price=99),
            ),
            _response(bible, _item(ingredient_id="ingredient_c", kind="preserved")),
        ]
    )

    result = await generate_ingredients(llm=llm, bible=bible, count=2)

    assert result.call_count == 2
    assert not has_errors(result.issues)
    ids = [item.ingredient_id for item in result.ingredients]
    assert "ingredient_a" in ids, "통과한 재료를 버렸다"


async def test_feedback_reaches_the_model() -> None:
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(base_price=99)),
            _pair(bible),
        ]
    )

    await generate_ingredients(llm=llm, bible=bible, count=1)

    assert "지난번" in llm.calls[1].context
    assert "단가" in llm.calls[1].context


async def test_missing_kind_triggers_regeneration() -> None:
    """신선만 나온 배치는 다시 만든다. 상시 메뉴를 짤 수 없기 때문이다.

    다만 이것은 **배치 단위** 문제라 개별 검사로는 걸리지 않는다 — 재생성 루프가
    개별 ERROR만 보므로, 여기서는 고쳐지지 않고 경고로 남는다.
    """
    bible = _bible()
    only_fresh = _response(
        bible,
        _item(ingredient_id="ingredient_a", kind="fresh"),
        _item(ingredient_id="ingredient_b", kind="fresh"),
    )
    llm = FakeLlm([only_fresh])

    result = await generate_ingredients(llm=llm, bible=bible, count=2)

    assert result.call_count == 1, "개별 ERROR가 없으므로 재생성이 걸리지 않는다"
    assert has_errors(result.issues)
    assert any("preserved" in issue.message for issue in result.issues)


async def test_wrong_count_warns_instead_of_failing() -> None:
    bible = _bible()
    llm = FakeLlm([_pair(bible)])

    result = await generate_ingredients(llm=llm, bible=bible, count=5)

    assert any(
        issue.severity is Severity.WARNING and issue.field == "items" for issue in result.issues
    )


async def test_non_positive_count_never_calls_the_model() -> None:
    bible = _bible()
    llm = FakeLlm([])

    with pytest.raises(DomainError):
        await generate_ingredients(llm=llm, bible=bible, count=0)

    assert llm.calls == []
