"""재생성 정책 — 무엇이 살아남고 무엇이 다시 만들어지는가.

순수 함수다. LLM을 부르지 않는다. 부르는 쪽이 이 판단을 받아 모자란 만큼만 다시 뽑는다.

**배치 전체가 아니라 어긴 것만 다시 만든다.** 8개 중 1개가 틀렸을 때 8개를 다시 뽑으면
멀쩡한 7개를 버리고 돈을 8배로 쓴다. 게다가 다시 뽑은 8개가 이번엔 다른 곳에서 틀릴 수
있어 루프가 수렴하지 않는다.

정책은 콘텐츠 종류를 모른다. 손님이든 재료든 "ERROR가 있으면 다시, ID가 겹치면 뒤엣것을
다시"는 같은 규칙이라, 검사 방법만 받아서 쓴다.
"""

from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from daily_special.domain.issue import Issue, Severity, has_errors

MAX_REGENERATIONS = 3
"""검증 실패로 다시 부르는 최대 횟수 (규약 5-3).

전송 실패 재시도와 다른 층이다. 그쪽은 어댑터가 세고, 이쪽은 서비스가 센다.
한 곳에 두면 LLM 호출 횟수를 추적할 수 없게 된다.
"""


class Partition[T: BaseModel](BaseModel):
    """검증을 통과한 것과 다시 만들어야 할 것."""

    model_config = ConfigDict(frozen=True)

    kept: list[T]
    rejected: list[T]
    issues: list[Issue]
    """**rejected가** 왜 걸렸는지. 그대로 재생성 프롬프트에 실린다.

    통과한 것의 경고는 넣지 않는다. 다시 만들지 않을 것을 고치라고 말하면
    모델이 엉뚱한 데 힘을 쓴다.
    """


def partition_by_errors[T: BaseModel](
    items: Sequence[T],
    *,
    check: Callable[[T], list[Issue]],
    id_of: Callable[[T], str],
) -> Partition[T]:
    """ERROR가 있는 것만 골라낸다. WARNING은 통과시킨다.

    Issue의 필드 경로(`items[3].voice`)를 되파싱하지 않고 항목별로 다시 검사한다.
    문자열을 파싱하면 경로 형식이 바뀔 때 조용히 깨지고, 재검사는 공짜다.

    ID 중복은 개별 검사로 잡히지 않으므로 여기서 본다. **먼저 온 쪽을 남긴다** —
    둘 다 버리면 멀쩡한 것 하나를 이유 없이 잃는다.
    """
    kept: list[T] = []
    rejected: list[T] = []
    issues: list[Issue] = []
    seen_ids: set[str] = set()

    for item in items:
        item_issues = check(item)
        item_id = id_of(item)

        if item_id in seen_ids:
            item_issues = [*item_issues, _duplicate_issue(item_id)]

        if has_errors(item_issues):
            rejected.append(item)
            issues += item_issues
        else:
            kept.append(item)
            seen_ids.add(item_id)

    return Partition(kept=kept, rejected=rejected, issues=issues)


def _duplicate_issue(item_id: str) -> Issue:
    return Issue(
        severity=Severity.ERROR,
        field="id",
        message=f"ID '{item_id}'가 이미 있는 것과 겹친다. 항목마다 다른 ID를 쓴다",
    )
