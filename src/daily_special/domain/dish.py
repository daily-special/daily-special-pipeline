"""요리 — `dishes.json` 한 항목의 정의.

**처음으로 다른 생성물을 참조하는 콘텐츠다.** 손님과 재료는 ProjectBible 어휘만 보면
검증이 끝나는데, 요리는 "이 `ingredient_id`가 실재하는가"를 봐야 한다. 도메인이 파일을
읽을 수 없으므로(계층 규약) 알려진 재료를 인자로 받는다.

그 덕에 여기서만 할 수 있는 검사가 생긴다 — **원가가 판매가를 넘는가.** 재료를 모르면
볼 수 없고, 안 보면 만들수록 손해인 요리가 10단계 시뮬레이션까지 살아남는다.

여기 **없는** 것이 둘 있고 둘 다 유도된다.

- `dietary_conflicts` — 쓴 재료들의 저촉 합집합이다. 계약에 넣으면 재료를 고쳤을 때
  어긋날 수 있고, 그러면 아무도 모르는 채 육류 불가 손님에게 고기가 나간다
- 상시/오늘의 메뉴 구분 — 신선 재료가 하나라도 들어가면 오늘의 메뉴로만 가능하다.
  어느 것을 실제로 상시로 걸지는 플레이어가 정하므로 런타임이다
"""

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import IngredientKind, ProjectBible
from daily_special.domain.charset import check_charset
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import Issue, Severity

_DISH_ID = re.compile(r"^dish_[a-z0-9_]+$")
_MAX_ID_LEN = 64

type IngredientMap = Mapping[str, Ingredient]
"""재료 ID → 재료. 요리 검증이 바깥을 보는 창구다."""


class Dish(BaseModel):
    """요리 하나. 정체성만 담고 품질은 담지 않는다.

    파라미터 3축(불 세기·조리 시간·간)은 여기 없다 — 그것은 **플레이어가 매번 맞추는
    값**이지 요리의 속성이 아니다. 요리 선택이 욕구에 답하고 파라미터가 취향에 답하는
    두 축 분리가 설계의 핵심이라, 한쪽이 다른 쪽으로 새면 안 된다.
    """

    model_config = ConfigDict(frozen=True)

    dish_id: str
    """`dish_` 접두사 슬러그. 발행 후 불변."""

    name: str
    """한국어 표시명."""

    description: str
    """어떤 요리인지. 플레이어에게 보이고, 대사 생성기도 읽는다."""

    need_tags: list[str]
    """이 요리가 답하는 욕구들. 손님 욕구와 같은 어휘를 쓴다.

    갈라두면 만족도 계산에 매핑이 하나 더 생긴다.
    """

    ingredient_ids: list[str]
    """필요한 재료들. 실재하는 `ingredient_id`여야 한다."""

    base_price: int
    """고정가. 플레이어가 정하지 않는다 (설계서 6장)."""

    def cost(self, ingredients: IngredientMap) -> int:
        """재료 원가 합. 모르는 재료는 0으로 친다 — 그쪽은 따로 ERROR가 난다."""
        return sum(ingredients[key].base_price for key in self.ingredient_ids if key in ingredients)

    def dietary_conflicts(self, ingredients: IngredientMap) -> list[str]:
        """쓴 재료들의 저촉 합집합. 계약에 저장하지 않고 필요할 때 유도한다."""
        conflicts: list[str] = []
        for key in self.ingredient_ids:
            ingredient = ingredients.get(key)
            if ingredient is None:
                continue
            conflicts += [c for c in ingredient.dietary_conflicts if c not in conflicts]
        return conflicts

    def is_standing_capable(self, ingredients: IngredientMap) -> bool:
        """보존 재료만으로 만들 수 있는가. 상시 메뉴에 걸 수 있다는 뜻이다."""
        return all(
            ingredients[key].kind is IngredientKind.PRESERVED
            for key in self.ingredient_ids
            if key in ingredients
        )


