"""ProjectBible — 게임 수치와 어휘의 소유자.

욕구·파라미터 축·식이 제약은 코드가 아니라 이 설정이 소유한다. 스키마의 소비자가
코드와 프롬프트 둘이라, 변동이 예상되는 축을 코드로 조이면 변경 비용을 두 배로 낸다.

그 대가로 생성물 층의 검증이 느슨해지므로 **설정 스키마는 평소보다 강하게 조인다.**
설정이 틀리면 파이프라인 전 구간이 조용히 틀린 채로 통과하기 때문이다. 그래서 여기의
불변식 위반은 Issue로 모으지 않고 ConfigError로 즉시 멈춘다.
"""

import re
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from daily_special.common.errors import ConfigError

_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
"""식별자 표기. 출력 JSON의 열거 값이 그대로 이 키다."""


class IngredientKind(StrEnum):
    """재료 이분. 메뉴 두 층과 맞물린다."""

    FRESH = "fresh"
    """그날 안 쓰면 상한다. 오늘의 메뉴를 떠받치고, 장보기를 도박으로 만든다."""

    PRESERVED = "preserved"
    """재고가 유지된다. 상시 메뉴의 약속 비용이다."""


class NeedSpec(BaseModel):
    """욕구 하나. 손님의 상태가 낳고, 요리의 태그가 답한다.

    욕구와 요리 태그는 같은 어휘를 쓴다. 갈라두면 만족도 계산에 매핑이 하나 더 생긴다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    """한국어 표시명. 프롬프트에 그대로 실린다."""

    description: str
    """이 욕구가 무엇인지. 모델이 읽고 요리 태그를 붙일 근거가 된다."""


class AxisSpec(BaseModel):
    """요리 파라미터 축 하나. 플레이어가 슬라이더로 조작하는 값의 정의다.

    여기 있는 범위는 **슬라이더 전체 범위**다. 손님이 만족하는 이상 구간은 페르소나가
    따로 갖는다. 한 필드가 두 의미를 겸하면 코드에게는 "검증 상한", 프롬프트에게는
    "이 값을 어디쯤에 넣어라"로 갈려 읽히고, 기술적으로 유효하지만 쓸모없는 값이 나온다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str

    slider_min: int
    slider_max: int


class DietarySpec(BaseModel):
    """식이 제약 하나. 위반하면 만족도에서 페널티를 받는다."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str


class VoiceSpec(BaseModel):
    """말투 하나. 대사 풀이 (상황 × 대상 × 말투)로 짜이므로 이 키가 조인 키다.

    말투를 자유 텍스트로 두면 런타임이 대사를 고를 수 없다. 손님의 뉘앙스는
    페르소나의 personality 문장이 따로 나른다 — 조인은 키가, 결은 문장이 맡는다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str
    """이 말투가 어떻게 말하는지. 대사 생성 프롬프트에 그대로 실린다."""


class SubjectKind(StrEnum):
    """대사의 대상이 어느 어휘에서 오는가."""

    NONE = "none"
    """대상이 없다. 인사·퇴장처럼 무엇을 가리키지 않는 대사다."""

    NEED = "need"
    """욕구. "속이 쓰려요"가 어느 욕구를 말하는지."""

    AXIS = "axis"
    """파라미터 축. "좀 짠데요"가 어느 축을 말하는지."""


