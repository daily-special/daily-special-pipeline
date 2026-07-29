"""요리 생성 서비스를 고정한다. API 키 없이 통과한다.

요리는 바깥을 참조하는 첫 콘텐츠라, 여기서 검사할 것은 **재료가 생성 경로에 제대로
실리는가**다 — 프롬프트에도 들어가고 검증에도 들어가야 한다. 한쪽만 있으면 모델이
없는 재료를 지어내거나, 지어낸 것을 아무도 못 잡는다.
"""

from typing import Any

import pytest

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.generate_dishes import generate_dishes
from daily_special.application.port.llm import Tier
from daily_special.application.schema_builder import build_dish_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import has_errors


def _bible() -> ProjectBible:
    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

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
                "max_dish_need_tags": 2,
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


def _ingredients() -> list[Ingredient]:
    def make(key: str, kind: str, price: int) -> Ingredient:
        return Ingredient(
            ingredient_id=key,
            name=f"{key} 이름",
            kind=kind,  # type: ignore[arg-type]
            description="테스트 재료.",
            base_price=price,
            dietary_conflicts=[],
        )

    return [
        make("ingredient_grain", "preserved", 1),
        make("ingredient_salt", "preserved", 1),
    ]


def _item(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "dish_id": "dish_test_a",
        "name": "시험 국",
        "description": "테스트에만 나온다.",
        "need_tags": ["filling"],
        "ingredient_ids": ["ingredient_grain", "ingredient_salt"],
        "base_price": 8,
    }
    data.update(overrides)
    return data


def _response(bible: ProjectBible, *items: dict[str, Any]) -> Any:
    return build_dish_batch_schema(bible).model.model_validate({"dishes": list(items)})


async def test_generates_from_one_call() -> None:
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(dish_id="dish_test_b", need_tags=["mild"]))])

    result = await generate_dishes(llm=llm, bible=bible, ingredients=_ingredients(), count=2)

    assert len(result.dishes) == 2
    assert result.call_count == 1
    assert not has_errors(result.issues)


async def test_uses_the_quality_tier() -> None:
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(dish_id="dish_test_b"))])

    await generate_dishes(llm=llm, bible=bible, ingredients=_ingredients(), count=2)

    assert llm.calls[0].tier is Tier.QUALITY


async def test_ingredient_list_reaches_the_prompt() -> None:
    """스키마로는 "실재하는 재료만"을 강제할 수 없다.

    재료 ID는 어휘가 아니라 다른 생성물의 값이라 enum으로 굳힐 수 없다. 목록을
    본문에 싣지 않으면 모델은 재료를 지어낼 수밖에 없다.
    """
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(dish_id="dish_test_b"))])

    await generate_dishes(llm=llm, bible=bible, ingredients=_ingredients(), count=2)

    context = llm.calls[0].context
    assert "ingredient_grain" in context
    assert "ingredient_salt" in context


async def test_invented_ingredient_is_caught() -> None:
    """프롬프트가 못 막은 것을 검증이 받친다."""
    bible = _bible()
    llm = FakeLlm(
        [_response(bible, _item(ingredient_ids=["ingredient_grain", "ingredient_ghost"]))]
    )

    result = await generate_dishes(
        llm=llm, bible=bible, ingredients=_ingredients(), count=1, max_regenerations=0
    )

    assert has_errors(result.issues)
    assert any("없는 재료" in issue.message for issue in result.issues)
    assert len(result.dishes) == 1, "생성물을 버렸다"


async def test_only_the_offender_is_regenerated() -> None:
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(), _item(dish_id="dish_test_b", base_price=99)),
            _response(bible, _item(dish_id="dish_test_c", need_tags=["mild"])),
        ]
    )

    result = await generate_dishes(llm=llm, bible=bible, ingredients=_ingredients(), count=2)

    assert result.call_count == 2
    assert not has_errors(result.issues)
    assert "dish_test_a" in [dish.dish_id for dish in result.dishes]


async def test_feedback_reaches_the_model() -> None:
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(base_price=99)),
            _response(bible, _item(dish_id="dish_test_b")),
        ]
    )

    await generate_dishes(llm=llm, bible=bible, ingredients=_ingredients(), count=1)

    assert "지난번" in llm.calls[1].context
    assert "가격" in llm.calls[1].context


async def test_no_ingredients_never_calls_the_model() -> None:
    """재료가 없으면 요리를 만들 수 없다. 돈을 쓰기 전에 막는다."""
    llm = FakeLlm([])

    with pytest.raises(DomainError, match="재료가 없으면"):
        await generate_dishes(llm=llm, bible=_bible(), ingredients=[], count=1)

    assert llm.calls == []


async def test_non_positive_count_never_calls_the_model() -> None:
    llm = FakeLlm([])

    with pytest.raises(DomainError):
        await generate_dishes(llm=llm, bible=_bible(), ingredients=_ingredients(), count=0)

    assert llm.calls == []
