"""만족도 엔진의 계약을 고정한다.

**이 파일은 이식 명세다.** 게임 런타임이 이 계산을 다른 언어로 옮기므로, 여기의
케이스를 그대로 통과시켜야 두 구현이 어긋나지 않는다.

계수는 실제 project_bible.json이 아니라 아래 고정값을 쓴다. 밸런스 조정으로
수치가 바뀔 때마다 테스트가 깨지면, 그 테스트는 결정이 아니라 현재값을 지키는 것이 된다.
"""

from typing import Any

import pytest

from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.satisfaction import (
    GuestPersona,
    IdealRange,
    ServedDish,
    VisitState,
    evaluate,
)

TOLERANCE = 25
FLOOR = 0.15
OVERRUN = 1.5
VIOLATION = 0.1


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
            "needs": [named(k) for k in ("filling", "restorative", "mild", "affordable")],
            "axes": [axis(k) for k in ("heat", "cook_time", "seasoning")],
            "dietary_constraints": [named(k) for k in ("no_meat", "no_dairy")],
            # 만족도 계산은 말투를 읽지 않는다. 설정이 요구해서 채울 뿐이다.
            "voices": [named("gruff")],
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
            },
            "scoring": {
                "need_floor": FLOOR,
                "axis_tolerance": TOLERANCE,
                "budget_overrun_ratio": OVERRUN,
                "dietary_violation_factor": VIOLATION,
            },
        }
    )


def _persona(**overrides: Any) -> GuestPersona:
    """세 축 모두 40~60을 이상 구간으로 갖는 손님."""
    base: dict[str, Any] = {
        "ideal_ranges": {
            "heat": IdealRange(low=40, high=60),
            "cook_time": IdealRange(low=40, high=60),
            "seasoning": IdealRange(low=40, high=60),
        },
        "dietary": [],
    }
    return GuestPersona.model_validate(base | overrides)


def _state(**overrides: Any) -> VisitState:
    base: dict[str, Any] = {"needs": ["restorative", "mild"], "wallet": 100}
    return VisitState.model_validate(base | overrides)


def _dish(**overrides: Any) -> ServedDish:
    """모든 항이 만점인 요리."""
    base: dict[str, Any] = {
        "need_tags": ["restorative", "mild"],
        "price": 100,
        "dietary_conflicts": [],
        "params": {"heat": 50, "cook_time": 50, "seasoning": 50},
    }
    return ServedDish.model_validate(base | overrides)


def _evaluate(
    persona: GuestPersona | None = None,
    state: VisitState | None = None,
    dish: ServedDish | None = None,
) -> Any:
    return evaluate(
        persona=persona or _persona(),
        state=state or _state(),
        dish=dish or _dish(),
        bible=_bible(),
    )


# ---------------------------------------------------------------- 핵심 주장


def test_is_deterministic() -> None:
    """같은 입력에 항상 같은 출력. 이 저장소의 핵심 주장이다.

    런타임 LLM 0을 내세우는 근거가 여기 있다 — 손님의 판단이 재현되고 테스트된다.
    """
    first = _evaluate()
    second = _evaluate()
    assert first == second


def test_perfect_serving_scores_one() -> None:
    result = _evaluate()
    assert result.total == pytest.approx(1.0)


def test_one_zero_term_zeroes_everything() -> None:
    """곱셈인 이유. 덧셈이면 한쪽만 잘해도 통과해버린다."""
    result = _evaluate(dish=_dish(price=1000))
    assert result.budget_score == 0.0
    assert result.total == 0.0
    assert result.need_score == pytest.approx(1.0)  # 다른 항은 멀쩡하다


# ---------------------------------------------------------------- 욕구 충족도


def test_covering_half_the_needs_scores_half() -> None:
    result = _evaluate(dish=_dish(need_tags=["restorative"]))
    assert result.need_score == pytest.approx(0.5)
    assert result.unmet_needs == ["mild"]


def test_extra_tags_are_not_penalised() -> None:
    """회복식이 마침 포만감도 준다고 나쁠 것은 없다."""
    result = _evaluate(dish=_dish(need_tags=["restorative", "mild", "filling"]))
    assert result.need_score == pytest.approx(1.0)


def test_missing_every_need_falls_back_to_floor() -> None:
    """완전히 빗나가도 바닥값. "원하던 건 아니지만 밥은 먹었다"."""
    result = _evaluate(dish=_dish(need_tags=["filling"]))
    assert result.need_score == pytest.approx(FLOOR)
    assert result.unmet_needs == ["restorative", "mild"]


def test_single_need_miss_does_not_zero_the_serving() -> None:
    """욕구가 1개면 비율이 0/1 이분법이 된다. 바닥값이 전체 붕괴를 막는다.

    바닥값이 없으면 아무리 잘 만들어도 만족이 0이 되어, 하드 게임오버 없는
    코지 게임의 기조와 어긋난다.
    """
    result = _evaluate(
        state=_state(needs=["restorative"]),
        dish=_dish(need_tags=["filling"]),
    )
    assert result.need_score == pytest.approx(FLOOR)
    assert result.total > 0.0


# ---------------------------------------------------------------- 취향 일치도


def test_inside_ideal_range_scores_one() -> None:
    for value in (40, 50, 60):
        result = _evaluate(dish=_dish(params={"heat": value, "cook_time": 50, "seasoning": 50}))
        heat = next(a for a in result.axis_scores if a.axis == "heat")
        assert heat.score == pytest.approx(1.0)
        assert heat.distance == 0
        assert heat.direction == 0


