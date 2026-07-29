"""요리 스키마와 검증을 고정한다.

요리는 **바깥을 참조하는 첫 콘텐츠**다. 그래서 여기서 지키는 것은 앞의 둘에 없던
것들이다 — 없는 재료를 요구하지 않는가, 원가보다 비싼가, 메뉴가 메뉴 구실을 하는가.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.domain.bible import ProjectBible
from daily_special.domain.dish import Dish, check_dish, check_dish_batch
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import Severity, has_errors
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"
PACKAGE_DIR = REPO_ROOT / "out" / "packages" / SCHEMA_VERSION


def _load_dishes() -> Package[Dish]:
    return Package[Dish].model_validate(
        json.loads((PACKAGE_DIR / "dishes.json").read_text(encoding="utf-8"))
    )


def _load_ingredients() -> dict[str, Ingredient]:
    package = Package[Ingredient].model_validate(
        json.loads((PACKAGE_DIR / "ingredients.json").read_text(encoding="utf-8"))
    )
    return {item.ingredient_id: item for item in package.items}


def _test_bible() -> ProjectBible:
    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling"), named("mild"), named("special")],
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
            },
            "scoring": {
                "need_floor": 0.15,
                "axis_tolerance": 25,
                "budget_overrun_ratio": 1.5,
                "dietary_violation_factor": 0.1,
            },
        }
    )


def _ingredients() -> dict[str, Ingredient]:
    def make(key: str, kind: str, price: int, conflicts: list[str]) -> Ingredient:
        return Ingredient(
            ingredient_id=key,
            name=key,
            kind=kind,  # type: ignore[arg-type]
            description="테스트 재료.",
            base_price=price,
            dietary_conflicts=conflicts,
        )

    return {
        "ingredient_grain": make("ingredient_grain", "preserved", 1, []),
        "ingredient_salt": make("ingredient_salt", "preserved", 1, []),
        "ingredient_meat": make("ingredient_meat", "fresh", 5, ["no_meat"]),
    }


def _dish(**overrides: Any) -> Dish:
    data: dict[str, Any] = {
        "dish_id": "dish_test_stew",
        "name": "시험 국",
        "description": "테스트에만 나온다.",
        "need_tags": ["filling"],
        "ingredient_ids": ["ingredient_grain", "ingredient_salt"],
        "base_price": 8,
    }
    data.update(overrides)
    return Dish.model_validate(data)


# ---------------------------------------------------------------- 목 파일


def test_mock_package_parses() -> None:
    package = _load_dishes()
    assert package.kind is PackageKind.DISHES
    assert package.schema_version == SCHEMA_VERSION


def test_mock_package_declares_the_current_bible() -> None:
    assert _load_dishes().bible_version == load_bible(BIBLE_PATH).version


def test_mock_dishes_pass_validation() -> None:
    """목 요리가 목 재료만 참조하는지까지 함께 확인된다."""
    issues = check_dish_batch(_load_dishes().items, load_bible(BIBLE_PATH), _load_ingredients())
    assert not issues, [issue.message for issue in issues]


def test_mock_menu_can_stand() -> None:
    """보존 재료만으로 되는 요리가 있어야 상시 메뉴를 걸 수 있다."""
    ingredients = _load_ingredients()
    assert any(dish.is_standing_capable(ingredients) for dish in _load_dishes().items)


def test_mock_dietary_conflicts_are_derived() -> None:
    """요리는 저촉을 저장하지 않는다. 재료에서 유도된다."""
    ingredients = _load_ingredients()
    stew = next(d for d in _load_dishes().items if d.dish_id == "dish_boar_bone_stew")
    porridge = next(d for d in _load_dishes().items if d.dish_id == "dish_barley_porridge")

    assert stew.dietary_conflicts(ingredients) == ["no_meat"]
    assert porridge.dietary_conflicts(ingredients) == []
    assert "dietary_conflicts" not in Dish.model_fields


def test_parameters_are_not_dish_properties() -> None:
    """요리 선택은 욕구에, 파라미터는 취향에 답한다. 두 축이 섞이면 설계가 무너진다."""
    assert not set(Dish.model_fields) & {"params", "heat", "cook_time", "seasoning"}


# ---------------------------------------------------------------- 검증


def test_valid_dish_has_no_issues() -> None:
    assert check_dish(_dish(), _test_bible(), _ingredients()) == []


def test_unknown_ingredient_is_an_error() -> None:
    """없는 재료를 요구하는 요리는 영원히 만들 수 없다."""
    issues = check_dish(
        _dish(ingredient_ids=["ingredient_grain", "ingredient_ghost"]),
        _test_bible(),
        _ingredients(),
    )
    assert has_errors(issues)
    assert issues[0].field == "ingredient_ids"


def test_cost_above_price_is_an_error() -> None:
    """재료를 알아야만 할 수 있는 검사다.

    안 하면 만들수록 손해인 요리가 10단계 시뮬레이션까지 살아남는다.
    """
    issues = check_dish(
        _dish(ingredient_ids=["ingredient_meat", "ingredient_grain"], base_price=6),
        _test_bible(),
        _ingredients(),
    )
    assert has_errors(issues)
    assert any("원가" in issue.message for issue in issues)


def test_cost_check_is_skipped_when_an_ingredient_is_unknown() -> None:
    """모르는 재료가 있으면 원가가 실제보다 낮게 나온다. 그쪽 ERROR가 먼저다."""
    issues = check_dish(
        _dish(ingredient_ids=["ingredient_ghost", "ingredient_grain"]),
        _test_bible(),
        _ingredients(),
    )
    assert not any("원가" in issue.message for issue in issues)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"dish_id": "herb_porridge"}, "dish_id"),
        ({"need_tags": []}, "need_tags"),
        ({"need_tags": ["nonexistent"]}, "need_tags"),
        ({"need_tags": ["filling", "mild", "special"]}, "need_tags"),
        ({"need_tags": ["filling", "filling"]}, "need_tags"),
        ({"ingredient_ids": ["ingredient_grain"]}, "ingredient_ids"),
        ({"ingredient_ids": ["ingredient_grain", "ingredient_grain"]}, "ingredient_ids"),
        ({"base_price": 99}, "base_price"),
        ({"description": " "}, "description"),
    ],
)
def test_violation_is_an_error(overrides: dict[str, Any], field: str) -> None:
    issues = check_dish(_dish(**overrides), _test_bible(), _ingredients())
    assert has_errors(issues)
    assert issues[0].field == field


# ---------------------------------------------------------------- 배치


def test_unanswered_need_is_a_warning_not_an_error() -> None:
    """생성이 배치로 나뉘므로 한 배치가 모든 욕구를 덮을 수는 없다.

    ERROR로 두면 작은 배치가 영원히 재생성만 한다 — 어떤 생성물도 통과할 수 없는
    합격선은 합격선이 아니다.
    """
    dishes = [_dish(), _dish(dish_id="dish_other", need_tags=["mild"])]
    issues = check_dish_batch(dishes, _test_bible(), _ingredients())

    assert not has_errors(issues)
    assert any(
        issue.severity is Severity.WARNING and "special" in issue.message for issue in issues
    )


def test_menu_without_a_standing_dish_warns() -> None:
    """상시 메뉴에 걸 것이 없으면 식당의 정체성을 정할 수 없다."""
    fresh_only = [
        _dish(ingredient_ids=["ingredient_meat", "ingredient_grain"], base_price=10),
        _dish(dish_id="dish_other", ingredient_ids=["ingredient_meat", "ingredient_salt"]),
    ]
    issues = check_dish_batch(fresh_only, _test_bible(), _ingredients())

    assert any("상시 메뉴" in issue.message for issue in issues)


def test_duplicate_id_in_batch_is_an_error() -> None:
    issues = check_dish_batch([_dish(), _dish()], _test_bible(), _ingredients())
    assert [issue.field for issue in issues if issue.severity is Severity.ERROR] == [
        "items[0].dish_id",
        "items[1].dish_id",
    ]


def test_empty_batch_is_fine() -> None:
    assert check_dish_batch([], _test_bible(), _ingredients()) == []
