"""프롬프트 조립.

프롬프트는 인프라 지식이 아니라 도메인 지식이라 어댑터가 아니라 여기 산다 (규약 4-2).
어떤 손님을 원하는지는 어느 LLM 회사를 쓰는지와 무관하다.

**어휘와 필드별 규칙은 여기 없다.** 그쪽은 schema_builder가 필드 설명으로 싣는다.
같은 말을 두 곳에 쓰면 토큰을 두 번 내고, 더 나쁘게는 둘이 어긋난다.
여기 있는 것은 스키마가 표현할 수 없는 것뿐이다 — 세계관, 배치 안의 다양성, 톤.
"""

from collections.abc import Sequence

from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.guest import Guest
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import Issue

_WORLD = """\
「오늘의 정식」은 모험가 길드의 구내식당을 운영하는 코지 경영 시뮬레이션이다.
플레이어는 길드 식당의 요리사이고, 손님은 길드를 드나드는 모험가와 직원들이다.

- 화려한 미식이 아니라 **가성비 백반집**이다. \
손님들은 비싼 것이 아니라 오늘 자기에게 맞는 것을 원한다
- 코지한 톤이다. 악당도 비극도 없다. 고단한 하루를 보낸 사람들이 밥을 먹으러 온다
- 판타지지만 생활감이 있다. \
용을 잡는 영웅보다 변방을 순찰하고 장부를 적고 짐을 나르는 사람들이 많다\
"""
"""세계관. 설계 문서(design.md)가 원본이고 여기 실린 것은 생성에 필요한 요약이다."""

_RULES = """\
지켜야 할 것:

1. **서로 겹치지 않게 만든다.** 이것이 가장 중요하다. 직업·처지·말투·취향이 골고루 갈려야 하고,
   비슷한 사람 둘을 만드느니 한 명을 확실히 다르게 만든다
2. **정체성과 숫자가 앞뒤로 맞아야 한다.** 불맛을 즐기는 사람이 약불 구간을 갖거나,
   지친 사람이 자극적인 것만 찾으면 틀린 것이다. 사연이 먼저고 수치가 그것을 따라간다
3. **대부분은 평범한 사람이다.** 사연이 특별한 손님은 소수여야 특별해 보인다
4. 모든 글은 한국어로 쓴다. 이름도 한국어 표기로 짓는다\
"""


def build_guest_instruction() -> str:
    """생성기의 역할과 규칙. 요청마다 바뀌지 않는다."""
    return (
        "너는 게임의 캐릭터 설정을 짓는 작가다. "
        "아래 세계관에 맞는 식당 손님들을 만든다.\n\n"
        f"{_WORLD}\n\n{_RULES}"
    )


def build_guest_context(
    bible: ProjectBible,
    count: int,
    *,
    existing: Sequence[Guest] = (),
    issues: Sequence[Issue] = (),
) -> str:
    """이번 요청에만 해당하는 것.

    어휘 목록을 여기 싣지 않는다 — 스키마의 필드 설명이 이미 나르고 있고,
    모델은 그것을 함께 본다.

    `existing`과 `issues`는 재생성 때만 찬다. 첫 호출과 재생성이 같은 함수를 쓰는 이유는
    축 설명 문단이 양쪽에 똑같이 필요하기 때문이다 — 갈라두면 한쪽만 고치게 된다.
    """
    if count < 1:
        raise DomainError(f"손님 수는 1 이상이어야 한다: {count}")

    axes = " · ".join(f"{axis.label}({axis.key})" for axis in bible.axes)
    parts = [
        f"손님 {count}명을 만들어라.",
        f"이 식당에서 요리는 {axes} 축으로 조절된다. "
        "손님마다 그중 만족하는 구간이 다르고, 플레이어는 반응을 보며 그 구간을 추측해 간다. "
        "그래서 어떤 축에는 취향이 뚜렷하고 어떤 축에는 무관심한 편이 "
        "손님을 더 또렷하게 만든다. "
        f"다만 최소 {bible.generation.min_preferred_axes}개 축에는 취향이 있어야 한다 — "
        "전부 비우면 플레이어가 추측할 것이 없어진다.",
    ]

    if existing:
        parts.append(_avoid_section(existing))
    if issues:
        parts.append(_feedback_section(issues))

    parts.append(f"{count}명이 한 식당에 같은 날 들어와도 서로 구별되는지 확인하고 내라.")
    return "\n\n".join(parts)


_INGREDIENT_RULES = """\
지켜야 할 것:

1. **신선과 보존이 골고루 나와야 한다.** 신선만 있으면 상시 메뉴를 짤 수 없고,
   보존만 있으면 장보기가 도박이 아니게 된다. 게임의 두 층이 각각 이 둘에 얹혀 있다
2. **서로 겹치지 않게 만든다.** "말린 강가 허브"와 "건조 강변 약초"는 같은 재료다
3. **요리로 이어질 수 있어야 한다.** 설명만 읽고 무엇을 만들지 떠오르지 않으면 쓸모가 없다.
   맛과 쓰임을 적는다
4. **평범한 식재료가 대부분이다.** 구내식당의 백반이지 궁정 연회가 아니다.
   진귀한 재료는 소수여야 특별해 보인다
5. 모든 글은 한국어로 쓴다\
"""