def test_outside_ideal_range_decays_linearly() -> None:
    result = _evaluate(dish=_dish(params={"heat": 65, "cook_time": 50, "seasoning": 50}))
    heat = next(a for a in result.axis_scores if a.axis == "heat")
    assert heat.distance == 5
    assert heat.score == pytest.approx(1.0 - 5 / TOLERANCE)


def test_beyond_tolerance_scores_zero() -> None:
    result = _evaluate(dish=_dish(params={"heat": 200, "cook_time": 50, "seasoning": 50}))
    heat = next(a for a in result.axis_scores if a.axis == "heat")
    assert heat.score == 0.0


def test_axes_are_averaged_not_multiplied() -> None:
    """세 축이 각각 0.8이면 평균 0.8이다. 곱하면 0.512가 되어 개선이 체감되지 않는다."""
    result = _evaluate(dish=_dish(params={"heat": 65, "cook_time": 65, "seasoning": 65}))
    assert result.taste_score == pytest.approx(0.8)


def test_direction_tells_which_way_it_missed() -> None:
    """피드백 대사("좀 짠데요")를 고르는 값이다."""
    result = _evaluate(dish=_dish(params={"heat": 20, "cook_time": 50, "seasoning": 80}))
    by_axis = {a.axis: a for a in result.axis_scores}
    assert by_axis["heat"].direction == -1
    assert by_axis["seasoning"].direction == 1
    assert by_axis["cook_time"].direction == 0


def test_axis_without_preference_is_not_scored() -> None:
    """취향이 없는 축은 어떤 값이든 만족이다."""
    persona = _persona(ideal_ranges={"heat": IdealRange(low=40, high=60)})
    result = _evaluate(persona=persona, dish=_dish(params={"heat": 50, "seasoning": 0}))
    assert [a.axis for a in result.axis_scores] == ["heat"]
    assert result.taste_score == pytest.approx(1.0)


def test_missing_param_for_preferred_axis_is_an_error() -> None:
    """손님이 취향을 가진 축의 값이 없으면 계산할 수 없다. 조용히 넘기지 않는다."""
    with pytest.raises(DomainError, match="heat"):
        _evaluate(dish=_dish(params={"cook_time": 50, "seasoning": 50}))


# ---------------------------------------------------------------- 예산 적합


def test_price_within_wallet_is_full_score() -> None:
    for price in (1, 50, 100):
        result = _evaluate(dish=_dish(price=price))
        assert result.budget_score == pytest.approx(1.0)


def test_price_over_wallet_decays_instead_of_collapsing() -> None:
    """1원 차이로 만족이 통째로 0이 되면 곱셈 구조에서 지나치게 가혹하다."""
    result = _evaluate(dish=_dish(price=101))
    assert 0.0 < result.budget_score < 1.0


def test_budget_reaches_zero_at_the_overrun_ratio() -> None:
    result = _evaluate(dish=_dish(price=int(100 * OVERRUN)))
    assert result.budget_score == 0.0


def test_budget_is_scale_free() -> None:
    """가격/지갑 비율만 쓴다. 화폐 단위를 100배 해도 결과가 같다.

    덕분에 화폐의 절대 스케일을 아직 정하지 않아도 엔진이 완성된다.
    """
    small = _evaluate(state=_state(wallet=100), dish=_dish(price=125))
    large = _evaluate(state=_state(wallet=10_000), dish=_dish(price=12_500))
    assert small.budget_score == pytest.approx(large.budget_score)


# ---------------------------------------------------------------- 식이 제약


def test_dietary_violation_multiplies_instead_of_subtracting() -> None:
    """뺄셈이면 음수가 나와 clamp가 필요하고, clamp는 얼마나 나빴는지를 뭉갠다."""
    result = _evaluate(
        persona=_persona(dietary=["no_meat"]),
        dish=_dish(dietary_conflicts=["no_meat"]),
    )
    assert result.dietary_factor == pytest.approx(VIOLATION)
    assert result.violated_dietary == ["no_meat"]
    assert result.total == pytest.approx(VIOLATION)


def test_two_violations_hurt_more_than_one() -> None:
    result = _evaluate(
        persona=_persona(dietary=["no_meat", "no_dairy"]),
        dish=_dish(dietary_conflicts=["no_meat", "no_dairy"]),
    )
    assert result.dietary_factor == pytest.approx(VIOLATION**2)


def test_irrelevant_conflict_is_ignored() -> None:
    """이 손님이 지키지 않는 제약은 위반이 아니다."""
    result = _evaluate(
        persona=_persona(dietary=["no_meat"]),
        dish=_dish(dietary_conflicts=["no_dairy"]),
    )
    assert result.dietary_factor == pytest.approx(1.0)
    assert result.violated_dietary == []


def test_satisfaction_never_leaves_zero_to_one() -> None:
    """네 항이 전부 0~1 비율이므로 곱도 그 안에 머문다. 이식할 때 clamp가 필요 없다."""
    worst = _evaluate(
        persona=_persona(dietary=["no_meat", "no_dairy"]),
        dish=_dish(
            need_tags=[],
            price=10_000,
            dietary_conflicts=["no_meat", "no_dairy"],
            params={"heat": 0, "cook_time": 0, "seasoning": 0},
        ),
    )
    assert 0.0 <= worst.total <= 1.0
    assert 0.0 <= _evaluate().total <= 1.0


# ---------------------------------------------------------------- 이상 구간


def test_inverted_ideal_range_is_rejected() -> None:
    with pytest.raises(ValueError, match="뒤집혔다"):
        IdealRange(low=60, high=40)
