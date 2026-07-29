"""OpenAI 어댑터를 고정한다. 네트워크는 타지 않는다.

어댑터가 지는 책임은 셋이다 — 티어를 모델로 바꾸고, 구조화 출력을 받아오고,
**전송 실패만** 재시도한다. 검증 실패로 인한 재생성은 여기 없어야 한다 (규약 5-3).
"""

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI, OpenAIError
from pydantic import BaseModel

from daily_special.adapter.outbound.llm.openai_llm import OpenAiLlm
from daily_special.application.port.llm import Tier
from daily_special.common.errors import LlmError


class _Answer(BaseModel):
    text: str


def _connection_error() -> APIConnectionError:
    return APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))


class _StubResponses:
    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _StubClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.responses = _StubResponses(outcomes)


def _llm(outcomes: list[Any], **kwargs: Any) -> tuple[OpenAiLlm, _StubResponses]:
    stub = _StubClient(outcomes)
    adapter = OpenAiLlm(cast(AsyncOpenAI, stub), backoff_seconds=0.0, **kwargs)
    return adapter, stub.responses


def _ok(text: str = "좋다") -> SimpleNamespace:
    """실제 응답처럼 usage를 함께 준다 — 어댑터가 토큰을 기록하기 때문이다."""
    return SimpleNamespace(
        output_parsed=_Answer(text=text),
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


async def _generate(adapter: OpenAiLlm, tier: Tier = Tier.QUALITY) -> _Answer:
    return await adapter.generate(instruction="지시", context="맥락", schema=_Answer, tier=tier)


# ---------------------------------------------------------------- 티어 → 모델


async def test_quality_tier_uses_the_pinned_model() -> None:
    """모델 ID를 별칭 없이 고정한다 (규약 4-3).

    세대 별칭에 기대면 일상적인 호출이 어느 날 조용히 가장 비싼 티어로 간다.
    """
    adapter, responses = _llm([_ok()])
    await _generate(adapter, Tier.QUALITY)

    assert responses.calls[0]["model"] == "gpt-5.6-terra"
    assert responses.calls[0]["reasoning"] == {"effort": "low"}


async def test_fast_tier_turns_reasoning_off() -> None:
    """판정은 창작이 아니라 비교다. 추론에 돈을 쓸 자리가 아니다."""
    adapter, responses = _llm([_ok()])
    await _generate(adapter, Tier.FAST)

    assert responses.calls[0]["model"] == "gpt-5.6-luna"
    assert responses.calls[0]["reasoning"] == {"effort": "none"}


async def test_prompt_parts_map_to_the_api() -> None:
    """포트의 instruction/context가 어디로 가는지 고정한다."""
    adapter, responses = _llm([_ok()])
    await _generate(adapter)

    assert responses.calls[0]["instructions"] == "지시"
    assert responses.calls[0]["input"] == "맥락"
    assert responses.calls[0]["text_format"] is _Answer


async def test_output_is_capped() -> None:
    """폭주 방지. 오프라인 배치라 한 번의 실수가 그대로 청구서다."""
    adapter, responses = _llm([_ok()])
    await _generate(adapter)

    assert responses.calls[0]["max_output_tokens"] > 0


# ---------------------------------------------------------------- 재시도


async def test_transport_failure_is_retried() -> None:
    adapter, responses = _llm([_connection_error(), _connection_error(), _ok("셋째에 성공")])

    result = await _generate(adapter)

    assert result.text == "셋째에 성공"
    assert len(responses.calls) == 3


async def test_retries_are_bounded() -> None:
    """무한히 다시 걸면 죽은 것을 모른 채 돈과 시간을 쓴다."""
    adapter, responses = _llm([_connection_error()] * 5, max_attempts=3)

    with pytest.raises(LlmError, match="3회"):
        await _generate(adapter)

    assert len(responses.calls) == 3


async def test_non_transport_failure_is_not_retried() -> None:
    """잘못된 요청은 다시 걸어도 같다. 재시도는 낭비다."""
    adapter, responses = _llm([OpenAIError("잘못된 요청"), _ok()])

    with pytest.raises(LlmError):
        await _generate(adapter)

    assert len(responses.calls) == 1


async def test_missing_structured_output_is_an_error() -> None:
    """거부되었거나 응답이 빈 경우. None을 그대로 흘려보내면 훨씬 뒤에서 터진다."""
    adapter, _ = _llm([SimpleNamespace(output_parsed=None, usage=None)])

    with pytest.raises(LlmError, match="구조화 출력"):
        await _generate(adapter)


async def test_openai_exceptions_do_not_escape() -> None:
    """OpenAI 고유 예외가 새면 프로바이더를 격리한 의미가 없다 (규약 4-2)."""
    adapter, _ = _llm([OpenAIError("내부 사정")])

    with pytest.raises(LlmError):
        await _generate(adapter)


async def test_calls_are_recorded() -> None:
    """오프라인 배치라 몇 번 불렀는지가 곧 비용이다."""
    adapter, _ = _llm([_ok(), _ok()])

    await _generate(adapter, Tier.QUALITY)
    await _generate(adapter, Tier.FAST)

    assert [tier for _, tier in adapter.calls] == [Tier.QUALITY, Tier.FAST]


def test_zero_attempts_is_rejected() -> None:
    with pytest.raises(LlmError):
        OpenAiLlm(cast(AsyncOpenAI, _StubClient([])), max_attempts=0)
