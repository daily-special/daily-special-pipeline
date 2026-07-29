"""대사 생성.

앞의 셋과 호출 구조가 다르다. **한 번의 호출이 하나의 (상황, 대상) 자리를 맡고**,
그 안에서 말투마다 한 줄씩 낸다.

자리마다 부르는 이유는 둘이다. 컨텍스트가 짧아 모델이 그 자리에만 집중하고,
한 자리가 실패해도 나머지 79자리를 다시 뽑지 않는다.

말투를 응답 안에 함께 두는 이유는 손님 배치와 같다 — 한 응답에 여러 말투가 있으면
모델이 같은 문장을 어미만 바꿔 쓰지 않고 서로 다르게 쓴다.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.application.port.llm import LlmPort, Tier
from daily_special.application.prompt import build_line_context, build_line_instruction
from daily_special.application.regenerate import MAX_REGENERATIONS, partition_by_errors
from daily_special.application.schema_builder import LineBatchSchema, build_line_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible, SituationSpec, SubjectKind
from daily_special.domain.issue import Issue
from daily_special.domain.line import DialogueLine, check_line, check_line_batch


class LineGeneration(BaseModel):
    """생성 결과와 그 검증 결과."""

    model_config = ConfigDict(frozen=True)

    lines: list[DialogueLine]
    issues: list[Issue]
    call_count: int
    """자리마다 최소 한 번이라 앞의 셋보다 훨씬 크다. 그래서 더 봐야 한다."""


def plan_slots(bible: ProjectBible) -> list[tuple[SituationSpec, str | None]]:
    """채워야 하는 (상황, 대상) 자리 전부.

    이 목록의 길이가 곧 최소 호출 횟수다. 상황을 하나 늘리면 여기가 늘고,
    그만큼 돈이 든다 — 어휘를 늘리기 전에 이 수를 본다.
    """
    slots: list[tuple[SituationSpec, str | None]] = []

    for situation in bible.situations:
        if situation.subject is SubjectKind.NEED:
            slots += [(situation, need.key) for need in bible.needs]
        elif situation.subject is SubjectKind.AXIS:
            slots += [(situation, axis.key) for axis in bible.axes]
        else:
            slots.append((situation, None))

    return slots


async def generate_lines(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    slots: Sequence[tuple[SituationSpec, str | None]] | None = None,
    max_regenerations: int = MAX_REGENERATIONS,
) -> LineGeneration:
    """자리마다 말투 수만큼의 대사를 만든다.

    `slots`를 주지 않으면 전부 채운다. 일부만 다시 뽑고 싶을 때 잘라서 넘긴다 —
    대사는 자리가 많아 통째로 다시 뽑는 비용이 크다.
    """
    if max_regenerations < 0:
        raise DomainError(f"max_regenerations는 0 이상이어야 한다: {max_regenerations}")

    targets = list(slots) if slots is not None else plan_slots(bible)
    if not targets:
        raise DomainError("채울 자리가 없다")

    schema = build_line_batch_schema(bible)
    lines: list[DialogueLine] = []
    calls = 0

    for situation, subject in targets:
        produced, used = await _fill_slot(
            llm=llm,
            bible=bible,
            schema=schema,
            situation=situation,
            subject=subject,
            max_regenerations=max_regenerations,
        )
        lines += produced
        calls += used

    return LineGeneration(lines=lines, issues=check_line_batch(lines, bible), call_count=calls)


async def _fill_slot(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    schema: LineBatchSchema,
    situation: SituationSpec,
    subject: str | None,
    max_regenerations: int,
) -> tuple[list[DialogueLine], int]:
    """한 자리를 채운다."""

    async def call(feedback: list[Issue]) -> list[DialogueLine]:
        response = await llm.generate(
            instruction=build_line_instruction(),
            context=build_line_context(bible, situation, subject, issues=feedback),
            schema=schema.model,
            tier=Tier.QUALITY,
        )
        return schema.to_lines(response, situation.key, subject)

    lines = await call([])
    calls = 1

    for _ in range(max_regenerations):
        partition = partition_by_errors(
            lines,
            check=lambda line: check_line(line, bible),
            id_of=lambda line: line.line_id,
        )
        if not partition.rejected:
            break

        # 한 자리는 통째로 다시 뽑는다. 말투별로 한 줄씩이라 부분 재생성이
        # 오히려 말투 대비를 잃게 만든다 — 남은 줄을 모르는 채 새 줄이 나온다.
        lines = await call(partition.issues)
        calls += 1

    return lines, calls
