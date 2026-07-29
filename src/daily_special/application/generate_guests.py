"""손님 페르소나 생성.

생성 → 되접기 → 검증까지 한다. **재생성은 하지 않는다** — 그것은 6단계의 일이고,
여기서는 Issue를 붙여 그대로 돌려준다. 두 층을 한 함수에 넣으면 LLM 호출 횟수를
추적할 수 없게 된다 (규약 5-3).
"""

from pydantic import BaseModel, ConfigDict

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import build_guest_context, build_guest_instruction
from daily_special.application.schema_builder import build_guest_batch_schema
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest_batch
from daily_special.domain.issue import Issue, Severity


class GuestGeneration(BaseModel):
    """생성 결과와 그 검증 결과.

    생성물과 Issue를 함께 돌려주는 이유는, 규칙을 어겼다고 버리지 않기 때문이다.
    재생성을 소진해도 경고를 붙여 내보낸다 (규약 5-3).
    """

    model_config = ConfigDict(frozen=True)

    guests: list[Guest]
    issues: list[Issue]
    """필드 경로가 `items[i].voice` 꼴이라 어느 손님의 문제인지 자리로 가리킨다."""


async def generate_guests(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    count: int,
) -> GuestGeneration:
    """손님 count명을 한 번의 호출로 만든다.

    1인 1호출이 아니라 배치인 이유는 품질이다. 한 응답 안에 여러 명이 있으면 모델이
    서로 겹치지 않게 만든다. 한 명씩 부르면 매번 백지에서 시작해 비슷한 사람이 쌓인다.
    중복 회피는 규칙으로 잡을 수 없는 문제라 여기서 공짜로 얻는 편이 낫다.
    """
    schema = build_guest_batch_schema(bible)
    context = build_guest_context(bible, count)

    response = await llm.generate(
        instruction=build_guest_instruction(),
        context=context,
        schema=schema.model,
        tier=Tier.QUALITY,
    )

    guests = schema.to_guests(response)
    issues = check_guest_batch(guests, bible)
    issues += _check_count(guests, count)

    return GuestGeneration(guests=guests, issues=issues)


def _check_count(guests: list[Guest], requested: int) -> list[Issue]:
    """요청한 수와 다르면 알리되 배치를 되던지지는 않는다.

    ERROR로 두면 8명 요청에 7명이 왔을 때 멀쩡한 7명을 통째로 다시 만들게 된다.
    모자란 만큼 더 뽑는 편이 싸므로, 그 판단을 재생성 층에 넘긴다.
    """
    if len(guests) == requested:
        return []

    return [
        Issue(
            severity=Severity.WARNING,
            field="items",
            message=f"손님 {requested}명을 요청했으나 {len(guests)}명이 왔다",
        )
    ]
