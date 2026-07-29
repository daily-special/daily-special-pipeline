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
            "situations": [
                {
                    "key": "greet",
                    "label": "인사",
                    "description": "들어올 때",
                    "subject": "none",
                },
            ],
            "economy": {
                "wallet_min": 8,
                "wallet_max": 40,
                "dish_price_min": 6,
                "dish_price_max": 30,
                "ingredient_price_min": 1,
                "ingredient_price_max": 8,
            },
            "generation": {
                "max_ideal_span_ratio": 0.5,
                "min_preferred_axes": 1,
                "min_preferred_needs": 1,
                "max_preferred_needs": 2,
                "min_text_length": 1,
                "max_dish_need_tags": 1,
                "min_dish_ingredients": 2,
                "max_dish_ingredients": 4,
                "max_line_length": 40,
            },
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

    assert llm.calls[0].tier is Tier.QUALITY


async def test_result_is_contract_shaped() -> None:
    """되접기까지가 서비스의 일이다. 부르는 쪽은 동적 스키마를 몰라도 된다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item())])

    guest = (await generate_guests(llm=llm, bible=bible, count=1)).guests[0]

    assert guest.ideal_ranges["heat"].low == 40
    assert "seasoning" not in guest.ideal_ranges


async def test_rule_violation_is_reported_not_discarded() -> None:
    """규칙을 어겼다고 버리지 않는다 (규약 5-3).

    재생성을 끄고 본다. 루프가 없어도 생성물은 Issue를 달고 살아남아야 한다.
    """
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(ideal_heat={"low": 90, "high": 130}))])

    result = await generate_guests(llm=llm, bible=bible, count=1, max_regenerations=0)

    assert len(result.guests) == 1, "생성물을 버렸다"
    assert has_errors(result.issues)
    assert result.issues[0].field == "items[0].ideal_ranges.heat"
    assert result.call_count == 1


async def test_duplicate_id_within_the_batch_is_an_error() -> None:
    """한 명씩 보면 잡히지 않는다. 계약은 ID가 유일하기를 요구한다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item())])

    result = await generate_guests(llm=llm, bible=bible, count=2, max_regenerations=0)

    assert has_errors(result.issues)
    assert [issue.field for issue in result.issues] == [
        "items[0].guest_id",
        "items[1].guest_id",
    ]


async def test_issue_field_points_at_the_position_not_the_id() -> None:
    """guest_id 자체가 잘못됐을 수 있으므로 자리로 가리킨다."""
    bible = _bible()
    llm = FakeLlm([_response(bible, _item(), _item(guest_id="bad-id"))])

    result = await generate_guests(llm=llm, bible=bible, count=2, max_regenerations=0)

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
    assert result.call_count == 1, "고칠 게 없는데 다시 불렀다"


# ---------------------------------------------------------------- 재생성 루프


def _bad_item(**overrides: Any) -> dict[str, Any]:
    """이상 구간이 슬라이더 밖이라 ERROR가 나는 손님."""
    return _item(ideal_heat={"low": 90, "high": 130}, **overrides)


async def test_only_the_offender_is_regenerated() -> None:
    """배치 전체가 아니라 어긴 사람만 다시 만든다.

    8명 중 1명이 틀렸을 때 8명을 다시 뽑으면 멀쩡한 7명을 버리고 돈을 8배로 쓴다.
    """
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(), _bad_item(guest_id="guest_test_02")),
            _response(bible, _item(guest_id="guest_test_03")),
        ]
    )

    result = await generate_guests(llm=llm, bible=bible, count=2)

    assert result.call_count == 2
    assert not has_errors(result.issues)
    ids = [guest.guest_id for guest in result.guests]
    assert "guest_test_01" in ids, "통과한 손님을 버렸다"
    assert "guest_test_03" in ids


async def test_regeneration_asks_only_for_the_missing_count() -> None:
    """다시 뽑는 인원은 걸린 사람 수만큼이다."""
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(), _bad_item(guest_id="guest_test_02")),
            _response(bible, _item(guest_id="guest_test_03")),
        ]
    )

    await generate_guests(llm=llm, bible=bible, count=2)

    assert "손님 1명" in llm.calls[1].context


async def test_feedback_reaches_the_model() -> None:
    """무엇이 틀렸는지 전달하지 않으면 재생성은 그냥 다시 굴리는 주사위다.

    Issue.message를 처음부터 모델이 읽는다고 생각하고 쓴 것이 여기서 값을 한다.
    """
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _bad_item()),
            _response(bible, _item(guest_id="guest_test_02")),
        ]
    )

    await generate_guests(llm=llm, bible=bible, count=1)

    retry_context = llm.calls[1].context
    assert "지난번" in retry_context
    assert "슬라이더 범위" in retry_context, "무엇이 틀렸는지 싣지 않았다"


async def test_kept_guests_are_listed_so_replacements_do_not_collide() -> None:
    """어긴 사람만 다시 뽑으면 새로 만들어지는 쪽은 남은 사람들을 모른다.

    그대로 두면 한 명을 고치려다 중복을 새로 만든다.
    """
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _item(name="살아남은손님"), _bad_item(guest_id="guest_test_02")),
            _response(bible, _item(guest_id="guest_test_03")),
        ]
    )

    await generate_guests(llm=llm, bible=bible, count=2)

    assert "살아남은손님" in llm.calls[1].context


async def test_exhausted_loop_still_returns_everything() -> None:
    """3회를 소진해도 버리지 않는다 (규약 5-3).

    여기서 예외를 올리면 같은 배치의 멀쩡한 손님까지 잃는다.
    """
    bible = _bible()
    llm = FakeLlm([_response(bible, _bad_item()) for _ in range(4)])

    result = await generate_guests(llm=llm, bible=bible, count=1, max_regenerations=3)

    assert result.call_count == 4, "첫 호출 1회 + 재생성 3회"
    assert len(result.guests) == 1, "고쳐지지 않았다고 버렸다"
    assert has_errors(result.issues), "못 고친 것을 조용히 통과시켰다"


async def test_loop_stops_as_soon_as_it_passes() -> None:
    """고쳐졌는데도 남은 횟수를 쓰면 그대로 돈이다."""
    bible = _bible()
    llm = FakeLlm(
        [
            _response(bible, _bad_item()),
            _response(bible, _item(guest_id="guest_test_02")),
        ]
    )

    result = await generate_guests(llm=llm, bible=bible, count=1, max_regenerations=3)

    assert result.call_count == 2


async def test_negative_max_regenerations_is_rejected() -> None:
    bible = _bible()
    llm = FakeLlm([])

    with pytest.raises(DomainError, match="0 이상"):
        await generate_guests(llm=llm, bible=bible, count=1, max_regenerations=-1)

    assert llm.calls == []


async def test_non_positive_count_never_calls_the_model() -> None:
    """돈을 쓰기 전에 막는다."""
    bible = _bible()
    llm = FakeLlm([])

    with pytest.raises(DomainError):
        await generate_guests(llm=llm, bible=bible, count=0)

    assert llm.calls == []
