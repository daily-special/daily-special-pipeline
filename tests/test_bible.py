"""ProjectBible의 불변식을 고정한다.

설정 스키마는 평소보다 강하게 조인다 — 생성물 층을 느슨하게 둔 대가로 검증 책임이
여기로 넘어와 있기 때문이다. 여기가 뚫리면 파이프라인 전 구간이 조용히 틀린 채 통과한다.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.common.errors import ConfigError
from daily_special.domain.bible import ProjectBible

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "data" / "project_bible.json"


def _minimal_data() -> dict[str, Any]:
    return {
        "version": "test.1",
        "needs": [{"key": "filling", "label": "포만", "description": "배를 채우고 싶다"}],
        "axes": [
            {
                "key": "heat",
                "label": "불 세기",
                "description": "약불에서 센불까지",
                "slider_min": 0,
                "slider_max": 100,
            }
        ],
        "dietary_constraints": [],
    }


# ---------------------------------------------------------------- 실제 설정 데이터


def test_real_bible_loads() -> None:
    """data/project_bible.json이 스키마와 어긋나지 않는다.

    데이터와 스키마가 따로 노는 것은 가장 흔하고 조용한 고장이다. 여기서 잡는다.
    """
    bible = load_bible(BIBLE_PATH)
    assert bible.version
    assert len(bible.needs) == 6
    assert len(bible.axes) == 3


def test_real_bible_axes_are_the_three_design_axes() -> None:
    """설계서의 파라미터 3축(불 세기·조리 시간·간)이 그대로 있다."""
    bible = load_bible(BIBLE_PATH)
    assert [axis.key for axis in bible.axes] == ["heat", "cook_time", "seasoning"]


def test_real_bible_records_its_assumptions() -> None:
    """가정값을 썼다면 unconfirmed에 남아 있어야 한다.

    가정값을 코드나 프롬프트에 직접 박으면 확정 시점에 찾을 수 없다.
    """
    bible = load_bible(BIBLE_PATH)
    assert bible.unconfirmed, "가정값을 쓰면서 근거를 남기지 않았다"
    assert all(item.why.strip() for item in bible.unconfirmed)


# ---------------------------------------------------------------- 불변식


def test_duplicate_key_is_rejected() -> None:
    """중복을 허용하면 뒤에 온 것이 조용히 이기고 추적할 수 없게 된다."""
    data = _minimal_data()
    data["needs"] = [
        {"key": "filling", "label": "포만", "description": "배를 채우고 싶다"},
        {"key": "filling", "label": "포만2", "description": "중복된 키"},
    ]
    with pytest.raises(ConfigError, match="중복된 키"):
        ProjectBible.model_validate(data)


def test_non_slug_key_is_rejected() -> None:
    """키 표기는 출력 JSON의 열거 값이 그대로 쓴다. 계약이라 흔들리면 안 된다."""
    data = _minimal_data()
    data["needs"] = [{"key": "Filling", "label": "포만", "description": "대문자는 안 된다"}]
    with pytest.raises(ConfigError, match="슬러그 표기"):
        ProjectBible.model_validate(data)


def test_inverted_slider_range_is_rejected() -> None:
    data = _minimal_data()
    data["axes"][0]["slider_min"] = 100
    data["axes"][0]["slider_max"] = 0
    with pytest.raises(ConfigError, match="뒤집혔다"):
        ProjectBible.model_validate(data)


def test_empty_needs_is_rejected() -> None:
    data = _minimal_data()
    data["needs"] = []
    with pytest.raises(ConfigError, match="needs가 비어 있다"):
        ProjectBible.model_validate(data)


def test_empty_axes_is_rejected() -> None:
    data = _minimal_data()
    data["axes"] = []
    with pytest.raises(ConfigError, match="axes가 비어 있다"):
        ProjectBible.model_validate(data)


# ---------------------------------------------------------------- 조회


def test_find_returns_none_for_unknown_key() -> None:
    """어휘 밖의 값은 설정 오류가 아니라 생성물의 문제다. 예외가 아니라 None으로 돌려준다."""
    bible = ProjectBible.model_validate(_minimal_data())
    assert bible.find_need("filling") is not None
    assert bible.find_need("nonexistent") is None
    assert bible.find_axis("nonexistent") is None
    assert bible.find_dietary("nonexistent") is None


# ---------------------------------------------------------------- 로더


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="읽을 수 없다"):
        load_bible(tmp_path / "없는파일.json")


def test_broken_json_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{ 이건 JSON이 아니다", encoding="utf-8")
    with pytest.raises(ConfigError, match="올바른 JSON이 아니다"):
        load_bible(path)


def test_schema_mismatch_raises_config_error(tmp_path: Path) -> None:
    """파이단틱의 ValidationError도 ConfigError로 감싼다. 부르는 쪽은 하나만 알면 된다."""
    path = tmp_path / "wrong_shape.json"
    data = _minimal_data()
    data["axes"][0]["slider_min"] = "숫자가 아니다"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="모양이 스키마와 다르다"):
        load_bible(path)
