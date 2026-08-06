"""재료 — `ingredients.json` 한 항목의 정의.

**재료가 식이 저촉의 출발점이다.** 만족도 엔진은 `ServedDish.dietary_conflicts`를
결과로만 받는데, 그 결과를 만드는 것이 여기다. 고기를 쓴 요리가 `no_meat`에 걸리는
판정은 재료가 `no_meat`를 들고 있기 때문에 성립한다.

저촉을 태그(`meat`/`dairy`/...)로 두고 태그 → 제약 매핑을 따로 만들지 않는다.
매핑이 하나 더 생기면 어휘가 늘 때마다 두 곳을 고쳐야 한다 — 욕구와 요리 태그를
같은 어휘로 둔 것과 같은 이유다.
"""

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import IngredientKind, ProjectBible
from daily_special.domain.charset import check_charset
from daily_special.domain.issue import Issue, Severity

_INGREDIENT_ID = re.compile(r"^ingredient_[a-z0-9_]+$")
"""데이터 계약 2절의 ID 문법."""

_MAX_ID_LEN = 64


class Ingredient(BaseModel):
    """재료 하나. 발행 후 바뀌지 않는 것만 담는다.

    오늘 시장에 무엇이 나왔는지, 오늘 얼마인지는 여기 없다 — 플레이 중에 바뀌므로
    서버가 소유한다 (계약 5절). 여기 있는 `base_price`는 변동의 기준선이다.
    """

    model_config = ConfigDict(frozen=True)

    ingredient_id: str
    """`ingredient_` 접두사 슬러그. 발행 후 불변."""

    name: str
    """한국어 표시명."""

    kind: IngredientKind
    """신선이냐 보존이냐. 메뉴 두 층과 맞물리는 이 게임의 경제 축이다.

    신선은 그날 안 쓰면 상해서 장보기를 도박으로 만들고, 보존은 재고가 유지되어
    상시 메뉴의 약속 비용이 된다.
    """

    description: str
    """어떤 재료인지. 요리 생성기가 이것을 읽고 조합을 짠다."""

    base_price: int
    """기준 단가. 시장의 그날 시세는 이 값을 중심으로 서버가 흔든다."""

    dietary_conflicts: list[str] = []
    """이 재료가 저촉하는 식이 제약 키. 요리의 저촉은 재료들의 합집합이다."""


def check_ingredient(ingredient: Ingredient, bible: ProjectBible) -> list[Issue]:
    """계약과 ProjectBible에 맞는지 본다. 손님과 같은 규칙이다 — 예외가 아니라 Issue."""
    issues: list[Issue] = []

    issues += _check_id(ingredient)
    issues += _check_dietary(ingredient, bible)
    issues += _check_price(ingredient, bible)
    issues += _check_substance(ingredient, bible)
    issues += _check_charset(ingredient, bible)

    return issues


def _check_charset(ingredient: Ingredient, bible: ProjectBible) -> list[Issue]:
    """화면에 뜨는 텍스트가 클라이언트 폰트로 그려지는가."""
    issues: list[Issue] = []
    for field, value in (("name", ingredient.name), ("description", ingredient.description)):
        issues += check_charset(value, field, bible)
    return issues


def check_ingredient_batch(ingredients: Sequence[Ingredient], bible: ProjectBible) -> list[Issue]:
    """배치 전체를 본다. 개별 검사로는 잡히지 않는 둘을 여기서 잡는다.

    ID 중복과, **두 종류가 다 있는가**다. 신선만 나오면 상시 메뉴를 짤 수 없고
    보존만 나오면 장보기 도박이 사라진다 — 어느 쪽이든 게임의 한 층이 통째로 죽는다.
    """
    issues: list[Issue] = []

    for index, ingredient in enumerate(ingredients):
        issues += [
            issue.model_copy(update={"field": f"items[{index}].{issue.field}"})
            for issue in check_ingredient(ingredient, bible)
        ]

    counts = Counter(item.ingredient_id for item in ingredients)
    for index, ingredient in enumerate(ingredients):
        if counts[ingredient.ingredient_id] > 1:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"items[{index}].ingredient_id",
                    message=(
                        f"ID '{ingredient.ingredient_id}'가 이 배치 안에서 겹친다. "
                        "재료마다 다른 ID를 쓴다"
                    ),
                )
            )

    issues += _check_kind_coverage(ingredients)
    return issues


