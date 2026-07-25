"""계층 경계 회귀 테스트.

규약 1절: 경계는 사람이 아니라 도구가 지킨다. 위반은 주석이 아니라 실패로 나타나야 한다.
"""

import subprocess

from daily_special.adapter.outbound.llm.fake import FakeLlm
from daily_special.application.port.llm import LlmPort


def test_layer_boundaries_hold() -> None:
    """adapter → application → domain 방향과 common의 독립을 검사한다."""
    result = subprocess.run(["lint-imports"], capture_output=True, text=True)
    assert result.returncode == 0, f"계층 경계 위반:\n{result.stdout}{result.stderr}"


def test_fake_llm_satisfies_port() -> None:
    """가짜 어댑터가 LlmPort를 구조적으로 만족한다.

    실질적인 검사는 mypy가 이 대입문에서 한다 — 어댑터 시그니처가 포트에서
    벗어나면 `mypy --strict`가 실패한다. Protocol이라 런타임 isinstance는 쓰지 않는다.
    """
    port: LlmPort = FakeLlm([])
    assert port.generate is not None
