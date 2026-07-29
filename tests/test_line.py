"""대사 스키마와 검증을 고정한다.

핵심은 하나다 — **`subject`가 상황이 선언한 어휘에서 왔는가.** 여기가 뚫리면
런타임이 대사를 못 고르거나 엉뚱한 자리에서 고른다.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import has_errors
from daily_special.domain.line import (
    DialogueLine,
    check_line,
    check_line_batch,
    check_line_coverage,
)
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"
LINES_PATH = REPO_ROOT / "out" / "packages" / SCHEMA_VERSION / "lines.json"


def _load() -> Package[DialogueLine]:
    return Package[DialogueLine].model_validate(json.loads(LINES_PATH.read_text(encoding="utf-8")))


def _test_bible() -> ProjectBible:
    def named(key: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key}

    def situation(key: str, subject: str) -> dict[str, Any]:
        return {"key": key, "label": key, "description": key, "subject": subject}

    return ProjectBible.model_validate(
        {
            "version": "test.1",
            "needs": [named("filling"), named("mild")],
            "axes": [
                {
                    "key": "heat",
                    "label": "heat",
                    "description": "heat",
                    "slider_min": 0,
                    "slider_max": 100,
                }
            ],
            "dietary_constraints": [named("no_meat")],
            "voices": [named("gruff"), named("polite")],
            "situations": [
                situation("greet", "none"),
                situation("order", "need"),
                situation("feedback_high", "axis"),
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


def _line(**overrides: Any) -> DialogueLine:
    data: dict[str, Any] = {
        "line_id": "line_greet_gruff_01",
        "situation": "greet",
        "subject": None,
        "voice": "gruff",
        "text": "왔다.",
    }
    data.update(overrides)
    return DialogueLine.model_validate(data)


# ---------------------------------------------------------------- 목 파일


def test_mock_package_parses() -> None:
    package = _load()
    assert package.kind is PackageKind.LINES
    assert package.schema_version == SCHEMA_VERSION


def test_mock_package_declares_the_current_bible() -> None:
    assert _load().bible_version == load_bible(BIBLE_PATH).version


def test_mock_lines_pass_validation() -> None:
    issues = check_line_batch(_load().items, load_bible(BIBLE_PATH))
    assert not issues, [issue.message for issue in issues]


def test_mock_covers_one_slot_completely() -> None:
    """목 데이터는 한 자리(인사)를 말투 전부로 채운 표본이다.

    한 자리가 어떻게 생겼는지 클라이언트가 보려면 그 자리는 완전해야 한다.
    """
    bible = load_bible(BIBLE_PATH)
    greet = [line for line in _load().items if line.situation == "greet"]
    assert {line.voice for line in greet} == {voice.key for voice in bible.voices}


# ---------------------------------------------------------------- 대상 검증


def test_valid_line_has_no_issues() -> None:
    assert check_line(_line(), _test_bible()) == []


def test_need_subject_is_accepted_for_order() -> None:
    line = _line(line_id="line_order_gruff_01", situation="order", subject="filling")
    assert check_line(line, _test_bible()) == []


def test_axis_subject_is_accepted_for_feedback() -> None:
    line = _line(line_id="line_fb_gruff_01", situation="feedback_high", subject="heat")
    assert check_line(line, _test_bible()) == []


def test_subject_from_the_wrong_vocabulary_is_an_error() -> None:
    """주문 상황에 파라미터 축이 대상으로 들어오는 경우.

    상황과 대상을 따로 보면 이것을 잡을 수 없다. 둘 다 각자의 어휘 안에 있기 때문이다.
    """
    line = _line(line_id="line_order_gruff_01", situation="order", subject="heat")
    issues = check_line(line, _test_bible())

    assert has_errors(issues)
    assert issues[0].field == "subject"


def test_subject_on_a_subjectless_situation_is_an_error() -> None:
    issues = check_line(_line(subject="filling"), _test_bible())
    assert has_errors(issues)
    assert "비워 둔다" in issues[0].message


def test_missing_subject_where_required_is_an_error() -> None:
    line = _line(line_id="line_order_gruff_01", situation="order", subject=None)
    issues = check_line(line, _test_bible())
    assert has_errors(issues)
    assert issues[0].field == "subject"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"line_id": "greet_gruff_01"}, "line_id"),
        ({"voice": "sarcastic"}, "voice"),
        ({"situation": "nonexistent"}, "situation"),
        ({"text": "  "}, "text"),
        ({"text": "가" * 60}, "text"),
    ],
)
def test_violation_is_an_error(overrides: dict[str, Any], field: str) -> None:
    issues = check_line(_line(**overrides), _test_bible())
    assert has_errors(issues)
    assert issues[0].field == field


# ---------------------------------------------------------------- 배치


def test_duplicate_text_is_an_error() -> None:
    """한 자리에 여러 줄을 두는 이유가 반복감을 없애는 것인데, 같은 문장이면 그 목적이 사라진다."""
    same = [_line(), _line(line_id="line_greet_polite_01", voice="polite")]
    issues = check_line_batch(same, _test_bible())

    assert has_errors(issues)
    assert any("같은 대사가" in issue.message for issue in issues)


def test_duplicate_id_is_an_error() -> None:
    issues = check_line_batch([_line(), _line(text="다른 말")], _test_bible())
    assert any(issue.field.endswith("line_id") for issue in issues)


# ---------------------------------------------------------------- 커버리지


def test_coverage_reports_every_empty_slot() -> None:
    """빈 자리는 런타임에 손님이 입을 다무는 것으로 나타난다. 테스트로는 안 잡힌다."""
    bible = _test_bible()
    issues = check_line_coverage([_line()], bible)

    assert has_errors(issues)
    # greet 2 + order 2×2 + feedback_high 1×2 = 8자리, 그중 하나만 찼다
    assert "7개" in issues[0].message


def test_full_coverage_has_no_issues() -> None:
    bible = _test_bible()
    lines: list[DialogueLine] = []
    index = 0

    for situation in bible.situations:
        subjects: list[str | None]
        if situation.subject.value == "need":
            subjects = [need.key for need in bible.needs]
        elif situation.subject.value == "axis":
            subjects = [axis.key for axis in bible.axes]
        else:
            subjects = [None]

        for subject in subjects:
            for voice in bible.voices:
                index += 1
                lines.append(
                    _line(
                        line_id=f"line_x_{index:03d}",
                        situation=situation.key,
                        subject=subject,
                        voice=voice.key,
                        text=f"대사 {index}",
                    )
                )

    assert check_line_coverage(lines, bible) == []