def check_dish(dish: Dish, bible: ProjectBible, ingredients: IngredientMap) -> list[Issue]:
    """계약·어휘·실재하는 재료에 맞는지 본다."""
    issues: list[Issue] = []

    issues += _check_id(dish)
    issues += _check_need_tags(dish, bible)
    issues += _check_ingredients(dish, bible, ingredients)
    issues += _check_price(dish, bible, ingredients)
    issues += _check_substance(dish, bible)
    issues += _check_charset(dish, bible)

    return issues


def _check_charset(dish: Dish, bible: ProjectBible) -> list[Issue]:
    """화면에 뜨는 텍스트가 클라이언트 폰트로 그려지는가."""
    issues: list[Issue] = []
    for field, value in (("name", dish.name), ("description", dish.description)):
        issues += check_charset(value, field, bible)
    return issues


def check_dish_batch(
    dishes: Sequence[Dish], bible: ProjectBible, ingredients: IngredientMap
) -> list[Issue]:
    """배치 전체를 본다. ID 중복과, 메뉴가 메뉴 구실을 하는가."""
    issues: list[Issue] = []

    for index, dish in enumerate(dishes):
        issues += [
            issue.model_copy(update={"field": f"items[{index}].{issue.field}"})
            for issue in check_dish(dish, bible, ingredients)
        ]

    counts = Counter(dish.dish_id for dish in dishes)
    for index, dish in enumerate(dishes):
        if counts[dish.dish_id] > 1:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"items[{index}].dish_id",
                    message=(
                        f"ID '{dish.dish_id}'가 이 배치 안에서 겹친다. 요리마다 다른 ID를 쓴다"
                    ),
                )
            )

    issues += _check_menu_coverage(dishes, bible, ingredients)
    return issues


def _check_menu_coverage(
    dishes: Sequence[Dish], bible: ProjectBible, ingredients: IngredientMap
) -> list[Issue]:
    """메뉴 전체가 성립하는지 본다. **ERROR가 아니라 WARNING이다.**

    생성이 배치로 나뉘므로 한 배치가 전부를 덮을 수는 없다. ERROR로 두면 5개짜리
    배치가 영원히 재생성만 하게 된다 — 어떤 생성물도 통과할 수 없는 합격선은
    합격선이 아니다. 최종 패키지를 볼 때 사람이 읽으라고 남긴다.
    """
    if not dishes:
        return []

    issues: list[Issue] = []

    answered = {tag for dish in dishes for tag in dish.need_tags}
    unanswered = [need.key for need in bible.needs if need.key not in answered]
    if unanswered:
        issues.append(
            Issue(
                severity=Severity.WARNING,
                field="items",
                message=(
                    f"어떤 요리도 답하지 않는 욕구가 있다: {', '.join(unanswered)}. "
                    "그 욕구를 가진 손님은 무엇을 내도 빗나간다"
                ),
            )
        )

    if not any(dish.is_standing_capable(ingredients) for dish in dishes):
        issues.append(
            Issue(
                severity=Severity.WARNING,
                field="items",
                message=(
                    "보존 재료만으로 만들 수 있는 요리가 하나도 없다. "
                    "상시 메뉴에 걸 것이 없어 식당의 정체성을 정할 수 없다"
                ),
            )
        )

    return issues


def _check_id(dish: Dish) -> list[Issue]:
    if not _DISH_ID.match(dish.dish_id):
        return [
            Issue(
                severity=Severity.ERROR,
                field="dish_id",
                message=(
                    f"'{dish.dish_id}'는 요리 ID 문법이 아니다. "
                    "'dish_'로 시작하고 소문자·숫자·밑줄만 쓴다 (예: dish_herb_porridge)"
                ),
            )
        ]
    if len(dish.dish_id) > _MAX_ID_LEN:
        return [
            Issue(
                severity=Severity.ERROR,
                field="dish_id",
                message=f"요리 ID가 {_MAX_ID_LEN}자를 넘는다: {len(dish.dish_id)}자",
            )
        ]
    return []