def _check_kind_coverage(ingredients: Sequence[Ingredient]) -> list[Issue]:
    """신선과 보존이 둘 다 있어야 메뉴 두 층이 성립한다."""
    if not ingredients:
        return []

    present = {item.kind for item in ingredients}
    missing = [kind for kind in IngredientKind if kind not in present]

    return [
        Issue(
            severity=Severity.ERROR,
            field="items",
            message=(
                f"'{kind}' 재료가 하나도 없다. 신선과 보존이 둘 다 있어야 "
                "오늘의 메뉴와 상시 메뉴가 각각 성립한다"
            ),
        )
        for kind in missing
    ]


def _check_id(ingredient: Ingredient) -> list[Issue]:
    if not _INGREDIENT_ID.match(ingredient.ingredient_id):
        return [
            Issue(
                severity=Severity.ERROR,
                field="ingredient_id",
                message=(
                    f"'{ingredient.ingredient_id}'는 재료 ID 문법이 아니다. "
                    "'ingredient_'로 시작하고 소문자·숫자·밑줄만 쓴다 "
                    "(예: ingredient_river_herb)"
                ),
            )
        ]
    if len(ingredient.ingredient_id) > _MAX_ID_LEN:
        return [
            Issue(
                severity=Severity.ERROR,
                field="ingredient_id",
                message=f"재료 ID가 {_MAX_ID_LEN}자를 넘는다: {len(ingredient.ingredient_id)}자",
            )
        ]
    return []


def _check_dietary(ingredient: Ingredient, bible: ProjectBible) -> list[Issue]:
    """어휘 밖의 제약을 저촉한다고 하면 어느 손님도 그것을 피할 수 없다."""
    known = ", ".join(item.key for item in bible.dietary_constraints)
    return [
        Issue(
            severity=Severity.ERROR,
            field="dietary_conflicts",
            message=f"'{key}'는 없는 식이 제약이다. 다음 중에서 고른다: {known}",
        )
        for key in ingredient.dietary_conflicts
        if bible.find_dietary(key) is None
    ]


def _check_price(ingredient: Ingredient, bible: ProjectBible) -> list[Issue]:
    """단가가 범위를 벗어나면 경제가 어긋난다.

    재료 하나가 요리값에 육박하면 만들수록 손해가 되는데, 그것은 어느 규칙에도
    걸리지 않고 10단계 시뮬레이션에 가서야 드러난다.
    """
    economy = bible.economy
    if economy.ingredient_price_min <= ingredient.base_price <= economy.ingredient_price_max:
        return []

    return [
        Issue(
            severity=Severity.ERROR,
            field="base_price",
            message=(
                f"단가 {ingredient.base_price}가 범위 "
                f"{economy.ingredient_price_min}~{economy.ingredient_price_max}를 벗어난다"
            ),
        )
    ]


def _check_substance(ingredient: Ingredient, bible: ProjectBible) -> list[Issue]:
    """이름과 설명이 있어야 요리 생성기가 읽을 것이 있다."""
    issues: list[Issue] = []

    if not ingredient.name.strip():
        issues.append(Issue(severity=Severity.ERROR, field="name", message="이름이 비어 있다"))

    minimum = bible.generation.min_text_length
    if len(ingredient.description.strip()) < minimum:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="description",
                message=(
                    f"설명이 너무 짧다({len(ingredient.description.strip())}자). "
                    f"{minimum}자 이상으로 쓴다 — 요리 생성기가 이것만 보고 조합을 짠다"
                ),
            )
        )

    return issues