class SituationSpec(BaseModel):
    """대사가 나오는 상황 하나.

    대사 풀은 **상황 × 대상 × 말투**로 짜인다. 상황과 말투만으로는 "속이 쓰려요"와
    "좀 짠데요"를 만들 수 없다 — 어느 욕구인지, 어느 축인지가 대사를 정한다.
    정보 공개 곡선이 그 두 대사 위에 얹혀 있다 (design.md 5장).

    상황 어휘를 `주문_회복`처럼 미리 펼치지 않는다. 그러면 욕구 어휘가 이 목록에
    복제되어, 욕구를 하나 늘릴 때 상황도 함께 늘려야 한다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str
    """언제 나오는 대사인지. 생성 프롬프트에 그대로 실린다."""

    subject: SubjectKind
    """이 상황의 대사가 무엇을 가리키는가. 대상 값은 여기 적힌 어휘에서 온다."""


class ScoringSpec(BaseModel):
    """만족도 계산의 계수. 코드에 박지 않고 여기서 소유한다.

    전부 밸런스 시뮬레이션으로 조정할 값이라, 하나라도 코드에 박히면 조정 때마다
    코드를 고치게 된다.
    """

    model_config = ConfigDict(frozen=True)

    need_floor: float
    """욕구 충족도의 바닥값.

    욕구가 1개인 손님은 비율이 0/1 이분법이 되어, 빗나가면 아무리 잘 만들어도
    만족이 0이 된다. 하드 게임오버 없는 코지 게임과 어긋나므로 바닥을 둔다 —
    "원하던 건 아니지만 밥은 먹었다".
    """

    axis_tolerance: int
    """이상 구간 밖으로 이만큼 벗어나면 그 축의 점수가 0이 된다.

    슬라이더 단위와 같은 척도다. 구간 안은 1.0, 밖은 여기까지 선형으로 감소한다.
    """

    budget_overrun_ratio: float
    """지갑의 몇 배 가격에서 예산 적합이 0이 되는가.

    1.0이면 지갑을 넘는 순간 0이라 이분법이 된다. 그보다 커야 완만해진다.
    """

    dietary_violation_factor: float
    """식이 제약 위반 하나당 곱하는 계수. 위반이 둘이면 두 번 곱한다."""


class GenerationSpec(BaseModel):
    """생성물의 합격선. ScoringSpec과 섞지 않는다.

    저쪽은 **만들어진 뒤** 만족도를 계산하는 계수고, 이쪽은 **만들어질 때** 이것을
    손님이라 부를 수 있는지 가르는 선이다. 한 블록에 두면 밸런스를 만지다가
    합격선이 따라 움직인다.

    여기 값들은 소비자가 둘이다 — `check_guest`가 판정에 쓰고, 프롬프트가 모델에게
    미리 알리는 데 쓴다. 그래서 코드가 아니라 설정이 소유한다 (규약 3절).
    """

    model_config = ConfigDict(frozen=True)

    max_ideal_span_ratio: float
    """이상 구간의 폭이 슬라이더 전체의 몇 배까지 허용되는가.

    **프롬프트가 권하는 폭과 다른 것이다.** 프롬프트는 axis_tolerance를 기준으로
    "이 정도로 좁게 잡아라"라고 권하고, 이 값은 "이보다 넓으면 취향이 아니다"라는
    실패선이다. 권고와 실패선이 같으면 조금 넘친 것까지 돈을 들여 다시 만들게 된다.
    """

    min_preferred_axes: int
    """취향이 있는 축이 최소 몇 개여야 하는가.

    하나도 없으면 플레이어가 추측할 것이 없다. 취향 추론이 이 게임의 핵심 루프라
    그런 손님은 손님 구실을 못 한다.
    """

    min_preferred_needs: int
    """평소 욕구가 최소 몇 개. 0개면 서버가 오늘의 욕구를 뽑을 근거가 없다."""

    max_preferred_needs: int
    """평소 욕구가 이보다 많으면 아무 요리에나 만족해 밋밋해진다. 넘어도 쓸 수는 있다."""

    min_text_length: int
    """bio·personality의 최소 길이.

    너무 짧으면 화면에 띄울 것도, 대사를 만들 재료도 없다.
    """

    max_dish_need_tags: int
    """요리 하나가 답할 수 있는 욕구의 최대 개수.

    욕구를 전부 붙이면 누구에게나 만점이라 요리를 고르는 행위가 사라진다.
    이 게임의 절반이 "무엇을 낼까"인데 그것이 없어진다.
    """

    min_dish_ingredients: int
    """재료가 이보다 적으면 조합이라고 할 수 없다."""

    max_dish_ingredients: int
    """재료가 이보다 많으면 오늘 산 것으로 만들 수 없다.

    재료 제약이 정답 암기를 막는 장치인데, 요구 재료가 많을수록 그날 만들 수 있는
    요리가 사라져 차선 찾기가 아니라 막다른 길이 된다.
    """

    max_line_length: int
    """대사 한 줄의 최대 길이.

    손님 한마디가 화면을 덮으면 게임이 멈춘 것처럼 보인다. 코지 게임의 호흡은
    짧은 한마디에서 나온다.
    """


class EconomySpec(BaseModel):
    """화폐 스케일. 세 범위의 **관계**가 이 블록의 내용이다.

    만족도의 예산 항은 가격/지갑 **비율만** 쓰므로 절대 스케일은 계산에 무관하다.
    그래서 여기서 정하는 기준은 게임 수학이 아니라 **플레이어가 읽기 쉬운가**다.
    작은 정수로 두는 이유가 그것이다.

    셋을 따로 정하면 요리 값이 재료 원가와 무관하게 정해질 수 있어 한 블록에 둔다.
    """

    model_config = ConfigDict(frozen=True)

    wallet_min: int
    wallet_max: int
    """손님이 하루에 낼 수 있는 돈의 범위.

    지갑 자체는 매 방문 서버가 만든다(계약 5절). 여기 있는 것은 요리 값을 어디에
    맞출지 정하는 **기준선**이다.
    """

    dish_price_min: int
    dish_price_max: int
    """요리 고정가의 범위. 대부분이 지갑 중앙값 근처에 오도록 잡는다."""

    ingredient_price_min: int
    ingredient_price_max: int
    """재료 단가의 범위. 요리 하나에 재료 여럿이 들어가므로 요리가보다 훨씬 낮아야 한다."""


class CharRange(BaseModel):
    """유니코드 코드포인트 구간. 양 끝을 포함한다."""

    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    label: str
    """무엇을 위해 넣었는지. 나중에 지워도 되는지 판단하려면 이유가 남아 있어야 한다."""


class TextCharsetSpec(BaseModel):
    """화면에 뜨는 텍스트가 쓸 수 있는 문자 집합.

    **클라이언트 폰트 아틀라스와 같은 값이어야 한다.** 여기 없는 문자는 폰에서 네모로 뜬다.
    실제로 한 번 겪었다 — 생성된 대사에 말줄임표(U+2026)가 11번 들어갔는데 아틀라스가
    `20-7E,AC00-D7A3`이라 지친 말투가 통째로 깨질 뻔했다. 그것도 **폰에 올려야만** 보이는
    버그였다.

    그래서 이 설정이 유니티 범위 문자열까지 만들어 낸다(`unity_range`). 파이프라인과 클라가
    같은 값을 각자 적으면 반드시 어긋나고, 어긋난 것을 알아채는 방법이 폰뿐이 된다.
    """

    model_config = ConfigDict(frozen=True)

    ranges: list[CharRange]

    def contains(self, char: str) -> bool:
        return any(item.start <= ord(char) <= item.end for item in self.ranges)

    def violations(self, text: str) -> list[str]:
        """집합 밖 문자를 처음 나온 순서대로, 중복 없이 돌려준다."""
        found: list[str] = []
        for char in text:
            if not self.contains(char) and char not in found:
                found.append(char)
        return found

    def unity_range(self) -> str:
        """유니티 Font Asset Creator의 `Unicode Range (Hex)`에 그대로 넣는 문자열.

        클라이언트는 이 값을 **계산하지 않고 받아 적는다.**
        """
        parts = [
            f"{item.start:X}" if item.start == item.end else f"{item.start:X}-{item.end:X}"
            for item in self.ranges
        ]
        return ",".join(parts)

    @model_validator(mode="after")
    def _check_ranges(self) -> "TextCharsetSpec":
        if not self.ranges:
            raise ConfigError("text_charset.ranges가 비어 있다. 그러면 어떤 글자도 쓸 수 없다")

        previous_end = -1
        for item in self.ranges:
            if item.start > item.end:
                raise ConfigError(
                    f"문자 구간 '{item.label}'이 뒤집혔다: U+{item.start:04X} > U+{item.end:04X}"
                )
            if item.start < 0 or item.end > 0x10FFFF:
                raise ConfigError(f"문자 구간 '{item.label}'이 유니코드 범위를 벗어났다")
            if item.start <= previous_end:
                # 정렬과 비겹침을 요구하는 이유는 unity_range()의 출력을 결정적으로
                # 만들기 위해서다. 클라가 받아 적는 값이 실행마다 달라지면 안 된다.
                raise ConfigError(
                    f"문자 구간 '{item.label}'이 앞 구간과 겹치거나 정렬되어 있지 않다: "
                    f"U+{item.start:04X}"
                )
            previous_end = item.end

        return self


DEFAULT_TEXT_CHARSET = TextCharsetSpec(
    ranges=[
        CharRange(start=0x20, end=0x7E, label="ASCII 인쇄 가능 — 영문·숫자·기본 문장부호"),
        CharRange(start=0xA1, end=0xFF, label="라틴-1 보충 — 가운뎃점(·)·도(°)·곱셈표(×)"),
        CharRange(start=0x2013, end=0x2014, label="줄표 (en dash, em dash)"),
        CharRange(start=0x2018, end=0x201D, label="홑·겹따옴표"),
        CharRange(start=0x2026, end=0x2026, label="말줄임표 … — 지친 말투가 실제로 쓴다"),
        CharRange(start=0xAC00, end=0xD7A3, label="한글 완성형 11,172자"),
    ]
)
"""기본 문자 집합.

