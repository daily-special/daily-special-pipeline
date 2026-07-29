"""손님 검토. 걸러내기와 렌더링만 하고 판정은 `review`가 한다."""

from collections.abc import Sequence

from daily_special.application.port.llm import LlmPort
from daily_special.application.prompt import (
    build_guest_review_context,
    build_guest_review_instruction,
)
from daily_special.application.review import Review, review_subjects
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.issue import has_errors


async def review_guests(
    *,
    llm: LlmPort,
    bible: ProjectBible,
    guests: Sequence[Guest],
) -> Review:
    """배치를 통째로 검토한다.

    **규칙이 깨진 손님은 넘기지 않는다** (규약 5-1). 어차피 다시 만들 것이라
    호출만 낭비된다.
    """
    reviewable = [guest for guest in guests if not has_errors(check_guest(guest, bible))]
    if len(reviewable) < 2:
        return Review(issues=[], call_count=0)

    return await review_subjects(
        llm=llm,
        instruction=build_guest_review_instruction(),
        context=build_guest_review_context(reviewable),
        subject_ids=[guest.guest_id for guest in reviewable],
    )
