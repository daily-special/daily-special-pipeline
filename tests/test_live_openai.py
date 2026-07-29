"""실호출 테스트. 기본 실행에서 제외된다 (규약 6절).

    make live

돈이 드는 유일한 테스트다. 그래서 손님 2명만 뽑고, 검사하는 것은 품질이 아니라
**파이프라인이 실제로 이어지는가**다 — 동적 스키마가 strict 모드를 통과하고,
응답이 계약 모양으로 되접히고, 검증까지 무사히 도착하는가.

생성물의 질은 사람이 눈으로 본다. 그래서 결과를 출력한다 (`make live`는 -s로 돈다).
"""

import json
from pathlib import Path

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.adapter.outbound.llm.openai_llm import OpenAiLlm
from daily_special.application.generate_guests import generate_guests
from daily_special.application.generate_ingredients import generate_ingredients
from daily_special.application.review_guests import review_guests
from daily_special.domain.guest import Guest
from daily_special.domain.issue import Severity, has_errors
from daily_special.domain.package import SCHEMA_VERSION, Package

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"
GUESTS_PATH = REPO_ROOT / "out" / "packages" / SCHEMA_VERSION / "guests.json"


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


async def test_reviews_the_mock_guests() -> None:
    """검토 대상으로 손으로 쓴 목 손님 4명을 쓴다.

    생성 호출 없이 FAST 한 번이면 끝나고, 입력이 고정이라 결과를 눈으로 비교할 수 있다.
    이 넷은 서로 다르게 쓴 것이므로 지적이 없는 쪽이 맞다 — 그런데도 지적이 쏟아지면
    프롬프트가 "억지로 찾지 않는다"를 전달하지 못하고 있다는 신호다.
    """
    bible = load_bible(BIBLE_PATH)
    raw = json.loads(GUESTS_PATH.read_text(encoding="utf-8"))
    guests = Package[Guest].model_validate(raw).items

    result = await review_guests(llm=OpenAiLlm(), bible=bible, guests=guests)

    print(f"\n검토 대상 {len(guests)}명 / LLM 호출 {result.call_count}회")
    if not result.issues:
        print("  지적 없음")
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.field}: {issue.message}")

    assert result.call_count == 1
    assert all(issue.severity is Severity.WARNING for issue in result.issues), (
        "검토가 ERROR를 냈다 — 자동 재생성이 걸린다"
    )


async def test_review_actually_catches_an_overlap() -> None:
    """지적 없음만 내는 검토는 고장난 검토와 구별되지 않는다.

    깨끗한 입력에 조용한 것은 옳지만, 그것만으로는 이 층이 일하고 있다는 증거가 못 된다.
    일부러 겹치는 둘을 넣어 실제로 잡는지 본다 — 6단계의 "피드백이 조용히 전달되지
    않는" 함정과 같은 모양이다.
    """
    bible = load_bible(BIBLE_PATH)
    twins = [
        Guest(
            guest_id="guest_grim_scout_01",
            name="카렌",
            title="잿빛 정찰병",
            bio="북쪽 능선을 순찰하고 돌아오는 정찰병이다. 말없이 한 그릇을 비우고 간다.",
            personality="말수가 적고 칭찬을 아낀다. 마음에 들면 그릇을 끝까지 비운다.",
            voice="gruff",
            preferred_needs=["filling", "restorative"],
            ideal_ranges={"heat": {"low": 60, "high": 80}},  # type: ignore[dict-item]
            dietary=[],
        ),
        Guest(
            guest_id="guest_ash_ranger_02",
            name="다렌",
            title="잿빛 순찰자",
            bio="북쪽 산길을 순찰하고 돌아오는 순찰자다. 조용히 한 그릇을 비우고 간다.",
            personality="말이 없고 칭찬에 인색하다. 마음에 들면 그릇을 싹 비운다.",
            voice="gruff",
            preferred_needs=["filling", "restorative"],
            ideal_ranges={"heat": {"low": 62, "high": 78}},  # type: ignore[dict-item]
            dietary=[],
        ),
    ]

    result = await review_guests(llm=OpenAiLlm(), bible=bible, guests=twins)

    for issue in result.issues:
        print(f"\n  [{issue.severity}] {issue.field}: {issue.message}")

    assert result.issues, "쌍둥이 손님 둘을 그냥 통과시켰다 — 검토가 일하지 않는다"
    assert all(issue.severity is Severity.WARNING for issue in result.issues)


async def test_generates_real_ingredients() -> None:
    """재료 생성이 실제로 이어지는가.

    검사하는 것은 손님 때와 같다 — 동적 스키마가 strict 모드를 통과하고, 응답이 계약
    모양으로 되접히고, 검증까지 무사히 도착하는가.
    """
    bible = load_bible(BIBLE_PATH)
    result = await generate_ingredients(llm=OpenAiLlm(), bible=bible, count=6)

    for item in result.ingredients:
        conflicts = ", ".join(item.dietary_conflicts) or "없음"
        print(f"\n[{item.ingredient_id}] {item.name} ({item.kind}, {item.base_price})")
        print(f"  {item.description}")
        print(f"  저촉: {conflicts}")

    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.field}: {issue.message}")

    print(f"\nLLM 호출 {result.call_count}회")

    assert len(result.ingredients) == 6
    assert not has_errors(result.issues), "실제 생성물이 규칙을 어겼다"