다른 설정값과 달리 **기본값을 준다.** 이것은 밸런스 손잡이가 아니라 클라이언트 폰트
아틀라스에 묶인 상수라, 설정마다 다시 정할 성질의 것이 아니다. `data/project_bible.json`은
그럼에도 명시적으로 선언한다 — 설정 파일만 읽고도 무엇이 허용되는지 알아야 한다.
"""


class Unconfirmed(BaseModel):
    """확정되지 않은 게임 수치. 가정값을 쓴다면 그 사실이 여기 남아야 한다.

    가정값을 코드나 프롬프트에 직접 박으면 확정 시점에 찾을 수 없다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    assumed: str
    """지금 쓰고 있는 가정값."""

    why: str
    """무엇이 왜 미확정인지, 무엇으로 확정할 것인지."""


class ProjectBible(BaseModel):
    """게임 수치와 어휘 전체."""

    model_config = ConfigDict(frozen=True)

    version: str
    """출력 패키지의 bible_version에 실린다. 밸런스 추적의 기준점이다."""

    needs: list[NeedSpec]
    axes: list[AxisSpec]
    dietary_constraints: list[DietarySpec]
    voices: list[VoiceSpec]
    situations: list[SituationSpec]
    scoring: ScoringSpec
    generation: GenerationSpec
    economy: EconomySpec
    text_charset: TextCharsetSpec = DEFAULT_TEXT_CHARSET
    unconfirmed: list[Unconfirmed] = []

    def find_need(self, key: str) -> NeedSpec | None:
        """없으면 None. 생성물이 어휘 밖의 값을 낸 것은 설정 오류가 아니라 Issue다."""
        return next((need for need in self.needs if need.key == key), None)

    def find_axis(self, key: str) -> AxisSpec | None:
        return next((axis for axis in self.axes if axis.key == key), None)

    def find_dietary(self, key: str) -> DietarySpec | None:
        return next((item for item in self.dietary_constraints if item.key == key), None)

    def find_voice(self, key: str) -> VoiceSpec | None:
        return next((voice for voice in self.voices if voice.key == key), None)

    def find_situation(self, key: str) -> SituationSpec | None:
        return next((item for item in self.situations if item.key == key), None)

    @model_validator(mode="after")
    def _check_invariants(self) -> "ProjectBible":
        if not self.version.strip():
            raise ConfigError("version이 비어 있다")

        _require_unique_slugs("needs", [need.key for need in self.needs])
        _require_unique_slugs("axes", [axis.key for axis in self.axes])
        _require_unique_slugs(
            "dietary_constraints", [item.key for item in self.dietary_constraints]
        )
        _require_unique_slugs("voices", [voice.key for voice in self.voices])
        _require_unique_slugs("situations", [item.key for item in self.situations])

        if not self.needs:
            raise ConfigError("needs가 비어 있다. 욕구가 없으면 손님이 무엇도 원할 수 없다")
        if not self.axes:
            raise ConfigError("axes가 비어 있다. 축이 없으면 요리 품질을 표현할 수 없다")
        if not self.voices:
            raise ConfigError(
                "voices가 비어 있다. 말투가 없으면 손님이 유효한 voice를 가질 수 없다"
            )
        if not self.situations:
            raise ConfigError("situations가 비어 있다. 상황이 없으면 대사가 나올 자리가 없다")

        for axis in self.axes:
            if axis.slider_min >= axis.slider_max:
                raise ConfigError(
                    f"축 '{axis.key}'의 슬라이더 범위가 뒤집혔다: "
                    f"{axis.slider_min} >= {axis.slider_max}"
                )

        self._check_scoring()
        self._check_generation()
        self._check_economy()
        return self

    def _check_scoring(self) -> None:
        """계수가 범위를 벗어나면 만족도가 0~1을 벗어난다. 여기서 막는다."""
        scoring = self.scoring

        if not 0.0 <= scoring.need_floor < 1.0:
            raise ConfigError(
                f"need_floor는 0 이상 1 미만이어야 한다: {scoring.need_floor}. "
                "1이면 욕구를 빗나가도 만점이라 추론할 이유가 사라진다"
            )
        if not 0.0 <= scoring.dietary_violation_factor < 1.0:
            raise ConfigError(
                f"dietary_violation_factor는 0 이상 1 미만이어야 한다: "
                f"{scoring.dietary_violation_factor}. 1이면 위반이 아무 대가가 없다"
            )
        if scoring.budget_overrun_ratio <= 1.0:
            raise ConfigError(
                f"budget_overrun_ratio는 1보다 커야 한다: {scoring.budget_overrun_ratio}. "
                "1 이하면 지갑을 넘는 순간 0이 되어 이분법이 된다"
            )
        if scoring.axis_tolerance <= 0:
            raise ConfigError(
                f"axis_tolerance는 0보다 커야 한다: {scoring.axis_tolerance}. "
                "0이면 이상 구간을 1만큼만 벗어나도 그 축이 0이 된다"
            )

    def _check_generation(self) -> None:
        """합격선이 어휘보다 크면 어떤 생성물도 통과할 수 없다. 여기서 막는다."""
        generation = self.generation

        if not 0.0 < generation.max_ideal_span_ratio <= 1.0:
            raise ConfigError(
                f"max_ideal_span_ratio는 0 초과 1 이하여야 한다: "
                f"{generation.max_ideal_span_ratio}. 1을 넘으면 슬라이더보다 넓은 구간을 "
                "허용하는 뜻이 되는데, 그것은 이미 다른 검사가 막는다"
            )
        if not 1 <= generation.min_preferred_axes <= len(self.axes):
            raise ConfigError(
                f"min_preferred_axes는 1 이상 축 개수({len(self.axes)}) 이하여야 한다: "
                f"{generation.min_preferred_axes}. 0이면 취향 없는 손님이 통과하고, "
                "축 개수를 넘으면 어떤 손님도 통과할 수 없다"
            )
        if generation.min_preferred_needs < 1:
            raise ConfigError(
                f"min_preferred_needs는 1 이상이어야 한다: {generation.min_preferred_needs}. "
                "0이면 서버가 오늘의 욕구를 뽑을 근거가 없는 손님이 통과한다"
            )
        if generation.max_preferred_needs < generation.min_preferred_needs:
            raise ConfigError(
                f"max_preferred_needs가 min보다 작다: "
                f"{generation.max_preferred_needs} < {generation.min_preferred_needs}"
            )
        if generation.max_preferred_needs > len(self.needs):
            raise ConfigError(
                f"max_preferred_needs가 욕구 개수({len(self.needs)})를 넘는다: "
                f"{generation.max_preferred_needs}"
            )
        if generation.min_text_length < 1:
            raise ConfigError(f"min_text_length는 1 이상이어야 한다: {generation.min_text_length}")
        if not 1 <= generation.max_dish_need_tags <= len(self.needs):
            raise ConfigError(
                f"max_dish_need_tags는 1 이상 욕구 개수({len(self.needs)}) 이하여야 한다: "
                f"{generation.max_dish_need_tags}. 0이면 어떤 요리도 통과할 수 없고, "
                "욕구를 전부 허용하면 누구에게나 만점인 요리가 나온다"
            )
        if generation.min_dish_ingredients < 1:
            raise ConfigError(
                f"min_dish_ingredients는 1 이상이어야 한다: "
                f"{generation.min_dish_ingredients}. 재료 없는 요리는 만들 수 없다"
            )
        if generation.max_line_length < 1:
            raise ConfigError(f"max_line_length는 1 이상이어야 한다: {generation.max_line_length}")
        if generation.max_dish_ingredients < generation.min_dish_ingredients:
            raise ConfigError(
                f"max_dish_ingredients가 min보다 작다: "
                f"{generation.max_dish_ingredients} < {generation.min_dish_ingredients}"
            )

    def _check_economy(self) -> None:
        """세 범위의 관계를 지킨다. 따로 정해지면 요리가 재료 원가와 무관해진다."""
        economy = self.economy

        for name, low, high in (
            ("wallet", economy.wallet_min, economy.wallet_max),
            ("dish_price", economy.dish_price_min, economy.dish_price_max),
            ("ingredient_price", economy.ingredient_price_min, economy.ingredient_price_max),
        ):
            if low < 1:
                raise ConfigError(f"{name}_min은 1 이상이어야 한다: {low}")
            if low > high:
                raise ConfigError(f"{name} 범위가 뒤집혔다: {low} > {high}")

        # 재료 상한이 요리 **하한**보다 높은 것은 막지 않는다. 싼 요리가 싼 재료를 쓰고
        # 비싼 재료가 비싼 요리로 가는 것은 정상이며, 조합별 마진은 범위로 판정할 수 없다.
        if economy.ingredient_price_max >= economy.dish_price_max:
            raise ConfigError(
                f"가장 비싼 재료가 가장 비싼 요리만큼 한다: "
                f"{economy.ingredient_price_max} >= {economy.dish_price_max}. "
                "요리 하나에 재료가 여럿 들어가므로 어떤 조합도 마진이 나지 않는다"
            )
        if economy.dish_price_min > economy.wallet_max:
            raise ConfigError(
                f"가장 싼 요리가 가장 두둑한 지갑보다 비싸다: "
                f"{economy.dish_price_min} > {economy.wallet_max}. "
                "아무도 아무것도 살 수 없다"
            )


def _require_unique_slugs(field: str, keys: Sequence[str]) -> None:
    """키는 슬러그 표기여야 하고 중복될 수 없다.

    중복을 허용하면 뒤에 온 것이 조용히 이기고, 어느 쪽이 쓰였는지 추적할 수 없다.
    """
    seen: set[str] = set()
    for key in keys:
        if not _SLUG.match(key):
            raise ConfigError(
                f"{field}의 키 '{key}'가 슬러그 표기가 아니다 (소문자·숫자·밑줄, 소문자로 시작)"
            )
        if key in seen:
            raise ConfigError(f"{field}에 중복된 키가 있다: '{key}'")
        seen.add(key)
