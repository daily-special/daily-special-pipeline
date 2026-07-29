"""페르소나 스키마를 고정한다.

여기서 지키는 것은 둘이다.

1. **손으로 쓴 목 파일이 계약대로 읽힌다.** 클라이언트가 이 파일로 개발을 시작하므로,
   파일이 깨지면 파이프라인이 아니라 클라가 먼저 멈춘다.
2. **계약 데이터가 만족도 엔진에 그대로 먹힌다.** 스키마가 엔진에서 역산돼 나왔다는
   주장은 실제로 왕복시켜 봐야 증명된다.
"""

import json
from pathlib import Path

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.issue import Severity, has_errors
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind
from daily_special.domain.satisfaction import IdealRange, ServedDish, VisitState, evaluate

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"
GUESTS_PATH = REPO_ROOT / "out" / "packages" / SCHEMA_VERSION / "guests.json"


def _load_guests() -> Package[Guest]:
    raw = json.loads(GUESTS_PATH.read_text(encoding="utf-8"))
    return Package[Guest].model_validate(raw)


def _test_bible() -> ProjectBible:
    """어휘만 갖춘 작은 설정. 실제 파일의 밸런스 조정에 흔들리지 않게 한다."""
    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [
                {"key": "filling", "label": "포만", "description": "배를 채운다"},
                {"key": "mild", "label": "순한", "description": "부담이 없다"},
            ],
            "axes": [
                {
                    "key": "heat",
                    "label": "불 세기",
                    "description": "약불~센불",
                    "slider_min": 0,
                    "slider_max": 100,
                },
                {
                    "key": "seasoning",
                    "label": "간",
                    "description": "심심함~짭짤함",
                    "slider_min": 0,
                    "slider_max": 100,
                },
            ],
            "dietary_constraints": [
                {"key": "no_meat", "label": "육류 불가", "description": "고기를 안 먹는다"}
            ],
            "voices": [{"key": "gruff", "label": "무뚝뚝", "description": "말이 짧다"}],
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
            },
            "scoring": {
                "need_floor": 0.15,
                "axis_tolerance": 25,
                "budget_overrun_ratio": 1.5,
                "dietary_violation_factor": 0.1,
            },
        }
    )


def _guest(**overrides: object) -> Guest:
    data: dict[str, object] = {
        "guest_id": "guest_test_01",
        "name": "테스트",
        "title": "시험용 손님",
        "bio": "테스트에만 나온다.",
        "voice": "gruff",
        "personality": "말이 없다.",
        "preferred_needs": ["filling"],
        "ideal_ranges": {"heat": {"low": 40, "high": 60}},
        "dietary": [],
    }
    data.update(overrides)
    return Guest.model_validate(data)


# ---------------------------------------------------------------- 목 파일


def test_mock_package_parses() -> None:
    """4단계의 완료 조건 — guests.json 한 장을 손으로 쓸 수 있다."""
    package = _load_guests()
    assert package.kind is PackageKind.GUESTS
    assert package.schema_version == SCHEMA_VERSION
    assert len(package.items) == 4


def test_mock_package_declares_the_current_bible() -> None:
    """bible_version이 실제 설정과 같아야 한다.

    다르면 그 필드가 거짓말을 한다 — 클라는 그 밸런스로 검증됐다고 믿고 읽는다.
    설정 버전을 올릴 때 이 테스트가 깨지는 것은 의도된 것이다. 목 파일을 다시 보라는 뜻이다.
    """
    bible = load_bible(BIBLE_PATH)
    assert _load_guests().bible_version == bible.version


def test_mock_guests_pass_validation() -> None:
    """실제 ProjectBible 어휘로 검증한다. 어휘가 바뀌면 여기서 드러난다."""
    bible = load_bible(BIBLE_PATH)
    for guest in _load_guests().items:
        issues = check_guest(guest, bible)
        assert not issues, f"{guest.guest_id}: {[issue.message for issue in issues]}"


