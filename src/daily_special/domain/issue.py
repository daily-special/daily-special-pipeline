"""검증 결과 항목.

검증은 예외를 던지지 않고 Issue를 모아 돌려준다. 첫 위반에서 멈추면
재생성 피드백에 한 줄밖에 싣지 못한다. 전부 모아야 한 번의 재생성으로 고칠 수 있다.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    """위반의 무게."""

    ERROR = "error"
    """규칙 위반. 재생성 대상이다."""

    WARNING = "warning"
    """산출물은 살리되 경고를 붙여 내보낸다."""


class Issue(BaseModel):
    """검증에서 발견한 위반 하나."""

    model_config = ConfigDict(frozen=True)

    severity: Severity
    field: str
    """위반이 발견된 필드 경로. 예: "ideal_ranges.heat"."""

    message: str
    """한국어. 재생성 프롬프트에 그대로 실리므로 사람이 아니라 모델이 읽는다고 생각하고 쓴다."""


def has_errors(issues: Sequence[Issue]) -> bool:
    """ERROR가 하나라도 있으면 재생성 대상이다. WARNING만 있으면 통과시킨다."""
    return any(issue.severity is Severity.ERROR for issue in issues)
