"""손님 페르소나 — `guests.json` 한 항목의 정의.

만족도 엔진이 읽는 것(`GuestPersona`)보다 넓다. 엔진은 이상 구간과 식이 제약만 쓰지만,
계약은 대사 생성기와 클라이언트 UI도 함께 먹인다.

**엔진 입력과 계약 항목을 한 모델로 합치지 않는다.** 엔진은 다른 언어로 이식될
레퍼런스 구현이고, 입력이 좁을수록 명세로서 정확하다. 이름과 말투가 입력에 섞여 있으면
이식하는 쪽이 "이것도 계산에 필요한가"를 코드로 알 수 없다. `to_persona()`가
"계약 필드 중 계산에 쓰이는 것은 둘뿐"이라는 사실 자체를 못박는다.
"""

import re

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import Issue, Severity
from daily_special.domain.satisfaction import GuestPersona, IdealRange

_GUEST_ID = re.compile(r"^guest_[a-z0-9_]+$")
"""데이터 계약 2절의 ID 문법. 접두사가 종류를 말한다."""

_MAX_ID_LEN = 64


class Guest(BaseModel):
    """손님 하나. 발행 후 바뀌지 않는 것만 담는다.

    허기·컨디션·기분·지갑은 여기 없다. 플레이 중에 바뀌는 것은 서버가 소유한다
    (데이터 계약 5절). 엔진 쪽에서는 `VisitState`가 그 자리를 맡는다.
    """

    model_config = ConfigDict(frozen=True)

    guest_id: str
    """`guest_` 접두사 슬러그. 발행 후 불변이며 서버 DB와 클라 에셋 파일명이 문다."""

    name: str
    """한국어 표시명."""

    title: str
    """이름 옆에 붙는 짧은 정체성. 예: "잿빛 정찰병"."""

    bio: str
    """**무엇을 하는 사람인가.** 플레이어에게 보이는 텍스트다.

    아래 personality와 나누는 이유는 소비자가 다르기 때문이다. 이쪽은 클라이언트 UI가
    화면에 띄우고, 저쪽은 대사 생성 프롬프트가 읽는다. 한 필드가 둘을 겸하면 UI에는
    너무 길고 프롬프트에는 정보가 없는 글이 나온다.
    """

    voice: str
    """말투 키. ProjectBible의 어휘이자 `lines.json`과의 조인 키다."""

    personality: str
    """**어떻게 말하고 반응하는가.** 대사 생성 프롬프트의 입력이다.

    말투 키가 큰 결을 정하고, 이 문장이 같은 말투 안에서 이 손님을 구별한다.
    """

    preferred_needs: list[str]
    """평소 이 손님이 기우는 욕구들. 가중치는 없다 — 전부 동등하다.

    엔진은 이것을 읽지 않는다. 오늘의 욕구를 뽑는 것은 서버의 일이고, 엔진은 그 결과를
    `VisitState.needs`로 받는다. 그런데도 계약에 있는 이유는 그 규칙의 **입력**이면서
    플레이 중에 바뀌지 않기 때문이다 — "플레이 중에 바뀌면 서버, 안 바뀌면 계약".
    """

    ideal_ranges: dict[str, IdealRange]
    """축 키 → 이상 구간. 취향이 없는 축은 넣지 않는다 (어떤 값이든 만족).

    임의 키 dict라 구조화 출력의 strict 모드가 그대로는 받지 못한다. 5단계에서
    ProjectBible의 축 목록으로 축마다 필드 하나인 스키마를 만들어 넘긴 뒤 여기로 되접는다.
    """

    dietary: list[str] = []
    """식이 제약 키 목록."""

    def to_persona(self) -> GuestPersona:
        """만족도 엔진이 읽는 부분만 뽑는다."""
        return GuestPersona(ideal_ranges=self.ideal_ranges, dietary=self.dietary)