def test_mock_guests_have_unique_ids() -> None:
    """ID는 서버 DB와 에셋 파일명이 무는 값이다. 겹치면 조용히 덮어쓴다."""
    ids = [guest.guest_id for guest in _load_guests().items]
    assert len(ids) == len(set(ids))


def test_mock_guests_cover_the_engine_branches() -> None:
    """목 데이터는 예쁜 예시가 아니라 엔진의 갈림길을 밟는 표본이어야 한다.

    클라이언트가 UI를 만들 때 극단값을 미리 봐야 한다 — 취향 축이 하나뿐인 손님과
    제약이 둘인 손님은 화면에서 다르게 생겼다.
    """
    guests = _load_guests().items
    assert any(len(guest.ideal_ranges) == 1 for guest in guests), "취향 축이 하나뿐인 손님이 없다"
    assert any(len(guest.dietary) >= 2 for guest in guests), "식이 제약이 둘인 손님이 없다"
    assert any(not guest.dietary for guest in guests), "제약이 없는 손님이 없다"


# ---------------------------------------------------------------- 엔진과의 왕복


def test_persona_from_contract_feeds_the_engine() -> None:
    """계약 → to_persona() → evaluate()가 실제로 돈다.

    이 왕복이 4단계의 증명이다. 스키마가 엔진에서 역산됐다는 말은 여기서만 참이 된다.
    """
    bible = load_bible(BIBLE_PATH)
    guest = next(g for g in _load_guests().items if g.guest_id == "guest_ashen_scout_01")
    persona = guest.to_persona()

    dish = ServedDish(
        need_tags=["filling", "restorative"],
        price=10,
        dietary_conflicts=[],
        params={key: (ideal.low + ideal.high) // 2 for key, ideal in guest.ideal_ranges.items()},
    )
    result = evaluate(
        persona=persona,
        state=VisitState(needs=list(guest.preferred_needs), wallet=20),
        dish=dish,
        bible=bible,
    )

    assert result.total == pytest.approx(1.0)


def test_to_persona_carries_only_what_the_engine_reads() -> None:
    """엔진 입력은 좁아야 한다. 넓히면 이식하는 쪽이 무엇이 계산에 쓰이는지 알 수 없다."""
    persona = _guest().to_persona()
    assert set(type(persona).model_fields) == {"ideal_ranges", "dietary"}
    assert persona.ideal_ranges == {"heat": IdealRange(low=40, high=60)}


def test_runtime_state_is_not_in_the_contract() -> None:
    """플레이 중에 바뀌는 것은 계약이 아니라 서버가 소유한다 (데이터 계약 5절).

    지갑·허기·기분이 페르소나에 섞이면 매 방문 달라지는 값이 발행 후 불변인 파일에 박힌다.
    """
    fields = set(Guest.model_fields)
    assert not fields & {"wallet", "hunger", "mood", "condition", "needs", "relationship"}


# ---------------------------------------------------------------- 검증


def test_valid_guest_has_no_issues() -> None:
    assert check_guest(_guest(), _test_bible()) == []


def test_axis_without_preference_is_allowed() -> None:
    """취향이 없는 축은 넣지 않는다. 빠진 것이 아니라 '어떤 값이든 만족'이다."""
    guest = _guest(ideal_ranges={"heat": {"low": 40, "high": 60}})
    assert check_guest(guest, _test_bible()) == []


def test_guest_with_no_preference_at_all_is_an_error() -> None:
    """축 하나를 비우는 것과 전부 비우는 것은 다르다.

    취향이 하나도 없으면 플레이어가 추측할 것이 없다. 취향 추론이 이 게임의 핵심
    루프라 그런 손님은 문법적으로 완벽해도 손님 구실을 못 한다.
    """
    issues = check_guest(_guest(ideal_ranges={}), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "ideal_ranges"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"guest_id": "ashen_scout_01"}, "guest_id"),
        ({"guest_id": "guest_Ashen_Scout"}, "guest_id"),
        ({"voice": "sarcastic"}, "voice"),
        ({"preferred_needs": ["nonexistent"]}, "preferred_needs"),
        ({"dietary": ["no_gluten"]}, "dietary"),
        ({"ideal_ranges": {"sweetness": {"low": 10, "high": 20}}}, "ideal_ranges.sweetness"),
    ],
)
def test_vocabulary_violation_is_an_error(overrides: dict[str, object], field: str) -> None:
    """어휘 밖의 값은 ERROR다. 설정 오류가 아니라 생성물의 문제이므로 예외가 아니다."""
    issues = check_guest(_guest(**overrides), _test_bible())
    assert has_errors(issues)
    assert [issue.field for issue in issues] == [field]


