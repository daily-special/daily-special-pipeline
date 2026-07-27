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
    """말투 하나. 대사 풀이 (상황 × 말투)로 짜이므로 이 키가 조인 키다.

    말투를 자유 텍스트로 두면 런타임이 대사를 고를 수 없다. 손님의 뉘앙스는
    페르소나의 personality 문장이 따로 나른다 — 조인은 키가, 결은 문장이 맡는다.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    description: str
    """이 말투가 어떻게 말하는지. 대사 생성 프롬프트에 그대로 실린다."""


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
    scoring: ScoringSpec
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

        if not self.needs:
            raise ConfigError("needs가 비어 있다. 욕구가 없으면 손님이 무엇도 원할 수 없다")
        if not self.axes:
            raise ConfigError("axes가 비어 있다. 축이 없으면 요리 품질을 표현할 수 없다")
        if not self.voices:
            raise ConfigError(
                "voices가 비어 있다. 말투가 없으면 손님이 유효한 voice를 가질 수 없다"
            )

        for axis in self.axes:
            if axis.slider_min >= axis.slider_max:
                raise ConfigError(
                    f"축 '{axis.key}'의 슬라이더 범위가 뒤집혔다: "
                    f"{axis.slider_min} >= {axis.slider_max}"
                )

        self._check_scoring()
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
