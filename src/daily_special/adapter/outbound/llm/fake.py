"""테스트용 가짜 LLM 어댑터.

테스트는 API 키 없이 전부 통과해야 한다. 실호출 테스트는 `live` 마커로 분리되어
기본 실행에서 제외된다.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from daily_special.application.port.llm import Tier


class FakeLlm:
    """미리 넣어둔 응답을 순서대로 돌려준다.

    LlmPort를 명시적으로 상속하지 않는다 — Protocol이므로 구조로 만족하면 된다.
    상속하면 어댑터가 포트 타입에 결합되고, 그건 격리하려던 방향과 반대다.
    """

    def __init__(self, responses: Sequence[BaseModel]) -> None:
        self._pending = list(responses)
        self.calls: list[tuple[str, Tier]] = []
        """호출 기록 (instruction, tier). 테스트가 "어느 티어로 몇 번 불렀나"를 검사한다."""

    async def generate[T: BaseModel](
        self,
        *,
        instruction: str,
        context: str,
        schema: type[T],
        tier: Tier,
    ) -> T:
        self.calls.append((instruction, tier))

        if not self._pending:
            raise AssertionError(
                "가짜 어댑터에 준비된 응답이 없다. 테스트가 호출 횟수를 잘못 잡았다"
            )

        response = self._pending.pop(0)
        if not isinstance(response, schema):
            raise AssertionError(
                "준비된 응답의 타입이 요청한 스키마와 다르다: "
                f"{type(response).__name__} != {schema.__name__}"
            )
        return response