def build_ingredient_instruction() -> str:
    """재료 생성기의 역할과 규칙."""
    return (
        "너는 게임의 식재료 목록을 짓는 작가다. "
        "아래 세계관의 식당이 쓸 재료들을 만든다.\n\n"
        f"{_WORLD}\n\n{_INGREDIENT_RULES}"
    )


def build_ingredient_context(
    bible: ProjectBible,
    count: int,
    *,
    existing: Sequence[Ingredient] = (),
    issues: Sequence[Issue] = (),
) -> str:
    """이번 요청에만 해당하는 것. 어휘와 가격 범위는 스키마 필드 설명이 나른다."""
    if count < 1:
        raise DomainError(f"재료 수는 1 이상이어야 한다: {count}")

    parts = [
        f"재료 {count}개를 만들어라.",
        "이 식당은 모험가 길드의 구내식당이다. 재료는 아침 시장에서 사 오고, "
        "신선한 것은 그날 안에 써야 하며, 보존되는 것은 창고에 쌓아둔다.",
    ]

    if existing:
        listed = "\n".join(
            f"- {item.name}({item.kind}), ID {item.ingredient_id}" for item in existing
        )
        parts.append("창고에는 이미 다음 재료들이 있다. **이들과 겹치지 않게** 만든다.\n" + listed)
    if issues:
        parts.append(_feedback_section(issues))

    parts.append("신선과 보존이 둘 다 들어갔는지 확인하고 내라.")
    return "\n\n".join(parts)


_REVIEW_RULES = """\
찾을 것은 둘뿐이다.

1. **겹침(overlap)** — 두 손님이 사실상 같은 캐릭터인가. 이름과 ID가 달라도 겹칠 수 있다.
   직업이 비슷하고 말투가 같고 원하는 것이 같으면 플레이어에게는 한 사람이다.
2. **어긋남(incoherent)** — 사연과 수치가 앞뒤로 맞지 않는가. "불맛을 즐긴다"고 써놓고
   불 세기 구간이 낮은 쪽에 있으면 틀린 것이다.

**문제가 없으면 빈 배열로 낸다. 억지로 찾지 않는다.** 지적이 매번 나오면 아무도 읽지 않게 되고,
그 순간 이 검토는 값어치를 잃는다.

다음은 지적하지 않는다.
- 문법·표기·어휘 — 그쪽은 이미 코드가 검사했다
- 취향이 비어 있는 축 — 의도된 것이다. 어떤 값이든 만족한다는 뜻이다
- 평범한 사람이라는 것 — 대부분은 평범해야 특별한 손님이 특별해 보인다\
"""


def build_review_instruction() -> str:
    """검토자의 역할. 요청마다 바뀌지 않는다."""
    return (
        "너는 게임 캐릭터 설정을 검수하는 편집자다. "
        "아래 손님들이 한 식당의 손님 명단으로 쓸 만한지 본다.\n\n"
        f"{_WORLD}\n\n{_REVIEW_RULES}"
    )


def build_review_context(guests: Sequence[Guest]) -> str:
    """검토 대상. 판정에 필요한 것만 싣는다.

    한 명씩이 아니라 전부 한 번에 싣는 이유는 주 목적이 "서로 비슷한가"이기 때문이다.
    비교 대상이 같은 컨텍스트에 없으면 그 판정 자체가 불가능하다.
    """
    if not guests:
        raise DomainError("검토할 손님이 없다")

    listed = "\n\n".join(_review_entry(guest) for guest in guests)
    return f"손님 {len(guests)}명이다.\n\n{listed}"


def _review_entry(guest: Guest) -> str:
    ranges = ", ".join(
        f"{key} {ideal.low}~{ideal.high}" for key, ideal in sorted(guest.ideal_ranges.items())
    )
    return (
        f"[{guest.guest_id}] {guest.name} — {guest.title} (말투 {guest.voice})\n"
        f"  소개: {guest.bio}\n"
        f"  성격: {guest.personality}\n"
        f"  욕구: {', '.join(guest.preferred_needs)}\n"
        f"  취향: {ranges or '없음'}"
    )


def _avoid_section(existing: Sequence[Guest]) -> str:
    """이미 확정된 손님들.

    어긴 사람만 다시 뽑으면 새로 만들어지는 쪽은 남은 사람들을 모른다. 그대로 두면
    같은 직업·같은 말투가 겹쳐서, 한 명을 고치려다 중복을 새로 만든다.
    """
    listed = "\n".join(
        f"- {guest.name}({guest.title}), 말투 {guest.voice}, ID {guest.guest_id}"
        for guest in existing
    )
    return (
        "이 식당에는 이미 다음 손님들이 있다. **이들과 겹치지 않게** 만든다 — "
        f"이름·ID·직업·말투가 달라야 한다.\n{listed}"
    )


def _feedback_section(issues: Sequence[Issue]) -> str:
    """지난번에 무엇이 틀렸는지.

    Issue.message는 처음부터 모델이 읽는다고 생각하고 쓴 것이라 그대로 싣는다.
    같은 문제가 여러 명에게 났으면 한 번만 말한다 — 같은 말을 반복하면 그것만 고치고
    나머지를 놓친다.
    """
    listed = "\n".join(sorted({f"- {issue.message}" for issue in issues}))
    return f"지난번 시도가 아래를 어겼다. 같은 실수를 반복하지 않는다.\n{listed}"
