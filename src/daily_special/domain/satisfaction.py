"""만족도 엔진 — 이 저장소의 심장.

    만족 = 욕구 충족도 × 취향 일치도 × 예산 적합 × 식이 계수

순수 함수다. LLM도, 파일도, 시각도 읽지 않는다. 같은 입력에 항상 같은 출력이 나온다.

**이 모듈은 레퍼런스 구현이다.** 게임 런타임(클라 또는 서버)이 이 계산을 다른 언어로
이식하므로, 여기의 테스트가 곧 이식 명세다. 연산 순서를 바꾸면 부동소수점 결과가
달라질 수 있으니 순서를 유지한다.

GuestPersona가 읽는 필드 목록이 곧 페르소나 스키마의 초안이다. 엔진이 읽지 않는 필드는
페르소나에 있을 이유가 없고, 엔진이 읽는데 없는 필드는 반드시 있어야 한다.
"""

from pydantic import BaseModel, ConfigDict, model_validator

from daily_special.common.errors import DomainError
from daily_special.domain.bible import ProjectBible


class IdealRange(BaseModel):
    """한 축에서 이 손님이 만족하는 구간.

    슬라이더 전체 범위(AxisSpec)와 다른 것이다. 이쪽이 훨씬 좁다.
    """

    model_config = ConfigDict(frozen=True)

    low: int
    high: int

    @model_validator(mode="after")
    def _check_order(self) -> "IdealRange":
        if self.low > self.high:
            raise ValueError(f"이상 구간이 뒤집혔다: {self.low} > {self.high}")
        return self


class GuestPersona(BaseModel):
    """손님의 고정된 부분. 관계가 깊어지면 플레이어에게 공개된다."""

    model_config = ConfigDict(frozen=True)

    ideal_ranges: dict[str, IdealRange]
    """축 키 → 이상 구간. 취향이 없는 축은 넣지 않는다 (어떤 값이든 만족)."""

    dietary: list[str] = []
    """식이 제약 키 목록."""


class VisitState(BaseModel):
    """이번 방문에만 해당하는 것. 매 방문 다시 만들어진다.

    이것을 만드는 것은 게임 런타임의 일이다 — 허기·컨디션·기분이 욕구를 낳는 규칙은
    이 저장소가 아니라 서버가 갖는다. 엔진은 결과만 받는다.
    """

    model_config = ConfigDict(frozen=True)

    needs: list[str]
    """오늘의 욕구 1~2개. 가중치는 없다 — 전부 동등하다."""

    wallet: int
    """오늘 낼 수 있는 돈."""


class ServedDish(BaseModel):
    """플레이어가 내놓은 한 접시."""

    model_config = ConfigDict(frozen=True)

    need_tags: list[str]
    """이 요리가 답하는 욕구들. 손님 욕구와 같은 어휘를 쓴다."""

    price: int

    dietary_conflicts: list[str] = []
    """이 요리가 저촉되는 식이 제약 키. 재료에서 유도되지만 엔진은 결과만 받는다."""

    params: dict[str, int]
    """축 키 → 플레이어가 맞춘 값."""


class AxisScore(BaseModel):
    """축 하나의 채점 결과. 정보 공개 곡선의 "좀 짠데요"가 여기서 나온다."""

    model_config = ConfigDict(frozen=True)

    axis: str
    value: int
    score: float

    distance: int
    """이상 구간에서 벗어난 거리. 0이면 구간 안이다."""

    direction: int
    """-1이면 손님 취향보다 낮고, +1이면 높다. 0이면 구간 안.

    이 값이 피드백 대사를 고른다 — seasoning이 +1이면 "좀 짠데요".
    """


class Satisfaction(BaseModel):
    """만족도와 그 근거.

    단일 숫자로 돌려주지 않는 이유는 셋이다. 피드백 대사가 축별 점수에서 나오고,
    밸런스 시뮬레이션이 어느 항이 병목인지 봐야 하고, 런타임 이식본이 레퍼런스와
    같은지 항별로 대조할 수 있어야 한다.
    """

    model_config = ConfigDict(frozen=True)

    total: float

    need_score: float
    taste_score: float
    budget_score: float
    dietary_factor: float

    axis_scores: list[AxisScore]
    unmet_needs: list[str]
    violated_dietary: list[str]


def evaluate(
    *,
    persona: GuestPersona,
    state: VisitState,
    dish: ServedDish,
    bible: ProjectBible,
) -> Satisfaction:
    """한 접시에 대한 만족도를 계산한다."""
    need_score, unmet = _score_needs(state, dish, bible)
    axis_scores = _score_axes(persona, dish, bible)
    taste_score = _average_axis_score(axis_scores)
    budget_score = _score_budget(state, dish, bible)
    dietary_factor, violated = _dietary_factor(persona, dish, bible)

    total = need_score * taste_score * budget_score * dietary_factor

    return Satisfaction(
        total=total,
        need_score=need_score,
        taste_score=taste_score,
        budget_score=budget_score,
        dietary_factor=dietary_factor,
        axis_scores=axis_scores,
        unmet_needs=unmet,
        violated_dietary=violated,
    )


