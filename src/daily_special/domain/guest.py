"""손님 페르소나 — `guests.json` 한 항목의 정의.

만족도 엔진이 읽는 것(`GuestPersona`)보다 넓다. 엔진은 이상 구간과 식이 제약만 쓰지만,
계약은 대사 생성기와 클라이언트 UI도 함께 먹인다.

**엔진 입력과 계약 항목을 한 모델로 합치지 않는다.** 엔진은 다른 언어로 이식될
레퍼런스 구현이고, 입력이 좁을수록 명세로서 정확하다. 이름과 말투가 입력에 섞여 있으면
이식하는 쪽이 "이것도 계산에 필요한가"를 코드로 알 수 없다. `to_persona()`가
"계약 필드 중 계산에 쓰이는 것은 둘뿐"이라는 사실 자체를 못박는다.
"""

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import ProjectBible
from daily_special.domain.charset import check_charset
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

    보는 것은 두 갈래다.

    - **어휘 적합성과 구조적 범위** — 없는 말투를 쓰지 않았는가, 슬라이더 밖을 가리키지
      않는가. 이쪽은 틀리면 게임이 잘못 돈다
    - **합격선** — 이것을 손님이라 부를 수 있는가. 문법적으로 완벽하면서 쓸모없는
      생성물이 있다. 취향이 하나도 없는 손님, 슬라이더 전체를 이상 구간으로 가진 손님

    ERROR와 WARNING을 가르는 기준은 **그대로 쓰면 게임이 잘못 도는가**다. ERROR만
    재생성을 부르고, 재생성은 돈을 쓴다 (규약 5-4).
    """
    issues: list[Issue] = []

    issues += _check_id(guest)
    issues += _check_voice(guest, bible)
    issues += _check_vocabulary(guest, bible)
    issues += _check_ideal_ranges(guest, bible)
    issues += _check_substance(guest, bible)
    issues += _check_charset(guest, bible)

    return issues


def _check_charset(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """화면에 뜨는 텍스트가 클라이언트 폰트로 그려지는가."""
    issues: list[Issue] = []
    for field, value in (
        ("name", guest.name),
        ("title", guest.title),
        ("bio", guest.bio),
        ("personality", guest.personality),
    ):
        issues += check_charset(value, field, bible)
    return issues


def check_guest_batch(guests: Sequence[Guest], bible: ProjectBible) -> list[Issue]:
    """한 번에 생성된 손님들을 통째로 본다.

    개별 검증만으로는 잡히지 않는 것이 하나 있다 — **ID 중복**이다. 계약은 ID가
    유일하고 발행 후 불변이기를 요구하는데(2절), 한 명씩 보면 그것을 볼 수 없다.

    필드 경로에 `items[i].`를 앞에 붙인다. guest_id가 유효하지 않을 수도 있어서
    ID가 아니라 자리로 가리킨다.
    """
    issues: list[Issue] = []

    for index, guest in enumerate(guests):
        issues += [
            issue.model_copy(update={"field": f"items[{index}].{issue.field}"})
            for issue in check_guest(guest, bible)
        ]

    counts = Counter(guest.guest_id for guest in guests)
    for index, guest in enumerate(guests):
        if counts[guest.guest_id] > 1:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"items[{index}].guest_id",
                    message=(
                        f"ID '{guest.guest_id}'가 이 배치 안에서 겹친다. 손님마다 다른 ID를 쓴다"
                    ),
                )
            )

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
            continue

        slider_span = axis.slider_max - axis.slider_min
        max_span = slider_span * bible.generation.max_ideal_span_ratio
        if ideal.high - ideal.low > max_span:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"ideal_ranges.{key}",
                    message=(
                        f"이상 구간 {ideal.low}~{ideal.high}의 폭이 축 '{key}'의 슬라이더 "
                        f"{slider_span} 중 {int(max_span)}을 넘는다. 이렇게 넓으면 "
                        "무엇을 내도 맞아서 취향이라고 할 수 없다. 좁게 잡거나, "
                        "이 축에 취향이 없다면 아예 비운다"
                    ),
                )
            )

    return issues


def _check_substance(guest: Guest, bible: ProjectBible) -> list[Issue]:
    """문법적으로 완벽하면서 쓸모없는 생성물을 잡는다.

    스키마도 어휘도 통과했는데 플레이할 수 없는 손님이 있다 — 취향이 하나도 없어
    추측할 것이 없거나, bio가 한 단어라 화면에 띄울 것이 없는 경우다.
    """
    spec = bible.generation
    issues: list[Issue] = []

    if len(guest.ideal_ranges) < spec.min_preferred_axes:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="ideal_ranges",
                message=(
                    f"취향이 있는 축이 {len(guest.ideal_ranges)}개다. 최소 "
                    f"{spec.min_preferred_axes}개는 있어야 한다 — 하나도 없으면 "
                    "플레이어가 추측할 것이 없어 손님 구실을 못 한다"
                ),
            )
        )

    count = len(guest.preferred_needs)
    if count < spec.min_preferred_needs:
        issues.append(
            Issue(
                severity=Severity.ERROR,
                field="preferred_needs",
                message=(
                    f"평소 욕구가 {count}개다. 최소 {spec.min_preferred_needs}개는 "
                    "있어야 오늘 무엇을 원할지 정할 수 있다"
                ),
            )
        )
    elif count > spec.max_preferred_needs:
        issues.append(
            Issue(
                severity=Severity.WARNING,
                field="preferred_needs",
                message=(
                    f"평소 욕구가 {count}개다. {spec.max_preferred_needs}개 이하가 좋다 — "
                    "많으면 아무 요리에나 만족해 밋밋해진다"
                ),
            )
        )

    for field, text in (("bio", guest.bio), ("personality", guest.personality)):
        if len(text.strip()) < spec.min_text_length:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=field,
                    message=(
                        f"{field}가 너무 짧다({len(text.strip())}자). "
                        f"{spec.min_text_length}자 이상으로 쓴다 — 짧으면 화면에 띄울 것도 "
                        "대사를 만들 재료도 없다"
                    ),
                )
            )

    return issues
