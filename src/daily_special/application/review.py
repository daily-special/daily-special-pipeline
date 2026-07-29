"""의미 검토의 핵심 — 콘텐츠 종류를 모른다.

**이 저장소에서 돈을 쓰는 유일한 검사다.** 그래서 정당화가 필요하다: 규칙 검증이
할 수 있는 일은 여기 오지 않는다 (규약 5-1의 3층).

코드가 못 잡는 것은 하나다 — **서로 비슷한가.** "무뚝뚝한 정찰병"과 "과묵한 순찰자",
"말린 강가 허브"와 "건조 강변 약초"는 ID도 이름도 다르고 모든 규칙을 통과하면서
같은 것이다. 문자열 비교로는 잡을 수 없다.

무엇을 검토하든 규칙이 같아서 종류를 모른다. 걸러내기와 렌더링은 부르는 쪽이 하고,
여기는 "이미 정해진 지시문과 본문으로 판정을 받아 Issue로 옮긴다"만 한다.

결과는 전부 WARNING이다. 검토가 지목했다고 자동으로 버리지 않는다 (규약 5-4·5-5).
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.common.errors import LlmError
from daily_special.domain.issue import Issue, Severity


class Verdict(StrEnum):
    """검토가 내릴 수 있는 판정. 둘 다 규칙 검증이 판정할 수 없는 것이다."""

    OVERLAP = "overlap"
    """둘이 사실상 같은 것이다. 이름과 ID가 달라도 겹칠 수 있다."""

    INCOHERENT = "incoherent"
    """설명과 수치가 앞뒤로 맞지 않는다. 문법적으로는 완벽하다."""


class ReviewFinding(BaseModel):
    """지적 하나. 판정 + 근거 + 수정 방향 (규약 5-1)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    subject_ids: list[str] = Field(description="이 지적에 걸린 항목들의 ID. 겹침은 둘 이상이 된다.")
    reason: str = Field(description="왜 그렇게 보는지 한 문장. 무엇이 겹치는지 짚는다.")
    suggestion: str = Field(description="어느 쪽을 어떻게 바꾸면 되는지 한 문장.")


class ReviewResponse(BaseModel):
    """모델이 뱉는 검토 결과."""

    model_config = ConfigDict(extra="forbid")

    findings: list[ReviewFinding] = Field(description="문제가 없으면 빈 배열. 억지로 찾지 않는다.")


class Review(BaseModel):
    """검토 결과. 생성물을 바꾸지 않고 경고만 붙인다."""

    model_config = ConfigDict(frozen=True)

    issues: list[Issue]
    """전부 WARNING이다. 자동 재생성을 부르지 않는다."""

    call_count: int
    """0일 수 있다 — 검토할 대상이 없으면 부르지 않는다."""


async def review_subjects(
    *,
    llm: LlmPort,
    instruction: str,
    context: str,
    subject_ids: Sequence[str],
) -> Review:
    """이미 걸러지고 렌더링된 대상들을 검토한다.

    둘 미만이면 부르지 않는다. 주 판정이 "서로 비슷한가"라 비교 대상이 없으면
    판정 자체가 불가능하고, 남는 판정은 값이 적다.
    """
    if len(subject_ids) < 2:
        return Review(issues=[], call_count=0)

    try:
        response = await llm.generate(
            instruction=instruction,
            context=context,
            schema=ReviewResponse,
            tier=Tier.FAST,
        )
    except LlmError as error:
        # 검토는 보조 장치다. 여기서 예외를 올리면 이미 만들어진 멀쩡한 생성물까지 잃는다.
        return Review(
            issues=[
                Issue(
                    severity=Severity.WARNING,
                    field="items",
                    message=f"의미 검토를 하지 못했다: {error}",
                )
            ],
            call_count=1,
        )

    return Review(issues=_to_issues(response, subject_ids), call_count=1)


def _to_issues(response: ReviewResponse, subject_ids: Sequence[str]) -> list[Issue]:
    """지적을 Issue로 옮긴다. 전부 WARNING이다.

    이미 있는 통로를 쓰는 이유는 9단계 출력이 그대로 경고를 싣고 나가기 때문이다.
    검토 전용 자료구조를 새로 만들면 출력 통로가 둘이 된다.
    """
    index_of = {subject_id: index for index, subject_id in enumerate(subject_ids)}
    issues: list[Issue] = []

    for finding in response.findings:
        known = [key for key in finding.subject_ids if key in index_of]
        # 검토가 없는 것을 지목했다면 그 지적은 근거가 없다. 그래도 버리지 않는다 —
        # 검토가 헛것을 봤다는 사실 자체가 신호다.
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
