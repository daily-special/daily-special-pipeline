"""대사 — `lines.json` 한 항목의 정의.

대사 풀은 **상황 × 대상 × 말투**로 짜인다. 상황과 말투만으로는 "속이 쓰려요"와
"좀 짠데요"를 만들 수 없다 — 어느 욕구인지, 어느 축인지가 대사를 정한다.
정보 공개 곡선이 그 두 대사 위에 얹혀 있다 (design.md 5장).

`subject`는 **한 가지 의미만 갖는다** — "이 대사가 가리키는 대상". 어느 어휘에서 오는지는
상황이 선언한다. 상황마다 필드를 따로 두면(`need`, `axis`, ...) 대부분이 늘 비어 있고,
어느 것이 채워져야 하는지를 코드가 다시 판단해야 한다.

**게임 내부 상수에 결합하지 않는다** (계약 2-3절). 대사는 사람이 검수한 결과물이라
비싸고, 상수가 바뀔 때 검수 결과까지 버려지면 안 된다.
"""

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import ProjectBible, SubjectKind
from daily_special.domain.charset import check_charset
from daily_special.domain.issue import Issue, Severity

_LINE_ID = re.compile(r"^line_[a-z0-9_]+$")
_MAX_ID_LEN = 64


class DialogueLine(BaseModel):
    """대사 한 줄.

    같은 (상황·대상·말투)에 여러 줄이 있고 런타임이 그중 하나를 고른다. 한 줄뿐이면
    같은 손님이 매번 똑같은 말을 한다.
    """

    model_config = ConfigDict(frozen=True)

    line_id: str
    """`line_` 접두사 슬러그. 발행 후 불변."""

    situation: str
    """언제 나오는 대사인가. ProjectBible의 상황 어휘다."""

    subject: str | None = None
    """이 대사가 가리키는 대상. 상황이 `none`을 선언했으면 비어 있다.

    키가 아니라 값이 없는 것이므로 `null`을 쓴다 (계약 1-3절).
    """

    voice: str
    """말투 키. 손님의 `voice`와 이어지는 조인 키다."""

    text: str
    """대사 본문. 플레이어가 그대로 읽는다."""


def check_line(line: DialogueLine, bible: ProjectBible) -> list[Issue]:
    """계약과 ProjectBible 어휘에 맞는지 본다.

    핵심은 하나다 — **`subject`가 상황이 선언한 어휘에 맞는가.** 여기가 뚫리면
    런타임이 대사를 못 고르거나 엉뚱한 자리에서 고른다.
    """
    issues: list[Issue] = []

    issues += _check_id(line)
    issues += _check_voice(line, bible)
    issues += _check_situation_and_subject(line, bible)
    issues += _check_text(line, bible)
    issues += check_charset(line.text, "text", bible)

    return issues


def check_line_batch(lines: Sequence[DialogueLine], bible: ProjectBible) -> list[Issue]:
    """배치 전체를 본다. ID 중복과, 같은 자리에 대사가 하나뿐인가.

    커버리지(모든 조합이 찼는가)는 여기서 보지 않는다. 생성이 배치로 나뉘므로 한
    배치가 전부를 덮을 수 없다. **최종 패키지를 낼 때 봐야 하는 검사다** — 빈 조합이
    있으면 런타임에 그 자리에서 손님이 입을 다문다.
    """
    issues: list[Issue] = []

    for index, line in enumerate(lines):
        issues += [
            issue.model_copy(update={"field": f"items[{index}].{issue.field}"})
            for issue in check_line(line, bible)
        ]

    counts = Counter(line.line_id for line in lines)
    for index, line in enumerate(lines):
        if counts[line.line_id] > 1:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    field=f"items[{index}].line_id",
                    message=(
                        f"ID '{line.line_id}'가 이 배치 안에서 겹친다. 대사마다 다른 ID를 쓴다"
                    ),
                )
            )

    issues += _check_duplicate_text(lines)
    return issues


def check_line_coverage(lines: Sequence[DialogueLine], bible: ProjectBible) -> list[Issue]:
    """모든 (상황 × 대상 × 말투) 자리가 찼는가. **최종 패키지에만 쓴다.**

    배치 검증에 섞지 않는 이유는 한 배치가 전부를 덮을 수 없기 때문이다. 그러나
    출력 시점에는 이것이 가장 중요한 검사다 — 빈 자리는 런타임에 손님이 입을 다무는
    것으로 나타나고, 그건 테스트로는 안 잡힌다.
    """
    filled = {(line.situation, line.subject, line.voice) for line in lines}
    missing: list[str] = []

    for situation in bible.situations:
        for subject in _subjects_for(situation.subject, bible):
            for voice in bible.voices:
                if (situation.key, subject, voice.key) not in filled:
                    label = f"{situation.key}/{subject or '-'}/{voice.key}"
                    missing.append(label)

    if not missing:
        return []

    shown = ", ".join(missing[:10])
    more = f" 외 {len(missing) - 10}개" if len(missing) > 10 else ""
    return [
        Issue(
            severity=Severity.ERROR,
            field="items",
            message=(
                f"대사가 없는 자리가 {len(missing)}개 있다: {shown}{more}. "
                "그 자리에서 손님이 입을 다문다"
            ),
        )
    ]


