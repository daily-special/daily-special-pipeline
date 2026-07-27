"""출력 패키지의 봉투. 데이터 계약 3절.

모든 출력 파일이 같은 봉투를 쓴다. 봉투가 없으면 파일 하나를 집었을 때 그것이 무엇이고
어느 계약·어느 밸런스로 만들어졌는지 알 방법이 없다.

여기 있는 것은 **모양**뿐이다. 파일로 쓰는 일과 provenance 로그는 어댑터의 몫이다.
"""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

SCHEMA_VERSION = "1.0.0"
"""이 계약의 현재 버전. 증가 규칙은 데이터 계약 3-1절."""

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class PackageKind(StrEnum):
    """파일 종류. 파일명과 중복되지만 파일이 옮겨져도 정체를 잃지 않게 한다.

    계약이 소유하는 어휘라 ProjectBible이 아니라 여기 있다. 이 목록이 늘면
    세 저장소가 함께 움직여야 한다.
    """

    GUESTS = "guests"
    DISHES = "dishes"
    INGREDIENTS = "ingredients"
    LINES = "lines"


class Package[T: BaseModel](BaseModel):
    """출력 파일 한 장."""

    model_config = ConfigDict(frozen=True)

    schema_version: str
    """이 계약의 버전. 소비 측이 호환성을 판단하는 유일한 기준이다."""

    bible_version: str
    """이 산출물을 만든 project_bible.json의 버전. 밸런스 추적용이며 분기에 쓰지 않는다."""

    kind: PackageKind
    generated_at: datetime
    """UTC. 시간대가 없는 값은 받지 않는다 — 어느 시각인지 알 수 없는 기록은 기록이 아니다."""

    run_id: str
    """생성 실행 식별자. provenance 로그와 이어진다."""

    items: list[T]

    @model_validator(mode="after")
    def _check_envelope(self) -> "Package[T]":
        if not _SEMVER.match(self.schema_version):
            raise ValueError(f"schema_version이 semver가 아니다: {self.schema_version}")
        if not self.bible_version.strip():
            raise ValueError("bible_version이 비어 있다. 밸런스를 되짚을 수 없게 된다")
        if not self.run_id.strip():
            raise ValueError("run_id가 비어 있다")

        offset = self.generated_at.utcoffset()
        if offset is None:
            raise ValueError("generated_at에 시간대가 없다. UTC로 적는다")
        if offset.total_seconds() != 0:
            raise ValueError(f"generated_at이 UTC가 아니다: {self.generated_at.isoformat()}")

        return self


def major_of(version: str) -> int:
    """semver의 major 자리.

    소비 측은 이 값이 다르면 로드를 거부한다. 조용히 넘어가면 밸런스가 어긋난 채
    굴러가고, 그것은 파싱 실패보다 훨씬 늦게 발견된다.
    """
    if not _SEMVER.match(version):
        raise ValueError(f"semver가 아니다: {version}")
    return int(version.split(".")[0])
