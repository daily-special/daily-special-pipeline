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
            "max_preferred_needs": 1,
            "min_text_length": 1,
        },
        "scoring": {
            "need_floor": 0.15,
            "axis_tolerance": 25,
            "budget_overrun_ratio": 1.5,
            "dietary_violation_factor": 0.1,
        },
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
    assert len(bible.voices) == 5


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


def test_empty_voices_is_rejected() -> None:
    """말투가 하나도 없으면 어떤 손님도 유효한 voice를 가질 수 없다."""
    data = _minimal_data()
    data["voices"] = []
    with pytest.raises(ConfigError, match="voices가 비어 있다"):
        ProjectBible.model_validate(data)


def test_duplicate_voice_key_is_rejected() -> None:
    """말투 키는 대사 풀과의 조인 키다. 중복되면 어느 대사가 붙는지 알 수 없다."""
    data = _minimal_data()
    data["voices"] = [
        {"key": "gruff", "label": "무뚝뚝", "description": "말이 짧다"},
        {"key": "gruff", "label": "무뚝뚝2", "description": "중복된 키"},
    ]
    with pytest.raises(ConfigError, match="중복된 키"):
        ProjectBible.model_validate(data)


# ---------------------------------------------------------------- 채점 계수


def test_need_floor_of_one_is_rejected() -> None:
    """바닥값이 1이면 욕구를 빗나가도 만점이라 추론할 이유가 사라진다."""
    data = _minimal_data()
    data["scoring"]["need_floor"] = 1.0
    with pytest.raises(ConfigError, match="need_floor"):
        ProjectBible.model_validate(data)


def test_dietary_factor_of_one_is_rejected() -> None:
    """계수가 1이면 식이 위반에 아무 대가가 없다."""
    data = _minimal_data()
    data["scoring"]["dietary_violation_factor"] = 1.0
    with pytest.raises(ConfigError, match="dietary_violation_factor"):
        ProjectBible.model_validate(data)


def test_budget_overrun_ratio_of_one_is_rejected() -> None:
    """1 이하면 지갑을 넘는 순간 0이 되어, 완만하게 두기로 한 결정이 무효가 된다."""
    data = _minimal_data()
    data["scoring"]["budget_overrun_ratio"] = 1.0
    with pytest.raises(ConfigError, match="budget_overrun_ratio"):
        ProjectBible.model_validate(data)


def test_zero_axis_tolerance_is_rejected() -> None:
    data = _minimal_data()
    data["scoring"]["axis_tolerance"] = 0
    with pytest.raises(ConfigError, match="axis_tolerance"):
        ProjectBible.model_validate(data)


# ---------------------------------------------------------------- 합격선


def test_zero_min_preferred_axes_is_rejected() -> None:
    """0이면 취향 없는 손님이 통과한다. 추측할 것이 없으면 손님 구실을 못 한다."""
    data = _minimal_data()
    data["generation"]["min_preferred_axes"] = 0
    with pytest.raises(ConfigError, match="min_preferred_axes"):
        ProjectBible.model_validate(data)


def test_min_preferred_axes_above_axis_count_is_rejected() -> None:
    """축 개수를 넘으면 어떤 손님도 통과할 수 없다 — 루프가 영원히 재생성만 한다."""
    data = _minimal_data()
    data["generation"]["min_preferred_axes"] = 2
    with pytest.raises(ConfigError, match="min_preferred_axes"):
        ProjectBible.model_validate(data)


def test_max_preferred_needs_above_vocabulary_is_rejected() -> None:
    data = _minimal_data()
    data["generation"]["max_preferred_needs"] = 5
    with pytest.raises(ConfigError, match="max_preferred_needs"):
        ProjectBible.model_validate(data)


def test_inverted_preferred_needs_bounds_are_rejected() -> None:
    data = _minimal_data()
    data["generation"]["min_preferred_needs"] = 1
    data["generation"]["max_preferred_needs"] = 0
    with pytest.raises(ConfigError, match="max_preferred_needs"):
        ProjectBible.model_validate(data)


def test_ideal_span_ratio_above_one_is_rejected() -> None:
    """1을 넘으면 슬라이더보다 넓은 구간을 허용한다는 뜻인데, 그것은 다른 검사가 이미 막는다."""
    data = _minimal_data()
    data["generation"]["max_ideal_span_ratio"] = 1.5
    with pytest.raises(ConfigError, match="max_ideal_span_ratio"):
        ProjectBible.model_validate(data)


# ---------------------------------------------------------------- 조회


def test_find_returns_none_for_unknown_key() -> None:
    """어휘 밖의 값은 설정 오류가 아니라 생성물의 문제다. 예외가 아니라 None으로 돌려준다."""
    bible = ProjectBible.model_validate(_minimal_data())
    assert bible.find_need("filling") is not None
    assert bible.find_need("nonexistent") is None
    assert bible.find_axis("nonexistent") is None
    assert bible.find_dietary("nonexistent") is None
    assert bible.find_voice("gruff") is not None
    assert bible.find_voice("nonexistent") is None


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