def test_ideal_range_outside_the_slider_is_an_error() -> None:
    """슬라이더가 낼 수 없는 구간은 플레이어가 영원히 맞출 수 없다.

    엔진은 이것을 "구간에서 멀다"로만 읽어 조용히 낮은 점수를 낸다. 여기서 잡지 않으면
    손님 하나가 영영 만족하지 않는 이유를 아무도 모른다.
    """
    issues = check_guest(_guest(ideal_ranges={"heat": {"low": 90, "high": 120}}), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "ideal_ranges.heat"


def test_ideal_range_wider_than_the_limit_is_an_error() -> None:
    """무엇을 내도 맞는 구간은 취향이 아니다.

    어휘도 슬라이더도 통과했는데 플레이할 수 없는 손님이 여기서 걸린다.
    """
    issues = check_guest(_guest(ideal_ranges={"heat": {"low": 0, "high": 90}}), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "ideal_ranges.heat"
    assert "취향" in issues[0].message


def test_too_many_preferred_needs_is_only_a_warning() -> None:
    """쓸 수는 있다. ERROR로 두면 밋밋하다는 이유로 돈을 들여 다시 만들게 된다."""
    issues = check_guest(_guest(preferred_needs=["filling", "mild", "filling"]), _test_bible())
    assert not has_errors(issues)
    assert issues[0].severity is Severity.WARNING


def test_no_preferred_needs_is_an_error() -> None:
    """서버가 오늘의 욕구를 뽑을 근거가 없다."""
    issues = check_guest(_guest(preferred_needs=[]), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "preferred_needs"


def test_empty_text_is_an_error() -> None:
    """화면에 띄울 것도, 대사를 만들 재료도 없다."""
    issues = check_guest(_guest(bio="   "), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "bio"


def test_issues_are_collected_not_raised_on_first() -> None:
    """첫 위반에서 멈추면 재생성 피드백에 한 줄밖에 싣지 못한다."""
    issues = check_guest(
        _guest(
            guest_id="bad-id",
            voice="sarcastic",
            preferred_needs=["nonexistent"],
            dietary=["no_gluten"],
        ),
        _test_bible(),
    )
    assert len(issues) == 4
    assert all(issue.severity is Severity.ERROR for issue in issues)
    assert {issue.field for issue in issues} == {
        "guest_id",
        "voice",
        "preferred_needs",
        "dietary",
    }


def test_error_message_lists_the_allowed_vocabulary() -> None:
    """메시지는 재생성 프롬프트에 그대로 실린다. 무엇이 틀렸는지만 말하면 모델이 또 틀린다."""
    issues = check_guest(_guest(voice="sarcastic"), _test_bible())
    assert "gruff" in issues[0].message


def test_inverted_ideal_range_is_rejected_by_the_schema() -> None:
    """구조적으로 불가능한 값은 검증 층까지 가지 않고 스키마가 막는다."""
    with pytest.raises(ValueError, match="뒤집혔다"):
        _guest(ideal_ranges={"heat": {"low": 60, "high": 40}})


def test_guest_is_frozen() -> None:
    """발행 후 불변이다. 계약 항목이 흐르는 중에 바뀌면 provenance가 거짓이 된다."""
    guest = _guest()
    with pytest.raises(ValueError):
        guest.name = "다른 이름"
