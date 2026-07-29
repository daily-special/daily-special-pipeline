"""재료 생성.

`generate_guests`와 같은 구조다 — 배치로 뽑고, 어긴 것만 다시 뽑고, 소진해도 버리지
않는다. 새로 정할 것이 없어서 짧다. 5·6단계에서 세운 층이 콘텐츠 종류를 타지 않는다는
것이 여기서 처음 확인된다.
"""

from pydantic import BaseModel, ConfigDict

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import (
    build_ingredient_context,
    build_ingredient_instruction,
)
from daily_special.application.regenerate import MAX_REGENERATIONS, partition_by_errors
from daily_special.application.schema_builder import build_ingredient_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.ingredient import Ingredient, check_ingredient, check_ingredient_batch
from daily_special.domain.issue import Issue, Severity


class IngredientGeneration(BaseModel):
    """생성 결과와 그 검증 결과."""

    model_config = ConfigDict(frozen=True)

    ingredients: list[Ingredient]
    issues: list[Issue]
    call_count: int
    """오프라인 배치라 이 값이 곧 비용이다."""


async def generate_ingredients(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    count: int,
    max_regenerations: int = MAX_REGENERATIONS,
) -> IngredientGeneration:
    """재료 count개를 만든다. 규칙을 어긴 것만 그 개수만큼 다시 뽑는다."""
    if max_regenerations < 0:
        raise DomainError(f"max_regenerations는 0 이상이어야 한다: {max_regenerations}")

    schema = build_ingredient_batch_schema(bible)

    async def call(needed: int, kept: list[Ingredient], feedback: list[Issue]) -> list[Ingredient]:
        response = await llm.generate(
            instruction=build_ingredient_instruction(),
            context=build_ingredient_context(bible, needed, existing=kept, issues=feedback),
            schema=schema.model,
            tier=Tier.QUALITY,
        )
        return schema.to_ingredients(response)

    ingredients = await call(count, [], [])
    calls = 1

    for _ in range(max_regenerations):
        partition = partition_by_errors(
            ingredients,
            check=lambda item: check_ingredient(item, bible),
            id_of=lambda item: item.ingredient_id,
        )
        if not partition.rejected:
            break

        replacements = await call(len(partition.rejected), partition.kept, partition.issues)
        calls += 1
        ingredients = [*partition.kept, *replacements]

    # 소진해도 그대로 들고 나간다. 고쳐지지 않은 것도 버리지 않는다 (규약 5-3).
    issues = check_ingredient_batch(ingredients, bible)
    issues += _check_count(ingredients, count)

    return IngredientGeneration(ingredients=ingredients, issues=issues, call_count=calls)


def _check_count(ingredients: list[Ingredient], requested: int) -> list[Issue]:
    """요청한 수와 다르면 알리되 배치를 되던지지 않는다."""
    if len(ingredients) == requested:
        return []

    return [
        Issue(
            severity=Severity.WARNING,
            field="items",
            message=f"재료 {requested}개를 요청했으나 {len(ingredients)}개가 왔다",
        )
    ]
