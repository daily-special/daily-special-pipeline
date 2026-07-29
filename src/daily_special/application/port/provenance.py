"""생성 이력 포트 (규약 9절).

모든 LLM 호출이 `LlmPort` 한 곳을 지나므로 그 지점에서 기록한다. 기성 관측성 도구를
쓰지 않기로 한 대가를 이것으로 메우고, 동시에 이 로그가 「AI 활용 기술 문서」에 실을
**실측 데이터**가 된다 — 호출 수, 비용, 재생성률.

**`LlmPort`의 반환 타입을 바꾸지 않는다.** 토큰 수를 돌려주게 만들면 모든 호출부가
영향을 받고, `application`이 모델명을 모르기로 한 것과 같은 이유로 토큰도 몰라야 한다.
대신 어댑터가 이 포트를 받아 자기가 기록한다.
"""

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class CallOutcome(BaseModel):
    """호출 하나의 결과. 규약 9절의 필드 목록 그대로다."""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    tier: str
    model_id: str

    instruction_hash: str
    """프롬프트 원문은 여기 넣지 않는다. 로그가 프롬프트로 뒤덮이면 읽을 수 없다."""

    schema_name: str
    input_tokens: int
    output_tokens: int
    latency_ms: int

    attempt: int
    """전송 재시도 횟수. 1부터 센다."""

    outcome: str
    """`ok` · `retry` · `failed`. 재생성률과 실패율이 여기서 나온다."""


class ProvenancePort(Protocol):
    """기록만 한다. 읽기는 사람이 파일로 한다."""

    def record(self, call: CallOutcome) -> None:
        """호출 하나를 남긴다. 실패해도 생성을 멈추지 않는다 — 기록은 보조 장치다."""
        ...

    def save_prompt(self, instruction_hash: str, instruction: str, context: str) -> None:
        """프롬프트 원문을 따로 둔다. 같은 해시는 한 번만 저장하면 된다."""
        ...