def _subjects_for(kind: SubjectKind, bible: ProjectBible) -> list[str | None]:
    """이 상황의 대상이 될 수 있는 값들."""
    if kind is SubjectKind.NEED:
        return [need.key for need in bible.needs]
    if kind is SubjectKind.AXIS:
        return [axis.key for axis in bible.axes]
    return [None]


def _check_id(line: DialogueLine) -> list[Issue]:
    if not _LINE_ID.match(line.line_id):
        return [
            Issue(
                severity=Severity.ERROR,
                field="line_id",
                message=(
                    f"'{line.line_id}'는 대사 ID 문법이 아니다. "
                    "'line_'으로 시작하고 소문자·숫자·밑줄만 쓴다 (예: line_greet_gruff_01)"
                ),
            )
        ]
    if len(line.line_id) > _MAX_ID_LEN:
        return [
            Issue(
                severity=Severity.ERROR,
                field="line_id",
                message=f"대사 ID가 {_MAX_ID_LEN}자를 넘는다: {len(line.line_id)}자",
            )
        ]
    return []


def _check_voice(line: DialogueLine, bible: ProjectBible) -> list[Issue]:
    if bible.find_voice(line.voice) is not None:
        return []

    known = ", ".join(voice.key for voice in bible.voices)
    return [
        Issue(
            severity=Severity.ERROR,
            field="voice",
            message=f"'{line.voice}'는 없는 말투다. 다음 중에서 고른다: {known}",
        )
    ]


def _check_situation_and_subject(line: DialogueLine, bible: ProjectBible) -> list[Issue]:
    """대상이 상황이 선언한 어휘에서 왔는가.

    상황마다 대상이 어디서 오는지가 다르므로 둘을 함께 본다. 따로 보면 "주문 상황에
    파라미터 축이 대상으로 들어온" 경우를 잡을 수 없다.
    """
    situation = bible.find_situation(line.situation)
    if situation is None:
        known = ", ".join(item.key for item in bible.situations)
        return [
            Issue(
                severity=Severity.ERROR,
                field="situation",
                message=f"'{line.situation}'는 없는 상황이다. 다음 중에서 고른다: {known}",
            )
        ]

    allowed = _subjects_for(situation.subject, bible)
    if line.subject in allowed:
        return []

    if situation.subject is SubjectKind.NONE:
        message = (
            f"상황 '{line.situation}'는 대상이 없는 대사인데 '{line.subject}'가 들어 있다. "
            "비워 둔다"
        )
    else:
        known = ", ".join(str(key) for key in allowed)
        message = (
            f"상황 '{line.situation}'의 대상은 {situation.subject} 어휘여야 한다. "
            f"'{line.subject}'는 그중에 없다. 다음 중에서 고른다: {known}"
        )

    return [Issue(severity=Severity.ERROR, field="subject", message=message)]


def _check_text(line: DialogueLine, bible: ProjectBible) -> list[Issue]:
    """대사는 짧아야 하지만 비어서는 안 된다."""
    text = line.text.strip()
    if not text:
        return [Issue(severity=Severity.ERROR, field="text", message="대사가 비어 있다")]

    if len(text) > bible.generation.max_line_length:
        return [
            Issue(
                severity=Severity.ERROR,
                field="text",
                message=(
                    f"대사가 너무 길다({len(text)}자). "
                    f"{bible.generation.max_line_length}자 이하로 쓴다 — "
                    "손님 한마디가 화면을 덮으면 게임이 멈춘 것처럼 보인다"
                ),
            )
        ]

    return []


def _check_duplicate_text(lines: Sequence[DialogueLine]) -> list[Issue]:
    """같은 문장이 두 번 나오면 변주가 아니다.

    한 자리에 여러 줄을 두는 이유가 반복감을 없애는 것인데, 같은 문장이면 그 목적이
    사라진다. 문자열 비교로 잡히는 유일한 종류의 겹침이라 여기서 본다.
    """
    counts = Counter(line.text.strip() for line in lines)
    return [
        Issue(
            severity=Severity.ERROR,
            field=f"items[{index}].text",
            message=f"같은 대사가 배치 안에 두 번 있다: '{line.text.strip()}'",
        )
        for index, line in enumerate(lines)
        if counts[line.text.strip()] > 1
    ]