def check_guest(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """계약과 ProjectBible 어휘에 맞는지 본다. 예외를 던지지 않고 전부 모아 돌려준다.

    설정이 틀린 것은 ConfigError로 즉시 멈추지만, 생성물이 틀린 것은 재생성으로 고친다.
    첫 위반에서 멈추면 재생성 피드백에 한 줄밖에 싣지 못한다.

    여기서 보는 것은 **어휘 적합성과 구조적 범위**뿐이다. "이상 구간이 슬라이더 폭의
    절반이라 취향이라 부를 수 없다" 같은 품질 판정은 6단계 검증 층의 몫이다.
    """
    issues: list[Issue] = []

    issues += _check_id(guest)
    issues += _check_voice(guest, bible)
    issues += _check_vocabulary(guest, bible)
    issues += _check_ideal_ranges(guest, bible)

    return issues


def _check_id(guest: Guest) -> list[Issue]:
    """ID는 서버 DB와 에셋 파일명이 무는 값이라 발행 후 고칠 수 없다."""
    if not _GUEST_ID.match(guest.guest_id):
        return [
            Issue(
                severity=Severity.ERROR,
                field="guest_id",
                message=(
                    f"'{guest.guest_id}'는 손님 ID 문법이 아니다. "
                    "'guest_'로 시작하고 소문자·숫자·밑줄만 쓴다 (예: guest_ashen_scout_01)"
                ),
            )
        ]
    if len(guest.guest_id) > _MAX_ID_LEN:
        return [
            Issue(
                severity=Severity.ERROR,
                field="guest_id",
                message=f"손님 ID가 {_MAX_ID_LEN}자를 넘는다: {len(guest.guest_id)}자",
            )
        ]
    return []


def _check_voice(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """말투가 어휘 밖이면 런타임이 대사를 한 줄도 고르지 못한다."""
    if bible.find_voice(guest.voice) is not None:
        return []

    known = ", ".join(voice.key for voice in bible.voices)
    return [
        Issue(
            severity=Severity.ERROR,
            field="voice",
            message=f"'{guest.voice}'는 없는 말투다. 다음 중에서 고른다: {known}",
        )
    ]


def _check_vocabulary(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """욕구와 식이 제약이 ProjectBible 어휘 안에 있는가."""
    issues: list[Issue] = []

    known_needs = ", ".join(need.key for need in bible.needs)
    for need in guest.preferred_needs:
        if bible.find_need(need) is None:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field="preferred_needs",
                    message=f"'{need}'는 없는 욕구다. 다음 중에서 고른다: {known_needs}",
                )
            )

    known_dietary = ", ".join(item.key for item in bible.dietary_constraints)
    for key in guest.dietary:
        if bible.find_dietary(key) is None:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field="dietary",
                    message=f"'{key}'는 없는 식이 제약이다. 다음 중에서 고른다: {known_dietary}",
                )
            )

    return issues


def _check_ideal_ranges(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """이상 구간이 실재하는 축의, 슬라이더가 낼 수 있는 값 안에 있는가.

    슬라이더 밖의 구간은 플레이어가 영원히 맞출 수 없다. 그런데 만족도 계산은
    "구간에서 멀다"로만 읽어 조용히 낮은 점수를 내므로, 여기서 잡지 않으면 드러나지 않는다.
    """
    issues: list[Issue] = []
    known_axes = ", ".join(axis.key for axis in bible.axes)

    for key, ideal in guest.ideal_ranges.items():
        axis = bible.find_axis(key)
        if axis is None:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"ideal_ranges.{key}",
                    message=f"'{key}'는 없는 축이다. 다음 중에서 고른다: {known_axes}",
                )
            )
            continue

        if ideal.low < axis.slider_min or ideal.high > axis.slider_max:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"ideal_ranges.{key}",
                    message=(
                        f"이상 구간 {ideal.low}~{ideal.high}이 축 '{key}'의 슬라이더 범위 "
                        f"{axis.slider_min}~{axis.slider_max}를 벗어난다. "
                        "플레이어가 낼 수 없는 값이라 영원히 맞출 수 없다"
                    ),
                )
            )

    return issues
