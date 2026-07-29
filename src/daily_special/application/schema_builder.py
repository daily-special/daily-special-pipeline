"""ProjectBible에서 구조화 출력 스키마를 만든다.

**이 모듈이 존재하는 이유는 strict 모드가 임의 키 dict를 금지하기 때문이다.**

계약의 `ideal_ranges`는 `{축 키: {low, high}}` 맵인데, strict 모드는 키를 미리 알 수 없는
객체를 받지 않는다. 그래서 축 하나가 필드 하나인 모델(`ideal_heat`, `ideal_cook_time`, ...)을
런타임에 만들어 넘기고, 받은 뒤 다시 맵으로 접는다.

어휘도 같은 이유로 여기서 enum이 된다. 모델이 없는 말투나 없는 욕구를 뱉는 것을
**구조적으로** 막는 것이 검증으로 잡아 재생성하는 것보다 항상 싸다 (규약 5-1의 1층).

그래서 축을 하나 더하면 스키마와 프롬프트가 함께 따라온다. 어휘를 코드에 박았다면
같은 변경을 두 번 냈을 것이다.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from daily_special.common.errors import DomainError
from daily_special.domain.bible import IngredientKind, ProjectBible
from daily_special.domain.guest import Guest
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.satisfaction import IdealRange

_AXIS_FIELD_PREFIX = "ideal_"

_CACHE: dict[tuple[str, str], "BatchSchema"] = {}
"""(스키마 종류, 설정 내용) → 스키마.

같은 설정이면 **같은 클래스 객체**를 돌려줘야 한다. 부를 때마다 새로 지으면 이름만 같고
서로 다른 클래스가 쌓여, 재생성 루프가 매번 Pydantic 모델을 다시 짓고 응답의 타입 비교가
어긋난다.

