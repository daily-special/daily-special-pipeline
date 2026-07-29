"""요리 검토.

손님 쪽과 같은 모양이지만 거르는 조건에 재료가 낀다 — 요리는 실재하는 재료를
참조해야 유효하고, 없는 재료를 요구하는 요리는 유료 검토에 보낼 값이 없다.
"""

from collections.abc import Sequence

from daily_special.application.port.llm import LlmPort
from daily_special.application.prompt import (
    build_dish_review_context,
    build_dish_review_instruction,
)
from daily_special.application.review import Review, review_subjects
from daily_special.domain.bible import ProjectBible
from daily_special.domain.dish import Dish, IngredientMap, check_dish
from daily_special.domain.issue import has_errors


async def review_dishes(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    dishes: Sequence[Dish],
    ingredients: IngredientMap,
) -> Review:
    """메뉴를 통째로 검토한다. 규칙이 깨진 요리는 넘기지 않는다."""
    reviewable = [dish for dish in dishes if not has_errors(check_dish(dish, bible, ingredients))]
    if len(reviewable) < 2:
        return Review(issues=[], call_count=0)

    return await review_subjects(
        llm=llm,
        instruction=build_dish_review_instruction(),
        context=build_dish_review_context(reviewable, ingredients),
        subject_ids=[dish.dish_id for dish in reviewable],
    )
