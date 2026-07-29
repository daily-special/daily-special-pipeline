"""동적 스키마 생성을 고정한다.

이 모듈의 존재 이유는 strict 모드가 임의 키 dict를 금지한다는 것 하나다. 그래서
가장 중요한 테스트는 **실제로 strict 변환을 통과하는가**이고, 그것을 네트워크 없이 본다.
"""

from typing import Any

import pytest

# OpenAI SDK의 내부 함수를 일부러 쓴다. 실호출 없이 "이 스키마가 strict 모드에서
# 받아들여지는가"를 검사할 다른 방법이 없고, 이 검사가 없으면 스키마가 깨진 것을
# 돈을 쓰는 시점에야 알게 된다.
from openai.lib._pydantic import to_strict_json_schema

from daily_special.application.schema_builder import build_guest_batch_schema
from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest
from daily_special.domain.satisfaction import IdealRange


def _bible_data(**overrides: Any) -> dict[str, Any]:
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

    data: dict[str, Any] = {
        "version": "test.1",
        "needs": [named("filling"), named("mild")],
        "axes": [axis("heat"), axis("seasoning")],
        "dietary_constraints": [named("no_meat")],
        "voices": [named("gruff"), named("polite")],
        "scoring": {
            "need_floor": 0.15,
            "axis_tolerance": 25,
            "budget_overrun_ratio": 1.5,
            "dietary_violation_factor": 0.1,
        },
    }
    data.update(overrides)
    return data


def _bible(**overrides: Any) -> ProjectBible:
    return ProjectBible.model_validate(_bible_data(**overrides))


def _generated(schema_model: type[Any], **overrides: Any) -> Any:
    """모델이 뱉었다고 치는 손님 하나를 만든다."""
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
    return schema_model.model_validate({"guests": [item]})


# ---------------------------------------------------------------- strict 적합성


def test_schema_survives_strict_conversion() -> None:
    """이 검사가 없으면 스키마가 깨진 것을 돈을 쓰는 시점에야 알게 된다."""
    schema = to_strict_json_schema(build_guest_batch_schema(_bible()).model)
    guest = schema["$defs"]["GeneratedGuest"]

    assert guest["additionalProperties"] is False
    assert set(guest["required"]) == set(guest["properties"]), (
        "strict 모드는 모든 필드가 required이기를 요구한다"
    )


def test_vocabulary_becomes_an_enum() -> None:
    """어휘 밖의 값을 구조적으로 막는 것이 검증으로 잡아 재생성하는 것보다 항상 싸다."""
    schema = to_strict_json_schema(build_guest_batch_schema(_bible()).model)
    assert schema["$defs"]["VoiceKey"]["enum"] == ["gruff", "polite"]
    assert schema["$defs"]["NeedKey"]["enum"] == ["filling", "mild"]


def test_ideal_ranges_are_flattened_into_one_field_per_axis() -> None:
    """임의 키 dict를 strict 모드가 받지 않으므로 축마다 필드 하나로 편다."""
    schema = to_strict_json_schema(build_guest_batch_schema(_bible()).model)
    properties = schema["$defs"]["GeneratedGuest"]["properties"]

    assert "ideal_heat" in properties
    assert "ideal_seasoning" in properties
    assert "ideal_ranges" not in properties


def test_adding_an_axis_adds_a_field() -> None:
    """설정이 스키마를 결정한다.

    이 저장소에서 가장 값비싼 주장이다 — 어휘를 코드에 박았다면 축 하나를 더할 때
    스키마와 프롬프트에 각각 손을 대야 한다. 여기서 그 주장이 참인지 본다.
    """
    data = _bible_data()
    data["axes"].append(
        {
            "key": "sweetness",
            "label": "단맛",
            "description": "달지 않음~달콤함",
            "slider_min": 0,
            "slider_max": 100,
        }
    )
    bible = ProjectBible.model_validate(data)
    schema = to_strict_json_schema(build_guest_batch_schema(bible).model)

    assert "ideal_sweetness" in schema["$defs"]["GeneratedGuest"]["properties"]


