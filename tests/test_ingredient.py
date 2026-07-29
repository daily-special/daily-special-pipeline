"""재료 스키마와 검증을 고정한다.

손님과 같은 구조라 새로 정할 것이 없다. 여기서 지키는 것은 재료에만 있는 둘이다 —
**식이 저촉의 출발점**이라는 것과, **두 종류가 다 있어야 한다**는 것.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.domain.bible import IngredientKind, ProjectBible
from daily_special.domain.ingredient import Ingredient, check_ingredient, check_ingredient_batch
from daily_special.domain.issue import has_errors
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"
INGREDIENTS_PATH = REPO_ROOT / "out" / "mock" / "ingredients.json"


def _load() -> Package[Ingredient]:
    raw = json.loads(INGREDIENTS_PATH.read_text(encoding="utf-8"))
    return Package[Ingredient].model_validate(raw)


def _test_bible() -> ProjectBible:
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
            "dietary_constraints": [named("no_meat"), named("no_spicy")],
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


def _ingredient(**overrides: Any) -> Ingredient:
    data: dict[str, Any] = {
        "ingredient_id": "ingredient_test_herb",
        "name": "시험 허브",
        "kind": "fresh",
        "description": "테스트에만 나온다.",
        "base_price": 3,
        "dietary_conflicts": [],
    }
    data.update(overrides)
    return Ingredient.model_validate(data)


# ---------------------------------------------------------------- 목 파일


def test_mock_package_parses() -> None:
    package = _load()
    assert package.kind is PackageKind.INGREDIENTS
    assert package.schema_version == SCHEMA_VERSION


def test_mock_package_declares_the_current_bible() -> None:
    """설정 버전을 올리면 여기가 깨진다. 목 파일을 다시 보라는 뜻이다."""
    assert _load().bible_version == load_bible(BIBLE_PATH).version


def test_mock_ingredients_pass_validation() -> None:
    bible = load_bible(BIBLE_PATH)
    issues = check_ingredient_batch(_load().items, bible)
    assert not issues, [issue.message for issue in issues]


def test_mock_covers_every_dietary_constraint() -> None:
    """식이 제약이 하나라도 어떤 재료에도 없으면, 그 제약을 가진 손님은 무엇이든 먹는다.

    제약이 있으나 마나가 되는 것을 목 데이터 단계에서 잡는다.
    """
    bible = load_bible(BIBLE_PATH)
    covered = {key for item in _load().items for key in item.dietary_conflicts}
    assert covered == {item.key for item in bible.dietary_constraints}


def test_mock_has_both_kinds() -> None:
    kinds = {item.kind for item in _load().items}
    assert kinds == set(IngredientKind)


# ---------------------------------------------------------------- 검증


def test_valid_ingredient_has_no_issues() -> None:
    assert check_ingredient(_ingredient(), _test_bible()) == []


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"ingredient_id": "river_herb"}, "ingredient_id"),
        ({"ingredient_id": "ingredient_River_Herb"}, "ingredient_id"),
        ({"dietary_conflicts": ["no_gluten"]}, "dietary_conflicts"),
        ({"base_price": 0}, "base_price"),
        ({"base_price": 99}, "base_price"),
        ({"description": "  "}, "description"),
    ],
)
def test_violation_is_an_error(overrides: dict[str, Any], field: str) -> None:
    issues = check_ingredient(_ingredient(**overrides), _test_bible())
    assert has_errors(issues)
    assert [issue.field for issue in issues] == [field]


def test_price_outside_the_range_is_caught_here() -> None:
    """재료 하나가 요리값에 육박하면 만들수록 손해다.

    어느 규칙에도 걸리지 않고 10단계 시뮬레이션에 가서야 드러나므로 여기서 잡는다.
    """
    issues = check_ingredient(_ingredient(base_price=50), _test_bible())
    assert has_errors(issues)
    assert "1~8" in issues[0].message


def test_unknown_dietary_key_is_rejected() -> None:
    """어휘 밖의 제약을 저촉한다고 하면 어느 손님도 그것을 피할 수 없다."""
    issues = check_ingredient(_ingredient(dietary_conflicts=["no_gluten"]), _test_bible())
    assert has_errors(issues)
    assert "no_meat" in issues[0].message


# ---------------------------------------------------------------- 배치


def test_missing_kind_is_an_error() -> None:
    """신선만 나오면 상시 메뉴를 짤 수 없다. 게임의 한 층이 통째로 죽는다."""
    only_fresh = [
        _ingredient(ingredient_id="ingredient_a", kind="fresh"),
        _ingredient(ingredient_id="ingredient_b", kind="fresh"),
    ]
    issues = check_ingredient_batch(only_fresh, _test_bible())

    assert has_errors(issues)
    assert issues[0].field == "items"
    assert "preserved" in issues[0].message


def test_both_kinds_present_is_fine() -> None:
    both = [
        _ingredient(ingredient_id="ingredient_a", kind="fresh"),
        _ingredient(ingredient_id="ingredient_b", kind="preserved"),
    ]
    assert check_ingredient_batch(both, _test_bible()) == []


def test_duplicate_id_in_batch_is_an_error() -> None:
    same = [
        _ingredient(ingredient_id="ingredient_a", kind="fresh"),
        _ingredient(ingredient_id="ingredient_a", kind="preserved"),
    ]
    issues = check_ingredient_batch(same, _test_bible())

    assert [issue.field for issue in issues] == [
        "items[0].ingredient_id",
        "items[1].ingredient_id",
    ]


def test_empty_batch_does_not_demand_kinds() -> None:
    """빈 배치는 종류가 빠진 것이 아니라 아무것도 없는 것이다. 다른 문제다."""
    assert check_ingredient_batch([], _test_bible()) == []
