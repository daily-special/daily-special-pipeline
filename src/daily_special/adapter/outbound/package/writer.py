"""출력 패키지를 파일로 쓴다 (계약 4절).

`out/` 전체를 git에 커밋하므로 **diff가 읽히게** 써야 한다. 들여쓰기를 주고 한글을
이스케이프하지 않는 이유가 그것이다 — 생성 결과의 변화가 눈에 보이는 것이 밸런스
작업에 직접 도움이 된다.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind


def build_package[T: BaseModel](
    *,
    kind: PackageKind,
    items: list[T],
    bible_version: str,
    run_id: str,
) -> Package[T]:
    """봉투를 씌운다. 시각은 여기서 한 번만 찍는다."""
    return Package[T](
        schema_version=SCHEMA_VERSION,
        bible_version=bible_version,
        kind=kind,
        generated_at=datetime.now(UTC),
        run_id=run_id,
        items=items,
    )


def write_package(package: Package[Any], directory: Path) -> Path:
    """`<directory>/<kind>.json`에 쓴다. 파일명은 봉투의 kind가 정한다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{package.kind}.json"

    payload = package.model_dump(mode="json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
