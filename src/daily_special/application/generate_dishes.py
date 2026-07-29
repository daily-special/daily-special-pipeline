"""요리 생성.

손님·재료와 같은 구조지만 입력이 하나 더 있다 — **재료 목록.** 요리는 바깥을
참조하는 첫 콘텐츠라, 무엇을 쓸 수 있는지 모르면 만들 수 없다.

재료가 프롬프트와 검증 양쪽에 들어간다. 스키마로는 막을 수 없기 때문이다 —
재료 ID는 어휘가 아니라 다른 생성물의 값이라 enum으로 굳힐 수 없다.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import build_dish_context, build_dish_instruction
from daily_special.application.regenerate import MAX_REGENERATIONS, partition_by_errors
from daily_special.application.schema_builder import build_dish_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.dish import Dish, check_dish, check_dish_batch
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import Issue, Severity


class DishGeneration(BaseModel):
    """생성 결과와 그 검증 결과."""

    model_config = ConfigDict(frozen=True)

    dishes: list[Dish]
    issues: list[Issue]
    call_count: int


async def generate_dishes(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    ingredients: Sequence[Ingredient],
    count: int,
    max_regenerations: int = MAX_REGENERATIONS,
) -> DishGeneration:
    """요리 count개를 만든다. 주어진 재료로만 만들 수 있다."""
    if max_regenerations < 0:
        raise DomainError(f"max_regenerations는 0 이상이어야 한다: {max_regenerations}")
    if not ingredients:
        raise DomainError("재료가 없으면 요리를 만들 수 없다")

    schema = build_dish_batch_schema(bible)
    known = {item.ingredient_id: item for item in ingredients}

    async def call(needed: int, kept: list[Dish], feedback: list[Issue]) -> list[Dish]:
        response = await llm.generate(
            instruction=build_dish_instruction(),
            context=build_dish_context(bible, needed, ingredients, existing=kept, issues=feedback),
            schema=schema.model,
            tier=Tier.QUALITY,
        )
        return schema.to_dishes(response)

    dishes = await call(count, [], [])
    calls = 1

    for _ in range(max_regenerations):
        partition = partition_by_errors(
            dishes,
            check=lambda dish: check_dish(dish, bible, known),
            id_of=lambda dish: dish.dish_id,
        )
        if not partition.rejected:
            break

        replacements = await call(len(partition.rejected), partition.kept, partition.issues)
        calls += 1
        dishes = [*partition.kept, *replacements]

    # 소진해도 그대로 들고 나간다 (규약 5-3).
    issues = check_dish_batch(dishes, bible, known)
    issues += _check_count(dishes, count)

    return DishGeneration(dishes=dishes, issues=issues, call_count=calls)


def _check_count(dishes: list[Dish], requested: int) -> list[Issue]:
    if len(dishes) == requested:
        return []

    return [
        Issue(
            severity=Severity.WARNING,
            field="items",
            message=f"요리 {requested}개를 요청했으나 {len(dishes)}개가 왔다",
        )
    ]