def _check_need_tags(dish: Dish, bible: ProjectBible) -> list[Issue]:
    """욕구를 전부 붙이면 누구에게나 만점이라 요리를 고르는 행위가 사라진다."""
    issues: list[Issue] = []
    known = ", ".join(need.key for need in bible.needs)

    issues += [
        Issue(
            severity=Severity.ERROR,
            field="need_tags",
            message=f"'{tag}'는 없는 욕구다. 다음 중에서 고른다: {known}",
        )
        for tag in dish.need_tags
        if bible.find_need(tag) is None
    ]

    if not dish.need_tags:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="need_tags",
                message="답하는 욕구가 없다. 어떤 손님도 이 요리를 원할 이유가 없다",
            )
        )
    elif len(dish.need_tags) > bible.generation.max_dish_need_tags:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="need_tags",
                message=(
                    f"욕구를 {len(dish.need_tags)}개 답한다. "
                    f"{bible.generation.max_dish_need_tags}개 이하여야 한다 — "
                    "다 붙이면 누구에게나 만점이라 무엇을 낼지 고를 이유가 없어진다"
                ),
            )
        )

    if len(set(dish.need_tags)) != len(dish.need_tags):
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="need_tags",
                message="같은 욕구를 두 번 적었다",
            )
        )

    return issues


def _check_ingredients(dish: Dish, bible: ProjectBible, ingredients: IngredientMap) -> list[Issue]:
    """실재하는 재료인가, 개수가 조합이라 할 만한가."""
    issues: list[Issue] = []
    spec = bible.generation

    issues += [
        Issue(
            severity=Severity.ERROR,
            field="ingredient_ids",
            message=(
                f"'{key}'는 없는 재료다. ingredients.json에 있는 것만 쓴다 — "
                "없는 재료를 요구하는 요리는 영원히 만들 수 없다"
            ),
        )
        for key in dish.ingredient_ids
        if key not in ingredients
    ]

    if len(set(dish.ingredient_ids)) != len(dish.ingredient_ids):
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="ingredient_ids",
                message="같은 재료를 두 번 적었다",
            )
        )

    count = len(dish.ingredient_ids)
    if not spec.min_dish_ingredients <= count <= spec.max_dish_ingredients:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="ingredient_ids",
                message=(
                    f"재료가 {count}개다. "
                    f"{spec.min_dish_ingredients}~{spec.max_dish_ingredients}개여야 한다 — "
                    "너무 적으면 조합이 아니고, 너무 많으면 그날 산 것으로 만들 수 없다"
                ),
            )
        )

    return issues


def _check_price(dish: Dish, bible: ProjectBible, ingredients: IngredientMap) -> list[Issue]:
    """범위 안인가, 그리고 **원가를 넘는가**.

    원가 검사는 재료를 알아야만 할 수 있다. 안 하면 만들수록 손해인 요리가
    10단계 시뮬레이션까지 살아남는다.
    """
    issues: list[Issue] = []
    economy = bible.economy

    if not economy.dish_price_min <= dish.base_price <= economy.dish_price_max:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="base_price",
                message=(
                    f"가격 {dish.base_price}가 범위 "
                    f"{economy.dish_price_min}~{economy.dish_price_max}를 벗어난다"
                ),
            )
        )

    # 모르는 재료가 섞여 있으면 원가가 실제보다 낮게 나온다. 그쪽 ERROR가 먼저 고쳐져야 한다.
    if all(key in ingredients for key in dish.ingredient_ids):
        cost = dish.cost(ingredients)
        if cost >= dish.base_price:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field="base_price",
                    message=(
                        f"재료 원가 {cost}가 판매가 {dish.base_price} 이상이다. "
                        "팔수록 손해라 식당이 굴러가지 않는다"
                    ),
                )
            )

    return issues


def _check_substance(dish: Dish, bible: ProjectBible) -> list[Issue]:
    issues: list[Issue] = []

    if not dish.name.strip():
        issues.append(Issue(severity=Severity.ERROR, field="name", message="이름이 비어 있다"))

    minimum = bible.generation.min_text_length
    if len(dish.description.strip()) < minimum:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="description",
                message=(
                    f"설명이 너무 짧다({len(dish.description.strip())}자). "
                    f"{minimum}자 이상으로 쓴다"
                ),
            )
        )

    return issues
