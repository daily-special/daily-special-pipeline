"""OpenAI 어댑터.

OpenAI 고유의 타입·예외·개념은 **이 파일 밖으로 나가지 않는다** (규약 4-2).
새어 나가면 프로바이더를 격리한 의미가 없어진다.

여기가 하는 일은 셋이다 — 티어를 모델로 바꾸고, 구조화 출력을 받아오고,
전송 실패를 재시도한다. 검증 실패로 인한 재생성은 서비스의 몫이다 (규약 5-3).
"""

import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    LengthFinishReasonError,
    OpenAIError,
    RateLimitError,
)
from openai.types.shared.reasoning_effort import ReasoningEffort
from pydantic import BaseModel

from daily_special.adapter.outbound.provenance.recorder import NullRecorder
from daily_special.application.port.llm import Tier
from daily_special.application.port.provenance import CallOutcome, ProvenancePort
from daily_special.common.errors import LlmError


@dataclass(frozen=True)
class _ModelSpec:
    """티어 하나가 무엇으로 불리는가."""

    model_id: str
    reasoning_effort: ReasoningEffort


_TIER_MODELS: dict[Tier, _ModelSpec] = {
    Tier.QUALITY: _ModelSpec("gpt-5.6-terra", "low"),
    Tier.FAST: _ModelSpec("gpt-5.6-luna", "none"),
}
"""티어 → 모델. **모델 ID를 별칭 없이 고정해 적는다** (규약 4-3).

세대 별칭에 기대면 일상적인 호출이 어느 날 조용히 가장 비싼 티어로 간다.

reasoning 다이얼도 여기 있다. 추론량은 application이 알 필요가 없는 값이고,
모델 선택과 같은 성격이라 같은 표에 둔다.

QUALITY에 low를 주는 이유: 스키마가 어휘 밖 값을 구조적으로 막고 check_guest가
범위 위반을 공짜로 잡으므로, 추론은 규칙으로 잡을 수 없는 것에만 쓰인다 —
배치 안에서 서로 겹치지 않는 것, 그리고 사연과 수치가 앞뒤로 맞는 것.
FAST는 창작이 아니라 비교라서 0으로 둔다.
"""

_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)
"""다시 걸면 될 수 있는 것만. 잘못된 요청이나 인증 실패는 재시도해도 같다."""

_DEFAULT_MAX_OUTPUT_TOKENS = 16000
"""폭주 방지용 상한. 손님 여남은 명에 필요한 양의 몇 배로 잡아 잘리지 않게 한다."""


class OpenAiLlm:
    """LlmPort의 실제 구현.

    FakeLlm과 마찬가지로 포트를 상속하지 않는다 — Protocol이라 구조로 만족하면 된다.
    """

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        *,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
        provenance: ProvenancePort | None = None,
    ) -> None:
        if max_attempts < 1:
            raise LlmError(f"max_attempts는 1 이상이어야 한다: {max_attempts}")

        self._client = client if client is not None else _build_client()
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._max_output_tokens = max_output_tokens
        self._provenance: ProvenancePort = provenance or NullRecorder()
        self.calls: list[tuple[str, Tier]] = []
        """호출 기록. 오프라인 배치라 몇 번 불렀는지가 곧 비용이다."""

    async def generate[T: BaseModel](
        self,
        *,
        instruction: str,
        context: str,
        schema: type[T],
        tier: Tier,
    ) -> T:
        spec = _TIER_MODELS[tier]
        self.calls.append((instruction, tier))

        digest = hashlib.sha256(instruction.encode("utf-8")).hexdigest()[:16]
        self._provenance.save_prompt(digest, instruction, context)

        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            started = time.monotonic()
            try:
                parsed, usage = await self._parse_once(
                    spec=spec, instruction=instruction, context=context, schema=schema
                )
            except _RETRYABLE as error:
                last_error = error
                self._log(spec, tier, digest, schema, attempt + 1, "retry", started, (0, 0))
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(self._backoff_seconds * 2**attempt)
            except LlmError:
                self._log(spec, tier, digest, schema, attempt + 1, "failed", started, (0, 0))
                raise
            else:
                self._log(spec, tier, digest, schema, attempt + 1, "ok", started, usage)
                return parsed

        raise LlmError(
            f"{spec.model_id} 호출이 {self._max_attempts}회 모두 전송 단계에서 실패했다: "
            f"{last_error}"
        ) from last_error

    async def _parse_once[T: BaseModel](
        self,
        *,
        spec: _ModelSpec,
        instruction: str,
        context: str,
        schema: type[T],
    ) -> tuple[T, tuple[int, int]]:
        try:
            response = await self._client.responses.parse(
                model=spec.model_id,
                instructions=instruction,
                input=context,
                text_format=schema,
                reasoning={"effort": spec.reasoning_effort},
                max_output_tokens=self._max_output_tokens,
            )
        except LengthFinishReasonError as error:
            raise LlmError(
                f"출력이 상한({self._max_output_tokens} 토큰)에 걸려 잘렸다. "
                "한 번에 만드는 수를 줄이거나 상한을 올린다"
            ) from error
        except _RETRYABLE:
            raise
        except OpenAIError as error:
            raise LlmError(f"{spec.model_id} 호출이 실패했다: {error}") from error

        parsed = response.output_parsed
        if parsed is None:
            raise LlmError(
                f"{spec.model_id}가 구조화 출력을 내지 않았다 (거부되었거나 응답이 비었다)"
            )

        usage = response.usage
        tokens = (usage.input_tokens, usage.output_tokens) if usage is not None else (0, 0)
        return parsed, tokens

    def _log(
        self,
        spec: _ModelSpec,
        tier: Tier,
        digest: str,
        schema: type[BaseModel],
        attempt: int,
        outcome: str,
        started: float,
        tokens: tuple[int, int],
    ) -> None:
        """호출 하나를 남긴다. 기록이 실패해도 생성은 계속된다."""
        self._provenance.record(
            CallOutcome(
                ts=datetime.now(UTC),
                tier=str(tier),
                model_id=spec.model_id,
                instruction_hash=digest,
                schema_name=schema.__name__,
                input_tokens=tokens[0],
                output_tokens=tokens[1],
                latency_ms=int((time.monotonic() - started) * 1000),
                attempt=attempt,
                outcome=outcome,
            )
        )


def _build_client() -> AsyncOpenAI:
    """OPENAI_API_KEY를 환경에서 읽는다.

    키를 코드나 설정 파일에 두지 않는다. 로컬에서는 `.env`에 넣고
    `uv run --env-file .env`로 실행한다 — 그래서 기본 테스트는 키 없이 돈다.
    """
    try:
        return AsyncOpenAI()
    except OpenAIError as error:
        raise LlmError(
            f"OpenAI 클라이언트를 만들 수 없다. OPENAI_API_KEY가 설정되어 있는지 본다: {error}"
        ) from error
