"""허용 문자 집합을 고정한다.

**이 검사가 없던 시절에 실제로 물렸다.** 생성된 대사에 말줄임표(U+2026)가 11번 들어갔는데
클라이언트 폰트 아틀라스가 `20-7E,AC00-D7A3`이라 지친 말투가 통째로 네모로 뜰 뻔했다.
스키마도 어휘도 길이도 전부 통과했고, **폰에 올려야만 보이는 버그**였다.

여기서 고정하는 것은 둘이다 — 집합 밖 문자가 ERROR로 잡히는가, 그리고 **설정이 만들어 내는
유니티 범위 문자열이 클라이언트가 받아 적은 값과 같은가.**
"""

import json
from pathlib import Path

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.common.errors import ConfigError
from daily_special.domain.bible import CharRange, ProjectBible, TextCharsetSpec
from daily_special.domain.charset import check_charset
from daily_special.domain.dish import Dish, check_dish
from daily_special.domain.guest import Guest, check_guest
from daily_special.domain.ingredient import Ingredient, check_ingredient
from daily_special.domain.issue import Severity, has_errors
from daily_special.domain.line import DialogueLine, check_line
from daily_special.domain.package import Package

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "out" / "packages" / "1.0.0"

# 클라이언트가 폰트 아틀라스를 구울 때 받아 적은 값. 여기가 갈리면 폰에서 글자가 깨진다.
CLIENT_ATLAS_RANGE = "20-7E,A1-FF,2013-2014,2018-201D,2026,AC00-D7A3"


@pytest.fixture(scope="module")
def bible() -> ProjectBible:
    return load_bible()


def _load[T](name: str, item_type: type[T]) -> list[T]:
    raw = json.loads((PACKAGE_DIR / name).read_text(encoding="utf-8"))
    return list(Package[item_type].model_validate(raw).items)  # type: ignore[valid-type]


# --- 설정이 만들어 내는 값 -------------------------------------------------


def test_unity_range_matches_what_the_client_baked(bible: ProjectBible) -> None:
    """클라이언트는 이 값을 계산하지 않고 받아 적는다.

    두 저장소가 각자 적으면 반드시 어긋나고, 어긋난 것을 알아채는 방법이 폰뿐이 된다.
    """
    assert bible.text_charset.unity_range() == CLIENT_ATLAS_RANGE


def test_single_code_point_range_is_rendered_without_a_dash() -> None:
    charset = TextCharsetSpec(ranges=[CharRange(start=0x2026, end=0x2026, label="말줄임표")])

    assert charset.unity_range() == "2026"


# --- 무엇이 들어오고 무엇이 걸리는가 ---------------------------------------


@pytest.mark.parametrize("char", ["가", "힣", "A", "7", ".", "…", "—", "·"])
def test_allowed_characters(bible: ProjectBible, char: str) -> None:
    assert bible.text_charset.contains(char)


@pytest.mark.parametrize("char", ["★", "🍲", "漢", "→", "​"])
def test_rejected_characters(bible: ProjectBible, char: str) -> None:
    assert not bible.text_charset.contains(char)


def test_ellipsis_is_allowed_now(bible: ProjectBible) -> None:
    """이 검사를 만들게 한 문자다.

    금지하고 재생성하는 대신 **아틀라스를 넓히기로** 했다. 재생성은 돈이 들고 지친 말투의
    어조가 나빠질 수 있는데, 아틀라스를 넓히는 것은 글리프 하나를 더 굽는 일이다.
    """
    assert not check_charset("앉으니 살 것 같네…", "text", bible)


def test_violations_are_deduplicated_in_first_seen_order(bible: ProjectBible) -> None:
    assert bible.text_charset.violations("★그릇★에 漢") == ["★", "漢"]


# --- Issue 모양 -------------------------------------------------------------


def test_clean_text_produces_no_issue(bible: ProjectBible) -> None:
    assert check_charset("따끈한 국밥 한 그릇, 8원.", "description", bible) == []


def test_offending_text_is_an_error_on_the_named_field(bible: ProjectBible) -> None:
    issues = check_charset("국밥 🍲", "description", bible)

    assert len(issues) == 1
    assert issues[0].severity is Severity.ERROR
    assert issues[0].field == "description"


def test_message_names_the_character_and_its_code_point(bible: ProjectBible) -> None:
    """메시지는 재생성 프롬프트에 그대로 실린다. 모델이 무엇을 고칠지 알아야 한다."""
    message = check_charset("국밥 🍲", "description", bible)[0].message

    assert "🍲" in message
    assert "U+1F372" in message


# --- 설정 불변식 ------------------------------------------------------------


def test_empty_charset_is_rejected() -> None:
    with pytest.raises(ConfigError, match="비어 있다"):
        TextCharsetSpec(ranges=[])


def test_inverted_range_is_rejected() -> None:
    with pytest.raises(ConfigError, match="뒤집혔다"):
        TextCharsetSpec(ranges=[CharRange(start=0x7E, end=0x20, label="뒤집힘")])


def test_unsorted_or_overlapping_ranges_are_rejected() -> None:
    """정렬·비겹침을 요구하는 이유는 unity_range()를 결정적으로 만들기 위해서다."""
    with pytest.raises(ConfigError, match="겹치거나"):
        TextCharsetSpec(
            ranges=[
                CharRange(start=0xAC00, end=0xD7A3, label="한글"),
                CharRange(start=0x20, end=0x7E, label="ASCII"),
            ]
        )


# --- 네 계약 항목에 전부 걸려 있는가 ----------------------------------------


def test_guest_text_is_checked(bible: ProjectBible) -> None:
    guest = _load("guests.json", Guest)[0]

    broken = guest.model_copy(update={"bio": guest.bio + " ★"})

    assert has_errors(check_guest(broken, bible))


def test_ingredient_text_is_checked(bible: ProjectBible) -> None:
    ingredient = _load("ingredients.json", Ingredient)[0]

    broken = ingredient.model_copy(update={"description": ingredient.description + " ★"})

    assert has_errors(check_ingredient(broken, bible))


def test_dish_text_is_checked(bible: ProjectBible) -> None:
    dishes = _load("dishes.json", Dish)
    ingredients = {item.ingredient_id: item for item in _load("ingredients.json", Ingredient)}

    broken = dishes[0].model_copy(update={"description": dishes[0].description + " ★"})

    assert has_errors(check_dish(broken, bible, ingredients))


def test_line_text_is_checked(bible: ProjectBible) -> None:
    line = _load("lines.json", DialogueLine)[0]

    broken = line.model_copy(update={"text": line.text + " ★"})

    assert has_errors(check_line(broken, bible))


# --- 실제 산출물 ------------------------------------------------------------


def test_every_generated_string_is_renderable(bible: ProjectBible) -> None:
    """지금 커밋된 생성분 전체가 클라이언트 폰트로 그려지는가.

    합성 예제만 보면 실제 산출물이 깨져도 초록이 뜬다. 이 테스트가 빨개지면 **콘텐츠를
    다시 뽑았는데 아틀라스를 안 넓힌 것**이다.
    """
    offenders: dict[str, list[str]] = {}

    for name in ("guests.json", "ingredients.json", "dishes.json", "lines.json"):
        raw = json.loads((PACKAGE_DIR / name).read_text(encoding="utf-8"))
        for text in _strings(raw["items"]):
            for char in bible.text_charset.violations(text):
                offenders.setdefault(char, []).append(name)

    assert not offenders, f"폰트에 없는 문자가 산출물에 있다: {sorted(offenders)}"


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    if isinstance(node, list):
        return [text for value in node for text in _strings(value)]
    return []
