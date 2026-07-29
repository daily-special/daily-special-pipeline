"""손님 페르소나 생성.

생성 → 검증 → 재생성까지 한다. 규칙을 어긴 손님만 골라 모자란 만큼 다시 부르고,
피드백으로 무엇이 틀렸는지 실어 보낸다.

**소진해도 생성물을 버리지 않는다** (규약 5-3). 3회를 다 써도 고쳐지지 않은 손님은
Issue를 붙인 채 함께 나간다 — 여기서 예외를 올리면 같은 배치의 멀쩡한 손님까지 잃는다.
"""

from pydantic import BaseModel, ConfigDict

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import build_guest_context, build_guest_instruction
from daily_special.application.regenerate import (
    MAX_REGENERATIONS,
    partition_by_errors,
)
from daily_special.application.schema_builder import build_guest_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest, check_guest_batch
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

    call_count: int
    """LLM을 몇 번 불렀는가. 오프라인 배치라 이 값이 곧 비용이다.

    재생성이 실제로 몇 번 돌았는지는 이 값에서만 보인다. 로그가 없으면 루프가
    조용히 세 번씩 도는 것을 아무도 모른다.
    """


async def generate_guests(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    count: int,
    max_regenerations: int = MAX_REGENERATIONS,
) -> GuestGeneration:
    """손님 count명을 만든다. 규칙을 어긴 손님은 그 인원만 다시 뽑는다.

    첫 호출은 배치로 간다 — 한 응답 안에 여러 명이 있으면 모델이 서로 겹치지 않게
    만든다. 한 명씩 부르면 매번 백지에서 시작해 비슷한 사람이 쌓인다.
    """
    schema = build_guest_batch_schema(bible)

    async def call(needed: int, kept: list[Guest], feedback: list[Issue]) -> list[Guest]:
        response = await llm.generate(
            instruction=build_guest_instruction(),
            context=build_guest_context(bible, needed, existing=kept, issues=feedback),
            schema=schema.model,
            tier=Tier.QUALITY,
        )
        return schema.to_guests(response)

    if max_regenerations < 0:
        raise DomainError(f"max_regenerations는 0 이상이어야 한다: {max_regenerations}")

    guests = await call(count, [], [])
    calls = 1

    for _ in range(max_regenerations):
        partition = partition_by_errors(
            guests,
            check=lambda guest: check_guest(guest, bible),
            id_of=lambda guest: guest.guest_id,
        )
        if not partition.rejected:
            break

        replacements = await call(len(partition.rejected), partition.kept, partition.issues)
        calls += 1
        guests = [*partition.kept, *replacements]

    # 루프를 소진해도 guests는 그대로 들고 나간다. 고쳐지지 않은 손님도 버리지 않는다.
    issues = check_guest_batch(guests, bible)
    issues += _check_count(guests, count)

    return GuestGeneration(guests=guests, issues=issues, call_count=calls)


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
