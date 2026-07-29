"""의미 검토 — 코드가 판정할 수 없는 것만 LLM에게 묻는다.

**이 저장소에서 돈을 쓰는 유일한 검사다.** 그래서 정당화가 필요하다: 규칙 검증이
할 수 있는 일은 여기 오지 않는다 (규약 5-1의 3층).

코드가 못 잡는 것은 하나다 — **손님들이 서로 비슷한가.** "무뚝뚝한 정찰병"과
"과묵한 순찰자"는 ID도 이름도 다르고 모든 규칙을 통과하면서 같은 캐릭터다.
문자열 비교로는 잡을 수 없다.

결과는 전부 WARNING이다. 검토가 지목했다고 자동으로 버리지 않는다 (규약 5-4) —
몇 점이 "너무 비슷한가"는 생성물이 쌓여야 알 수 있고, 근거 없는 컷은 멀쩡한 생성물을
버리면서 재생성 비용까지 낸다. 판단은 사람이 한다.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import build_review_context, build_review_instruction
from daily_special.common.errors import LlmError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.issue import Issue, Severity, has_errors


class Verdict(StrEnum):
    """검토가 내릴 수 있는 판정. 둘 다 규칙 검증이 판정할 수 없는 것이다."""

    OVERLAP = "overlap"
    """두 손님이 사실상 같은 캐릭터다. 이름과 ID가 달라도 겹칠 수 있다."""

    INCOHERENT = "incoherent"
    """사연과 수치가 앞뒤로 맞지 않는다. 문법적으로는 완벽하다."""


class ReviewFinding(BaseModel):
    """지적 하나. 판정 + 근거 + 수정 방향 (규약 5-1)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    guest_ids: list[str] = Field(
        description="이 지적에 걸린 손님들의 guest_id. 겹침은 둘 이상이 된다."
    )
    reason: str = Field(description="왜 그렇게 보는지 한 문장. 무엇이 겹치는지 짚는다.")
    suggestion: str = Field(description="어느 쪽을 어떻게 바꾸면 되는지 한 문장.")


class ReviewResponse(BaseModel):
    """모델이 뱉는 검토 결과."""

    model_config = ConfigDict(extra="forbid")

    findings: list[ReviewFinding] = Field(description="문제가 없으면 빈 배열. 억지로 찾지 않는다.")


class GuestReview(BaseModel):
    """검토 결과. 생성물을 바꾸지 않고 경고만 붙인다."""

    model_config = ConfigDict(frozen=True)

    issues: list[Issue]
    """전부 WARNING이다. 자동 재생성을 부르지 않는다."""

    call_count: int
    """0일 수 있다 — 검토할 대상이 없으면 부르지 않는다."""


async def review_guests(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    guests: Sequence[Guest],
) -> GuestReview:
    """배치를 통째로 검토한다.

    한 명씩 부르지 않는 이유는 주 목적이 "서로 비슷한가"이기 때문이다. 비교 대상이
    같은 컨텍스트에 없으면 그 판정 자체가 불가능하다.

    **규칙이 깨진 손님은 넘기지 않는다** (규약 5-1). 어차피 다시 만들 것이라
    호출만 낭비된다.
    """
    reviewable = [guest for guest in guests if not has_errors(check_guest(guest, bible))]

    if len(reviewable) < 2:
        # 비교할 상대가 없으면 겹침을 볼 수 없고, 남는 판정은 값이 적다.
        return GuestReview(issues=[], call_count=0)

    try:
        response = await llm.generate(
            instruction=build_review_instruction(),
            context=build_review_context(reviewable),
            schema=ReviewResponse,
            tier=Tier.FAST,
        )
    except LlmError as error:
        # 검토는 보조 장치다. 여기서 예외를 올리면 이미 만들어진 멀쩡한 생성물까지 잃는다.
        return GuestReview(
            issues=[
                Issue(
                    severity=Severity.WARNING,
                    field="items",
                    message=f"의미 검토를 하지 못했다: {error}",
                )
            ],
            call_count=1,
        )

    return GuestReview(issues=_to_issues(response, reviewable), call_count=1)


def _to_issues(response: ReviewResponse, guests: Sequence[Guest]) -> list[Issue]:
    """지적을 Issue로 옮긴다. 전부 WARNING이다.

    이미 있는 통로를 쓰는 이유는 9단계 출력이 그대로 경고를 싣고 나가기 때문이다.
    검토 전용 자료구조를 새로 만들면 출력 통로가 둘이 된다.
    """
    index_of = {guest.guest_id: index for index, guest in enumerate(guests)}
    issues: list[Issue] = []

    for finding in response.findings:
        known = [gid for gid in finding.guest_ids if gid in index_of]
        # 검토가 없는 손님을 지목했다면 그 지적은 근거가 없다. 자리를 특정하지 않고 남긴다.
        field = f"items[{index_of[known[0]]}]" if known else "items"
        who = ", ".join(known) if known else "(대상 불명)"

        issues.append(
            Issue(
                severity=Severity.WARNING,
                field=field,
                message=f"[{finding.verdict}] {who} — {finding.reason} 제안: {finding.suggestion}",
            )
        )

    return issues
