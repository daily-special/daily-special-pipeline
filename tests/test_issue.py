"""Issue의 계약을 고정한다.

설계 결정은 주석이 아니라 테스트로 고정한다 (규약 6절).
여기서 고정하는 결정은 둘이다 — WARNING은 재생성을 부르지 않는다, Issue는 불변이다.
"""

import pytest
from pydantic import ValidationError

from daily_special.domain.issue import Issue, Severity, has_errors


def _issue(severity: Severity) -> Issue:
    return Issue(
        severity=severity,
        field="ideal_ranges.heat",
        message="이상 구간이 슬라이더 범위를 벗어났다",
    )


def test_has_errors_is_true_when_any_error() -> None:
    issues = [_issue(Severity.WARNING), _issue(Severity.ERROR)]
    assert has_errors(issues) is True


def test_has_errors_is_false_when_only_warnings() -> None:
    """WARNING만 있으면 재생성하지 않는다. 경고를 붙여 그대로 내보낸다."""
    issues = [_issue(Severity.WARNING), _issue(Severity.WARNING)]
    assert has_errors(issues) is False


def test_has_errors_is_false_when_empty() -> None:
    assert has_errors([]) is False


def test_issue_is_frozen() -> None:
    """검증 결과는 만들어진 뒤 바뀌지 않는다. 재생성 피드백이 도중에 변조되면 추적이 끊긴다."""
    issue = _issue(Severity.ERROR)
    with pytest.raises(ValidationError):
        issue.severity = Severity.WARNING