def test_axis_description_carries_the_numbers_the_schema_cannot() -> None:
    """strict 모드는 수치 제약을 거부할 수 있다. 그래서 설명으로 알리고 check가 강제한다."""
    schema = to_strict_json_schema(build_guest_batch_schema(_bible()).model)
    description = schema["$defs"]["GeneratedGuest"]["properties"]["ideal_heat"]["description"]

    assert "0~100" in description, "슬라이더 범위를 알려주지 않았다"
    assert "25" in description, "허용 오차에서 유도한 구간 폭을 알려주지 않았다"


# ---------------------------------------------------------------- 되접기


def test_fold_back_produces_contract_guests() -> None:
    schema = build_guest_batch_schema(_bible())
    guests = schema.to_guests(_generated(schema.model))

    assert len(guests) == 1
    assert isinstance(guests[0], Guest)
    assert guests[0].guest_id == "guest_test_01"


def test_null_axis_becomes_a_missing_key() -> None:
    """계약에서 키가 없는 것이 "취향 없음"이다 (data-contract 7-2절).

    strict 모드는 모든 필드가 있기를 요구하고 계약은 없기를 요구하는데, null이 그 사이를 잇는다.
    """
    schema = build_guest_batch_schema(_bible())
    guest = schema.to_guests(_generated(schema.model))[0]

    assert guest.ideal_ranges == {"heat": IdealRange(low=40, high=60)}
    assert "seasoning" not in guest.ideal_ranges


def test_enum_values_are_folded_back_to_plain_strings() -> None:
    """계약 JSON에는 enum이 아니라 문자열이 실린다."""
    schema = build_guest_batch_schema(_bible())
    guest = schema.to_guests(
        _generated(schema.model, voice="polite", preferred_needs=["filling", "mild"])
    )[0]

    assert guest.voice == "polite"
    assert guest.preferred_needs == ["filling", "mild"]
    assert all(isinstance(need, str) for need in guest.preferred_needs)


def test_batch_folds_every_item() -> None:
    schema = build_guest_batch_schema(_bible())
    response = schema.model.model_validate(
        {
            "guests": [
                {
                    "guest_id": f"guest_test_{index:02d}",
                    "name": "테스트",
                    "title": "시험용",
                    "bio": "b",
                    "personality": "p",
                    "voice": "gruff",
                    "preferred_needs": ["filling"],
                    "dietary": [],
                    "ideal_heat": None,
                    "ideal_seasoning": None,
                }
                for index in range(3)
            ]
        }
    )

    assert [guest.guest_id for guest in schema.to_guests(response)] == [
        "guest_test_00",
        "guest_test_01",
        "guest_test_02",
    ]


def test_generated_value_outside_the_vocabulary_is_rejected_by_the_schema() -> None:
    """1층(스키마)에서 막힌다. 2층 검증까지 갈 일이 없다."""
    schema = build_guest_batch_schema(_bible())
    with pytest.raises(ValueError):
        _generated(schema.model, voice="sarcastic")


def test_same_bible_yields_the_same_schema_object() -> None:
    """이름만 같고 서로 다른 클래스가 쌓이면 재생성 루프가 매번 모델을 다시 짓는다."""
    assert build_guest_batch_schema(_bible()).model is build_guest_batch_schema(_bible()).model


def test_different_vocabulary_yields_a_different_schema() -> None:
    """버전 문자열이 같아도 어휘가 다르면 다른 스키마다. 같다고 보면 틀린 스키마가 나간다."""
    other = _bible(voices=[{"key": "haughty", "label": "도도", "description": "평가하듯"}])

    assert build_guest_batch_schema(_bible()).model is not build_guest_batch_schema(other).model


def test_bible_without_axes_cannot_build_a_schema() -> None:
    """설정 자체가 축이 없는 것을 막으므로 여기 오는 것은 호출 측의 잘못이다."""
    bible = _bible()
    with pytest.raises(DomainError, match="축이 없는"):
        build_guest_batch_schema(bible.model_copy(update={"axes": []}))
