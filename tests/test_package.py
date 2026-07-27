"""출력 봉투의 규칙을 고정한다 (데이터 계약 3절).

봉투는 세 저장소가 모두 읽는 유일한 공통 구조다. 여기가 흔들리면 파일 종류와 무관하게
전부 깨진다.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ConfigDict

from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind, major_of


class _Item(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str


def _envelope(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "bible_version": "test.1",
        "kind": "guests",
        "generated_at": "2026-07-27T00:00:00Z",
        "run_id": "run_test_01",
        "items": [{"key": "a"}],
    }
    data.update(overrides)
    return data


def test_envelope_parses() -> None:
    package = Package[_Item].model_validate(_envelope())
    assert package.kind is PackageKind.GUESTS
    assert package.items == [_Item(key="a")]


def test_envelope_is_generic_over_item_type() -> None:
    """봉투는 네 가지 파일에 같은 모양으로 쓰인다. 종류마다 봉투를 새로 만들지 않는다."""

    class _Other(BaseModel):
        dish_id: str

    package = Package[_Other].model_validate(
        _envelope(kind="dishes", items=[{"dish_id": "dish_stew"}])
    )
    assert package.items[0].dish_id == "dish_stew"


def test_unknown_field_is_ignored() -> None:
    """소비 측은 모르는 필드를 무시한다 (계약 3-2절).

    이래야 파이프라인이 필드를 더할 때 minor만 올리고 혼자 나갈 수 있다. 파싱 실패로
    다루면 필드 하나를 더할 때마다 세 저장소가 함께 움직여야 한다.
    """
    package = Package[_Item].model_validate(_envelope(future_field="아직 없는 것"))
    assert not hasattr(package, "future_field")


def test_unknown_kind_is_rejected() -> None:
    """kind는 계약이 소유하는 닫힌 어휘다. 늘리려면 세 저장소가 함께 움직여야 한다."""
    with pytest.raises(ValueError):
        Package[_Item].model_validate(_envelope(kind="recipes"))


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-beta", ""])
def test_non_semver_schema_version_is_rejected(version: str) -> None:
    """소비 측이 호환성을 판단하는 유일한 기준이라 모양이 흔들리면 안 된다."""
    with pytest.raises(ValueError, match="semver"):
        Package[_Item].model_validate(_envelope(schema_version=version))


def test_naive_timestamp_is_rejected() -> None:
    """어느 시각인지 알 수 없는 기록은 기록이 아니다."""
    with pytest.raises(ValueError, match="시간대가 없다"):
        Package[_Item].model_validate(_envelope(generated_at="2026-07-27T00:00:00"))


def test_non_utc_timestamp_is_rejected() -> None:
    """계약은 UTC로 적는다. 현지 시각이 섞이면 실행 순서를 되짚을 수 없다."""
    local = datetime(2026, 7, 27, 9, 0, tzinfo=timezone(timedelta(hours=9)))
    with pytest.raises(ValueError, match="UTC가 아니다"):
        Package[_Item].model_validate(_envelope(generated_at=local))


def test_utc_timestamp_is_accepted() -> None:
    package = Package[_Item].model_validate(
        _envelope(generated_at=datetime(2026, 7, 27, tzinfo=UTC))
    )
    assert package.generated_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("field", ["bible_version", "run_id"])
def test_blank_provenance_field_is_rejected(field: str) -> None:
    """생성 결과를 되짚을 수 없으면 밸런스 조정이 추측이 된다."""
    with pytest.raises(ValueError):
        Package[_Item].model_validate(_envelope(**{field: "   "}))


def test_major_of() -> None:
    assert major_of("1.0.0") == 1
    assert major_of("2.13.4") == 2
    with pytest.raises(ValueError, match="semver"):
        major_of("1.0")
