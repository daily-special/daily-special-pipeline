"""재생성 정책 — 누가 살아남고 누가 다시 만들어지는가.

순수 함수다. LLM을 부르지 않는다. 부르는 쪽(`generate_guests`)이 이 판단을 받아
모자란 만큼만 다시 뽑는다.

**배치 전체가 아니라 어긴 사람만 다시 만든다.** 8명 중 1명이 틀렸을 때 8명을 다시
뽑으면 멀쩡한 7명을 버리고 돈을 8배로 쓴다. 게다가 다시 뽑은 8명이 이번엔 다른 곳에서
틀릴 수 있어 루프가 수렴하지 않는다.
"""

from pydantic import BaseModel, ConfigDict

from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.issue import Issue, Severity, has_errors

MAX_REGENERATIONS = 3
"""검증 실패로 다시 부르는 최대 횟수 (규약 5-3).

전송 실패 재시도와 다른 층이다. 그쪽은 어댑터가 세고, 이쪽은 서비스가 센다.
한 곳에 두면 LLM 호출 횟수를 추적할 수 없게 된다.
"""


class Partition(BaseModel):
    """검증을 통과한 손님과 다시 만들어야 할 손님."""

    model_config = ConfigDict(frozen=True)

    kept: list[Guest]
    rejected: list[Guest]
    issues: list[Issue]
    """**rejected가** 왜 걸렸는지. 그대로 재생성 프롬프트에 실린다.

    통과한 손님의 경고는 넣지 않는다. 다시 만들지 않을 것을 고치라고 말하면
    모델이 엉뚱한 데 힘을 쓴다.
    """


def partition_by_errors(guests: list[Guest], bible: ProjectBible) -> Partition:
    """ERROR가 있는 손님만 골라낸다. WARNING은 통과시킨다.

    Issue의 필드 경로(`items[3].voice`)를 되파싱하지 않고 손님별로 다시 검사한다.
    문자열을 파싱하면 경로 형식이 바뀔 때 조용히 깨지고, 재검사는 공짜다.

    ID 중복은 개별 검사로 잡히지 않으므로 여기서 본다. **먼저 온 쪽을 남긴다** —
    둘 다 버리면 멀쩡한 손님 하나를 이유 없이 잃는다.
    """
    kept: list[Guest] = []
    rejected: list[Guest] = []
    issues: list[Issue] = []
    seen_ids: set[str] = set()

    for guest in guests:
        guest_issues = check_guest(guest, bible)

        if guest.guest_id in seen_ids:
            guest_issues = [*guest_issues, _duplicate_issue(guest)]

        if has_errors(guest_issues):
            rejected.append(guest)
            issues += guest_issues
        else:
            kept.append(guest)
            seen_ids.add(guest.guest_id)

    return Partition(kept=kept, rejected=rejected, issues=issues)


def _duplicate_issue(guest: Guest) -> Issue:
    return Issue(
        severity=Severity.ERROR,
        field="guest_id",
        message=(f"ID '{guest.guest_id}'가 이미 있는 손님과 겹친다. 손님마다 다른 ID를 쓴다"),
    )
