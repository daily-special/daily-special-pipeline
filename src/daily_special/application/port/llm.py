"""LLM 포트.

도메인 언어("페르소나를 생성하라")가 아니라 범용 구조화 생성 하나로 둔다.
도메인 언어 포트를 쓰면 프롬프트가 어댑터 안으로 들어가는데, 프롬프트는 인프라 지식이
아니라 도메인 지식이다 — 어떤 손님을 원하는지는 어느 LLM 회사를 쓰는지와 무관하다.

따라서 프롬프트 조립은 application에 남고, 어댑터는 "이 스키마로 구조화 출력을
받아온다" 하나만 한다. 생성 작업이 늘어도 포트는 늘지 않는다.
"""

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel


class Tier(StrEnum):
    """모델 티어. application은 모델명을 모른다.

    티어 → 모델 ID 변환은 어댑터가 한다. 세대 별칭에 의존하면 일상적인 호출이
    조용히 가장 비싼 티어로 갈 수 있으므로, 어댑터는 모델 ID를 고정해 적는다.
    """

    QUALITY = "quality"
    """생성. 품질이 눈에 보이는 곳 — 페르소나·요리·대사."""

    FAST = "fast"
    """판정. 창작이 아니라 비교인 곳 — 중복 검토·말투 검사."""


class LlmPort(Protocol):
    """구조화 출력 생성. 프로바이더 중립이다."""

    async def generate[T: BaseModel](
        self,
        *,
        instruction: str,
        context: str,
        schema: type[T],
        tier: Tier,
    ) -> T:
        """schema 모양의 구조화 출력을 받아온다.

        전송 실패(네트워크·rate limit) 재시도는 이 뒤의 어댑터가 처리한다.
        검증 실패로 인한 재생성은 서비스가 처리한다. 두 재시도를 한 곳에 두면
        LLM 호출 횟수를 추적할 수 없게 된다.
        """
        ...
