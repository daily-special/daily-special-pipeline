"""생성 진입점.

    make generate                     # 전부
    uv run daily-special --kind lines # 대사만

**종류를 나눠 돌릴 수 있어야 한다.** 대사가 자리 80개라 전체 호출의 대부분이고,
중간에 끊겼을 때 처음부터 다시 도는 것은 그대로 돈이다.

요리는 재료를 참조하므로 순서가 있다. `--kind all`은 재료를 먼저 만들고 그 결과를
요리에 넘긴다. 요리만 따로 돌릴 때는 이미 나와 있는 `ingredients.json`을 읽는다.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from daily_special.adapter.outbound.config.loader import load_bible
from daily_special.adapter.outbound.llm.openai_llm import OpenAiLlm
from daily_special.adapter.outbound.package.writer import build_package, write_package
from daily_special.adapter.outbound.provenance.recorder import JsonlRecorder
from daily_special.application.generate_dishes import generate_dishes
from daily_special.application.generate_guests import generate_guests
from daily_special.application.generate_ingredients import generate_ingredients
from daily_special.application.generate_lines import generate_lines, plan_slots
from daily_special.common.errors import ConfigError, DailySpecialError
from daily_special.domain.bible import ProjectBible
from daily_special.domain.ingredient import Ingredient
from daily_special.domain.issue import Issue, Severity
from daily_special.domain.line import check_line_coverage
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BIBLE = _REPO_ROOT / "data" / "project_bible.json"
_DEFAULT_OUT = _REPO_ROOT / "out" / "packages" / SCHEMA_VERSION
_DEFAULT_PROVENANCE = _REPO_ROOT / "out" / "provenance"

_KINDS = ("guests", "ingredients", "dishes", "lines")


def main() -> int:
    parser = argparse.ArgumentParser(description="「오늘의 정식」 콘텐츠 생성")
    parser.add_argument("--kind", choices=(*_KINDS, "all"), default="all")
    parser.add_argument("--guests", type=int, default=8)
    parser.add_argument("--ingredients", type=int, default=12)
    parser.add_argument("--dishes", type=int, default=10)
    parser.add_argument(
        "--line-slots",
        type=int,
        default=0,
        help="채울 대사 자리 수. 0이면 전부 (자리 하나가 호출 하나다)",
    )
    parser.add_argument("--bible", type=Path, default=_DEFAULT_BIBLE)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)

    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except DailySpecialError as error:
        print(f"실패: {error}")
        return 1


async def _run(args: argparse.Namespace) -> int:
    bible = load_bible(args.bible)
    run_id = f"run_{datetime.now(UTC):%Y%m%d_%H%M%S}"
    recorder = JsonlRecorder(_DEFAULT_PROVENANCE, run_id)
    llm = OpenAiLlm(provenance=recorder)

    wanted = _KINDS if args.kind == "all" else (args.kind,)
    issues: list[Issue] = []
    calls = 0

    print(f"run_id {run_id} / 설정 {bible.version}")

    if "guests" in wanted:
        result = await generate_guests(llm=llm, bible=bible, count=args.guests)
        calls += result.call_count
        issues += _tag("guests", result.issues)
        _emit(PackageKind.GUESTS, result.guests, bible, run_id, args.out, result.call_count)

    ingredients: list[Ingredient] = []
    if "ingredients" in wanted:
        made = await generate_ingredients(llm=llm, bible=bible, count=args.ingredients)
        calls += made.call_count
        issues += _tag("ingredients", made.issues)
        ingredients = made.ingredients
        _emit(PackageKind.INGREDIENTS, ingredients, bible, run_id, args.out, made.call_count)

    if "dishes" in wanted:
        if not ingredients:
            ingredients = _read_ingredients(args.out)
        result_d = await generate_dishes(
            llm=llm, bible=bible, ingredients=ingredients, count=args.dishes
        )
        calls += result_d.call_count
        issues += _tag("dishes", result_d.issues)
        _emit(PackageKind.DISHES, result_d.dishes, bible, run_id, args.out, result_d.call_count)

    if "lines" in wanted:
        slots = plan_slots(bible)
        if args.line_slots > 0:
            slots = slots[: args.line_slots]
        print(f"  대사 자리 {len(slots)}개 — 호출도 그만큼 든다")

        result_l = await generate_lines(llm=llm, bible=bible, slots=slots)
        calls += result_l.call_count
        issues += _tag("lines", result_l.issues)
        # 빈 자리는 파싱도 테스트도 통과하는데 런타임엔 손님이 입을 다문다.
        issues += _tag("lines", check_line_coverage(result_l.lines, bible))
        _emit(PackageKind.LINES, result_l.lines, bible, run_id, args.out, result_l.call_count)

    return _report(issues, calls, recorder.path)


def _emit[T: BaseModel](
    kind: PackageKind,
    items: list[T],
    bible: ProjectBible,
    run_id: str,
    out: Path,
    calls: int,
) -> None:
    package = build_package(
        kind=kind,
        items=items,
        bible_version=bible.version,
        run_id=run_id,
    )
    path = write_package(package, out)
    print(f"  {kind}: {len(items)}개, 호출 {calls}회 → {path}")


def _read_ingredients(out: Path) -> list[Ingredient]:
    """요리만 따로 돌릴 때. 재료가 없으면 요리를 만들 수 없다."""
    path = out / "ingredients.json"
    if not path.exists():
        raise ConfigError(
            f"{path}가 없다. 요리는 재료를 참조하므로 재료를 먼저 만든다 (--kind ingredients)"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(Package[Ingredient].model_validate(raw).items)


def _tag(kind: str, issues: list[Issue]) -> list[Issue]:
    """어느 파일의 문제인지 알아볼 수 있게 앞에 붙인다."""
    return [issue.model_copy(update={"field": f"{kind}.{issue.field}"}) for issue in issues]


def _report(issues: list[Issue], calls: int, log: Path) -> int:
    """**경고가 있어도 실패로 다루지 않는다** (규약 5-3).

    생성물은 이미 파일로 나갔다. 여기서 실패를 반환해 봐야 만든 것이 사라지지 않고,
    사람이 로그를 보고 판단하면 된다. ERROR만 종료 코드로 알린다.
    """
    errors = [issue for issue in issues if issue.severity is Severity.ERROR]
    warnings = [issue for issue in issues if issue.severity is Severity.WARNING]

    print(f"\nLLM 호출 {calls}회 / 이력 {log}")
    for issue in errors:
        print(f"  [ERROR]   {issue.field}: {issue.message}")
    for issue in warnings:
        print(f"  [warning] {issue.field}: {issue.message}")

    if not issues:
        print("  문제 없음")

    return 1 if errors else 0