키가 두 쪽인 이유는 다르다. 설정 내용을 넣는 것은 버전 문자열이 같은데 어휘가 다른 설정이
있을 수 있어서고 — 그런 것을 같다고 보면 조용히 틀린 스키마가 나간다 — 종류를 넣는 것은
같은 설정에서 손님 스키마와 재료 스키마가 함께 나오기 때문이다.
"""


class GeneratedRange(BaseModel):
    """모델이 뱉는 이상 구간. 계약의 IdealRange와 같은 모양이다.

    strict 모드가 수치 제약(minimum/maximum)을 거부할 수 있어 여기엔 제약을 걸지 않는다.
    범위는 필드 설명으로 알리고 실제 강제는 check_guest가 한다 (규약 4-4).
    """

    model_config = ConfigDict(extra="forbid")

    low: int
    high: int


class BatchSchema:
    """동적으로 만든 배치 스키마와, 그것을 계약으로 되접는 방법.

    둘을 한 곳에 묶는 이유는 되접기가 **어떻게 폈는지**를 알아야 하기 때문이다.
    떨어뜨려 두면 편 방식이 두 곳에서 따로 흐른다.
    """

    def __init__(self, model: type[BaseModel]) -> None:
        self.model = model
        """LlmPort.generate의 schema 인자로 그대로 넘기는 모델."""


class GuestBatchSchema(BatchSchema):
    """손님 배치. 축마다 필드 하나로 편 것을 다시 맵으로 접는다."""

    def __init__(self, model: type[BaseModel], axis_keys: Sequence[str]) -> None:
        super().__init__(model)
        self._axis_keys = tuple(axis_keys)

    def to_guests(self, response: BaseModel) -> list[Guest]:
        """응답을 계약 항목으로 되접는다.

        축 필드가 None이면 그 축은 결과 맵에서 **빠진다**. 계약에서 키가 없는 것이
        "취향 없음"이기 때문이다 (data-contract 7-2절). strict 모드는 모든 필드가
        있기를 요구하고 계약은 없기를 요구하는데, null이 그 사이를 잇는다.
        """
        items = cast(list[BaseModel], _field(response, "guests"))
        return [self._to_guest(item) for item in items]

    def _to_guest(self, item: BaseModel) -> Guest:
        needs = cast(list[Any], _field(item, "preferred_needs"))
        dietary = cast(list[Any], _field(item, "dietary"))

        ideal_ranges: dict[str, IdealRange] = {}
        for key in self._axis_keys:
            raw = cast(GeneratedRange | None, _field(item, _AXIS_FIELD_PREFIX + key))
            if raw is not None:
                ideal_ranges[key] = IdealRange(low=raw.low, high=raw.high)

        return Guest(
            guest_id=cast(str, _field(item, "guest_id")),
            name=cast(str, _field(item, "name")),
            title=cast(str, _field(item, "title")),
            bio=cast(str, _field(item, "bio")),
            personality=cast(str, _field(item, "personality")),
            voice=str(_field(item, "voice")),
            preferred_needs=[str(need) for need in needs],
            ideal_ranges=ideal_ranges,
            dietary=[str(key) for key in dietary],
        )


def build_guest_batch_schema(bible: ProjectBible) -> GuestBatchSchema:
    """설정의 어휘와 축으로 배치 스키마를 짓는다. 같은 설정이면 같은 것을 돌려준다."""
    if not bible.axes:
        raise DomainError("축이 없는 설정으로는 스키마를 만들 수 없다")

    cache_key = ("guests", bible.model_dump_json())
    cached = _CACHE.get(cache_key)
    if isinstance(cached, GuestBatchSchema):
        return cached

    need_enum = _string_enum("NeedKey", [need.key for need in bible.needs])
    voice_enum = _string_enum("VoiceKey", [voice.key for voice in bible.voices])
    dietary_enum = _string_enum("DietaryKey", [item.key for item in bible.dietary_constraints])
    fields: dict[str, Any] = {
        "guest_id": (
            str,
            Field(
                description=(
                    "손님 식별자. 'guest_'로 시작하고 소문자·숫자·밑줄만 쓴다. "
                    "정체성이 드러나게 짓고 끝에 두 자리 일련번호를 붙인다 "
                    "(예: guest_ashen_scout_01). 64자 이내."
                )
            ),
        ),
        "name": (str, Field(description="한국어 이름. 판타지 세계관에 어울리는 짧은 고유명.")),
        "title": (
            str,
            Field(description="이름 옆에 붙는 짧은 정체성. 예: '잿빛 정찰병'. 10자 이내."),
        ),
        "bio": (
            str,
            Field(
                description=(
                    "이 사람이 무엇을 하는 사람인지 한두 문장. 플레이어에게 그대로 보이는 "
                    "글이다. 왜 이 식당에 오는지가 드러나면 좋다. "
                    f"{bible.generation.min_text_length}자 이상."
                )
            ),
        ),
        "personality": (
            str,
            Field(
                description=(
                    "어떻게 말하고 반응하는지 한두 문장. 화면에 보이지 않고 대사를 만들 때만 "
                    "쓴다. 말투가 큰 결을 정하므로 여기엔 이 사람만의 버릇을 쓴다. "
                    f"{bible.generation.min_text_length}자 이상."
                )
            ),
        ),
        "voice": (voice_enum, Field(description=_voice_description(bible))),
        "preferred_needs": (
            list[need_enum],  # type: ignore[valid-type]
            Field(description=_need_description(bible)),
        ),
        "dietary": (
            list[dietary_enum],  # type: ignore[valid-type]
            Field(description=_dietary_description(bible)),
        ),
    }

    for axis in bible.axes:
        fields[_AXIS_FIELD_PREFIX + axis.key] = (
            GeneratedRange | None,
            Field(description=_axis_description(bible, axis.key)),
        )

    guest_model = create_model(
        "GeneratedGuest",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )
    batch_model = create_model(
        "GeneratedGuestBatch",
        __config__=ConfigDict(extra="forbid"),
        guests=(
            list[guest_model],  # type: ignore[valid-type]
            Field(description="요청받은 수만큼의 손님. 서로 겹치지 않게 만든다."),
        ),
    )

    schema = GuestBatchSchema(batch_model, [axis.key for axis in bible.axes])
    _CACHE[cache_key] = schema
    return schema


class IngredientBatchSchema(BatchSchema):
    """재료 배치. 손님과 달리 펼 것이 없어 되접기가 단순하다.

    임의 키 dict가 없기 때문이다 — 그런데도 스키마를 동적으로 짓는 이유는 식이 제약
    어휘가 enum이어야 하기 때문이다. 없는 제약을 저촉한다고 말하는 것을 구조적으로 막는다.
    """

    def to_ingredients(self, response: BaseModel) -> list[Ingredient]:
        items = cast(list[BaseModel], _field(response, "ingredients"))
        return [self._to_ingredient(item) for item in items]

    def _to_ingredient(self, item: BaseModel) -> Ingredient:
        conflicts = cast(list[Any], _field(item, "dietary_conflicts"))
        return Ingredient(
            ingredient_id=cast(str, _field(item, "ingredient_id")),
            name=cast(str, _field(item, "name")),
            kind=IngredientKind(str(_field(item, "kind"))),
            description=cast(str, _field(item, "description")),
            base_price=cast(int, _field(item, "base_price")),
            dietary_conflicts=[str(key) for key in conflicts],
        )


def build_ingredient_batch_schema(bible: ProjectBible) -> IngredientBatchSchema:
    """설정의 식이 어휘와 가격 범위로 재료 스키마를 짓는다."""
    cache_key = ("ingredients", bible.model_dump_json())
    cached = _CACHE.get(cache_key)
    if isinstance(cached, IngredientBatchSchema):
        return cached

    dietary_enum = _string_enum("DietaryKey", [item.key for item in bible.dietary_constraints])
    economy = bible.economy

    item_model = create_model(
        "GeneratedIngredient",
        __config__=ConfigDict(extra="forbid"),
        ingredient_id=(
            str,
            Field(
                description=(
                    "재료 식별자. 'ingredient_'로 시작하고 소문자·숫자·밑줄만 쓴다. "
                    "무엇인지 드러나게 짓는다 (예: ingredient_river_herb). 64자 이내."
                )
            ),
        ),
        name=(str, Field(description="한국어 이름. 판타지 세계관의 식재료다운 짧은 이름.")),
        kind=(
            IngredientKind,
            Field(
                description=(
                    "fresh는 그날 안 쓰면 상하는 재료 — 채소·생고기·생선처럼 오늘의 메뉴를 "
                    "떠받친다. preserved는 재고가 유지되는 재료 — 말린 것·절인 것·곡물처럼 "
                    "상시 메뉴의 바탕이 된다. 두 종류가 골고루 나와야 한다."
                )
            ),
        ),
        description=(
            str,
            Field(
                description=(
                    "어떤 재료이고 어떤 맛인지 한두 문장. 요리를 만드는 쪽이 이것만 보고 "
                    f"조합을 짜므로 맛과 쓰임이 드러나야 한다. "
                    f"{bible.generation.min_text_length}자 이상."
                )
            ),
        ),
        base_price=(
            int,
            Field(
                description=(
                    f"기준 단가. {economy.ingredient_price_min}~"
                    f"{economy.ingredient_price_max} 사이의 정수다. 흔한 재료는 낮게, "
                    "귀하거나 손이 많이 가는 재료는 높게 매긴다."
                )
            ),
        ),
        dietary_conflicts=(
            list[dietary_enum],  # type: ignore[valid-type]
            Field(description=_ingredient_dietary_description(bible)),
        ),
    )
    batch_model = create_model(
        "GeneratedIngredientBatch",
        __config__=ConfigDict(extra="forbid"),
        ingredients=(
            list[item_model],  # type: ignore[valid-type]
            Field(description="요청받은 수만큼의 재료. 서로 겹치지 않게 만든다."),
        ),
    )

    schema = IngredientBatchSchema(batch_model)
    _CACHE[cache_key] = schema
    return schema


def _ingredient_dietary_description(bible: ProjectBible) -> str:
    if not bible.dietary_constraints:
        return "이 재료가 저촉하는 식이 제약. 지금 설정에는 제약이 없으므로 빈 배열로 둔다."

    listed = " / ".join(
        f"{item.key}({item.label}): {item.description}" for item in bible.dietary_constraints
    )
    return (
        "이 재료를 쓴 요리가 저촉하게 되는 식이 제약. 재료 자체의 성질로만 판단한다 — "
        "고기면 육류 불가, 술이면 주류 불가에 걸린다. 해당 없으면 빈 배열로 둔다. "
        f"목록: {listed}"
    )


def _field(item: BaseModel, name: str) -> Any:
    """동적으로 만든 모델의 필드를 읽는다.

    모델이 런타임에 지어지므로 타입 검사기가 필드를 알 방법이 없다. getattr을 직접 쓰면
    "상수 문자열이니 속성으로 쓰라"는 린트에 걸리는데, 여기선 그럴 수 없다.
    """
    return getattr(item, name)


def _string_enum(name: str, keys: Sequence[str]) -> type[StrEnum]:
    """어휘를 enum으로. 어휘 밖의 값을 구조적으로 낼 수 없게 만든다.

    함수형 생성이라 타입 검사기가 결과를 클래스로 보지 못한다. 캐스트가 필요한 이유가
    그것이고, 어휘가 런타임에 정해지는 한 피할 수 없다.
    """
    return cast(type[StrEnum], StrEnum(name, {key.upper(): key for key in keys}))


def _voice_description(bible: ProjectBible) -> str:
    listed = " / ".join(
        f"{voice.key}({voice.label}): {voice.description}" for voice in bible.voices
    )
    return f"이 손님의 말투. 다음 중 하나를 고른다 — {listed}"


def _need_description(bible: ProjectBible) -> str:
    listed = " / ".join(f"{need.key}({need.label}): {need.description}" for need in bible.needs)
    spec = bible.generation
    return (
        f"이 손님이 평소 기우는 욕구. {spec.min_preferred_needs}~{spec.max_preferred_needs}개만 "
        f"고른다. 성격·처지와 이어져야 한다 — 목록: {listed}"
    )


def _dietary_description(bible: ProjectBible) -> str:
    if not bible.dietary_constraints:
        return "식이 제약. 지금 설정에는 제약이 없으므로 빈 배열로 둔다."

    listed = " / ".join(
        f"{item.key}({item.label}): {item.description}" for item in bible.dietary_constraints
    )
    return (
        "이 손님이 먹지 않는 것. 대부분의 손님은 제약이 없으니 빈 배열로 두고, "
        f"사연으로 설명되는 경우에만 넣는다 — 목록: {listed}"
    )


def _axis_description(bible: ProjectBible, axis_key: str) -> str:
    axis = bible.find_axis(axis_key)
    if axis is None:  # pragma: no cover - 축 목록에서 왔으므로 도달하지 않는다
        raise DomainError(f"축 '{axis_key}'가 설정에 없다")

    return (
        f"{axis.label} 축에서 이 손님이 만족하는 구간. {axis.description}. "
        f"슬라이더는 {axis.slider_min}~{axis.slider_max}이고 구간은 그 안에 들어와야 한다. "
        f"폭(high - low)은 {bible.scoring.axis_tolerance} 이하로 좁게 잡는다 — "
        "넓으면 취향이라고 할 수 없다. "
        "이 축에 취향이 없어 어떤 값이든 괜찮다면 null로 둔다. "
        "구간은 bio·personality와 앞뒤가 맞아야 한다."
    )
