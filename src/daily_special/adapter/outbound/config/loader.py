"""ProjectBible을 JSON 파일에서 읽는다.

설정이 잘못된 경우는 전부 ConfigError로 나간다 — 파일이 없든, JSON이 깨졌든,
모양이 스키마와 다르든, 불변식을 어겼든. 부르는 쪽이 예외를 종류별로 구별할 이유가 없다.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from daily_special.common.errors import ConfigError
from daily_special.domain.bible import ProjectBible

DEFAULT_BIBLE_PATH = Path("data/project_bible.json")
"""저장소 루트 기준 상대 경로. 테스트는 경로를 명시해 넘긴다."""


def load_bible(path: Path = DEFAULT_BIBLE_PATH) -> ProjectBible:
    """설정을 읽어 검증까지 마친 ProjectBible을 돌려준다."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없다: {path}") from exc

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"설정 파일이 올바른 JSON이 아니다: {path} — {exc}") from exc

    try:
        return ProjectBible.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"설정 파일의 모양이 스키마와 다르다: {path}\n{exc}") from exc
