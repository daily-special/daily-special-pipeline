"""실호출 테스트. 기본 실행에서 제외된다 (규약 6절).

    make live

돈이 드는 유일한 테스트다. 그래서 손님 2명만 뽑고, 검사하는 것은 품질이 아니라
**파이프라인이 실제로 이어지는가**다 — 동적 스키마가 strict 모드를 통과하고,
응답이 계약 모양으로 되접히고, 검증까지 무사히 도착하는가.

생성물의 질은 사람이 눈으로 본다. 그래서 결과를 출력한다 (`make live`는 -s로 돈다).
"""

from pathlib import Path

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.adapter.outbound.llm.openai_llm import OpenAiLlm
from daily_special.application.generate_guests import generate_guests
from daily_special.domain.issue import has_errors

pytestmark = pytest.mark.live

BIBLE_PATH = Path(__file__).resolve().parent.parent / "data" / "project_bible.json"


async def test_generates_real_guests() -> None:
    bible = load_bible(BIBLE_PATH)
    result = await generate_guests(llm=OpenAiLlm(), bible=bible, count=2)

    for guest in result.guests:
        ranges = ", ".join(
            f"{key} {ideal.low}~{ideal.high}" for key, ideal in guest.ideal_ranges.items()
        )
        print(f"\n[{guest.guest_id}] {guest.name} — {guest.title} ({guest.voice})")
        print(f"  {guest.bio}")
        print(f"  성격: {guest.personality}")
        print(f"  욕구: {', '.join(guest.preferred_needs)} / 식이: {guest.dietary or '없음'}")
        print(f"  취향: {ranges or '없음'}")

    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.field}: {issue.message}")

    # 호출 횟수가 곧 비용이다. 재생성이 조용히 세 번씩 도는 것을 눈으로 보기 위해 남긴다.
    print(f"\nLLM 호출 {result.call_count}회")

    assert len(result.guests) == 2
    assert not has_errors(result.issues), "실제 생성물이 규칙을 어겼다"


async def test_generated_personas_are_distinct() -> None:
    """배치로 뽑는 유일한 이유가 이것이다. 겹치면 배치의 값어치가 없다."""
    bible = load_bible(BIBLE_PATH)
    result = await generate_guests(llm=OpenAiLlm(), bible=bible, count=3)

    names = [guest.name for guest in result.guests]
    voices = [guest.voice for guest in result.guests]

    print(f"\n이름: {names}\n말투: {voices}")

    assert len(set(names)) == len(names), "같은 이름이 나왔다"
    assert len(set(guest.guest_id for guest in result.guests)) == len(result.guests)