def _score_needs(
    state: VisitState, dish: ServedDish, bible: ProjectBible
) -> tuple[float, list[str]]:
    """오늘의 욕구 중 요리가 덮은 비율. 바닥값 아래로 내려가지 않는다.

    욕구가 1개인 손님은 비율이 0/1 이분법이 되어, 빗나가면 아무리 잘 만들어도
    전체가 0이 된다. 바닥값이 그것을 막는다 — "원하던 건 아니지만 밥은 먹었다".
    """
    if not state.needs:
        return 1.0, []

    tags = set(dish.need_tags)
    unmet = [need for need in state.needs if need not in tags]
    ratio = (len(state.needs) - len(unmet)) / len(state.needs)

    return max(ratio, bible.scoring.need_floor), unmet


def _score_axes(persona: GuestPersona, dish: ServedDish, bible: ProjectBible) -> list[AxisScore]:
    """축별로 이상 구간에서 벗어난 거리를 점수로 바꾼다.

    구간 안이면 1.0, 밖은 허용 오차까지 선형으로 감소하고, 넘으면 0이다.
    선형인 이유는 플레이어가 피드백으로 방향과 크기를 추론할 수 있어야 하고,
    다른 언어로 이식할 때 결과가 어긋날 여지가 없어야 하기 때문이다.
    """
    tolerance = bible.scoring.axis_tolerance
    scores: list[AxisScore] = []

    for axis in bible.axes:
        ideal = persona.ideal_ranges.get(axis.key)
        if ideal is None:
            continue  # 이 축에는 취향이 없다. 어떤 값이든 만족한다

        if axis.key not in dish.params:
            raise DomainError(
                f"요리에 축 '{axis.key}'의 값이 없다. 손님은 이 축에 취향을 갖고 있다"
            )

        value = dish.params[axis.key]
        if value < ideal.low:
            distance, direction = ideal.low - value, -1
        elif value > ideal.high:
            distance, direction = value - ideal.high, 1
        else:
            distance, direction = 0, 0

        score = 1.0 if distance == 0 else max(0.0, 1.0 - distance / tolerance)
        scores.append(
            AxisScore(
                axis=axis.key,
                value=value,
                score=score,
                distance=distance,
                direction=direction,
            )
        )

    return scores


def _average_axis_score(axis_scores: list[AxisScore]) -> float:
    """축 사이는 곱이 아니라 평균이다.

    상위 공식이 이미 곱셈이라 축 간에도 곱하면 감쇠가 여섯 번 겹친다. 세 축이 각각
    0.7일 때 곱은 0.34, 평균은 0.7이다. 곱셈이면 웬만큼 잘 만들어도 바닥이라
    플레이어가 개선을 체감하지 못한다. 취향 일치도는 "얼마나 잘 만들었나" 하나의
    값이므로 축 사이는 평균이 의미상으로도 맞다.
    """
    if not axis_scores:
        return 1.0  # 취향이 없는 손님. 파라미터로는 실패할 수 없다
    return sum(score.score for score in axis_scores) / len(axis_scores)


def _score_budget(state: VisitState, dish: ServedDish, bible: ProjectBible) -> float:
    """지갑으로 감당되는가. 가격/지갑 비율만 쓰므로 화폐의 절대 스케일과 무관하다.

    이분법으로 두면 1원 차이에 만족이 통째로 0이 되어 곱셈 구조에서 지나치게 가혹하다.
    지갑을 넘는 순간부터 선형으로 떨어져 지갑의 budget_overrun_ratio 배에서 0이 된다.
    """
    if dish.price <= state.wallet:
        return 1.0
    if state.wallet <= 0:
        return 0.0

    zero_at = state.wallet * bible.scoring.budget_overrun_ratio
    if dish.price >= zero_at:
        return 0.0

    return (zero_at - dish.price) / (zero_at - state.wallet)


def _dietary_factor(
    persona: GuestPersona, dish: ServedDish, bible: ProjectBible
) -> tuple[float, list[str]]:
    """식이 위반은 뺄셈이 아니라 곱셈 계수다.

    뺄셈이면 결과가 음수가 될 수 있어 clamp가 필요하고, clamp는 "얼마나 나빴나"를
    뭉갠다. 계수로 두면 위반이 둘일 때 자연히 더 낮아지고 결과가 0~1에 머문다.
    """
    conflicts = set(dish.dietary_conflicts)
    violated = [key for key in persona.dietary if key in conflicts]

    return bible.scoring.dietary_violation_factor ** len(violated), violated
