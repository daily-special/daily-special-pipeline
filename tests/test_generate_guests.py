"""생성 서비스를 고정한다.

**API 키 없이 전부 통과한다.** 가짜 어댑터가 미리 넣어둔 응답을 돌려주므로,
여기서 검사하는 것은 "모델이 무엇을 뱉었나"가 아니라 "뱉은 것을 우리가 어떻게 다루나"다.
"""

from typing import Any

import pytest

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.generate_guests import generate_guests
from daily_special.application.port.llm import Tier
from daily_special.application.schema_builder import build_guest_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import Severity, has_errors


def _bible() -> ProjectBible:
    def axis(key: str) -> dict[str, Any]:
        return {
            "key": key,
            "label": key,
            "description": key,
            "slider_min": 0,
            "slider_max": 100,
        }

    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling"), named("mild")],
            "axes": [axis("heat"), axis("seasoning")],
            "dietary_constraints": [named("no_meat")],
            "voices": [named("gruff")],
            "scoring": {
                "need_floor": 0.15,
                "axis_tolerance": 25,
                "budget_overrun_ratio": 1.5,
                "dietary_violation_factor": 0.1,
            },
        }
    )


def _item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "guest_id": "guest_test_01",
        "name": "테스트",
        "title": "시험용",
        "bio": "테스트에만 나온다.",
        "personality": "말이 없다.",
        "voice": "gruff",
        "preferred_needs": ["filling"],
        "dietary": [],
        "ideal_heat": {"low": 40, "high": 60},
        "ideal_seasoning": None,
    }
    item.update(overrides)
    return item


def _response(bible: ProjectBible, *items: dict[str, Any]) -> Any:
    """모델이 이렇게 뱉었다고 치고 가짜 어댑터에 넣을 응답을 만든다."""
    return build_guest_batch_schema(bible).model.model_validate({"guests": list(items)})


async def test_generates_guests_from_one_call() -> None:
    """배치인 이유는 품질이다. 한 명씩 부르면 매번 백지에서 시작해 비슷한 사람이 쌓인다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(guest_id="guest_test_02"))])

    result = await generate_guests(llm=llm, bible=bible, count=2)

    assert len(result.guests) == 2
    assert len(llm.calls) == 1, "배치 하나에 호출은 한 번이어야 한다"


async def test_uses_the_quality_tier() -> None:
    """페르소나는 품질이 눈에 보이는 곳이다 (규약 4-3)."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item())])

    await generate_guests(llm=llm, bible=bible, count=1)

    assert llm.calls[0][1] is Tier.QUALITY


async def test_result_is_contract_shaped() -> None:
    """되접기까지가 서비스의 일이다. 부르는 쪽은 동적 스키마를 몰라도 된다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item())])

    guest = (await generate_guests(llm=llm, bible=bible, count=1)).guests[0]

    assert guest.ideal_ranges["heat"].low == 40
    assert "seasoning" not in guest.ideal_ranges


async def test_rule_violation_is_reported_not_discarded() -> None:
    """규칙을 어겼다고 버리지 않는다 (규약 5-3). 재생성은 6단계의 판단이다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(ideal_heat={"low": 90, "high": 130}))])

    result = await generate_guests(llm=llm, bible=bible, count=1)

    assert len(result.guests) == 1, "생성물을 버렸다"
    assert has_errors(result.issues)
    assert result.issues[0].field == "items[0].ideal_ranges.heat"


async def test_duplicate_id_within_the_batch_is_an_error() -> None:
    """한 명씩 보면 잡히지 않는다. 계약은 ID가 유일하기를 요구한다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item())])

    result = await generate_guests(llm=llm, bible=bible, count=2)

    assert has_errors(result.issues)
    assert [issue.field for issue in result.issues] == [
        "items[0].guest_id",
        "items[1].guest_id",
    ]


async def test_issue_field_points_at_the_position_not_the_id() -> None:
    """guest_id 자체가 잘못됐을 수 있으므로 자리로 가리킨다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(guest_id="bad-id"))])

    result = await generate_guests(llm=llm, bible=bible, count=2)

    assert [issue.field for issue in result.issues] == ["items[1].guest_id"]


async def test_wrong_count_warns_instead_of_failing() -> None:
    """8명 요청에 7명이 왔다고 멀쩡한 7명을 통째로 다시 만들 이유가 없다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item())])

    result = await generate_guests(llm=llm, bible=bible, count=3)

    assert not has_errors(result.issues)
    assert result.issues[0].severity is Severity.WARNING
    assert result.issues[0].field == "items"


async def test_valid_batch_has_no_issues() -> None:
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(guest_id="guest_test_02"))])

    result = await generate_guests(llm=llm, bible=bible, count=2)

    assert result.issues == []


async def test_does_not_regenerate() -> None:
    """재생성은 6단계다. 두 층을 한 함수에 넣으면 호출 횟수를 추적할 수 없다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(voice="gruff", guest_id="bad-id"))])

    await generate_guests(llm=llm, bible=bible, count=1)

    assert len(llm.calls) == 1


async def test_non_positive_count_never_calls_the_model() -> None:
    """돈을 쓰기 전에 막는다."""
    bible = _bible()
    llm = FakeLlm([])

    with pytest.raises(DomainError):
        await generate_guests(llm=llm, bible=bible, count=0)

    assert llm.calls == []
