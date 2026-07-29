"""파일 출력과 생성 이력을 고정한다.

여기서 지키는 것은 둘이다 — **소비 측이 그대로 읽을 수 있는가**(파이프라인의 완료
조건), 그리고 **기록이 실패해도 생성물이 살아남는가**(기록은 보조 장치다).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from openai import AsyncOpenAI
from pydantic import BaseModel

from daily_special.adapter.outbound.llm.openai_llm import OpenAiLlm
from daily_special.adapter.outbound.package.writer import build_package, write_package
from daily_special.adapter.outbound.provenance.recorder import JsonlRecorder, NullRecorder
from daily_special.application.port.llm import Tier
from daily_special.application.port.provenance import CallOutcome
from daily_special.domain.guest import Guest
from daily_special.domain.package import SCHEMA_VERSION, Package, PackageKind
from daily_special.domain.satisfaction import IdealRange


class _Answer(BaseModel):
    text: str


def _guest() -> Guest:
    return Guest(
        guest_id="guest_test_01",
        name="테스트",
        title="시험용",
        bio="테스트에만 나온다.",
        personality="말이 없다.",
        voice="gruff",
        preferred_needs=["filling"],
        ideal_ranges={"heat": IdealRange(low=40, high=60)},
        dietary=[],
    )


# ---------------------------------------------------------------- 파일 쓰기


def test_written_package_reads_back(tmp_path: Path) -> None:
    """9단계의 완료 조건 — 서버·클라가 그대로 읽을 수 있는 파일이 나온다."""
    package = build_package(
        kind=PackageKind.GUESTS,
        items=[_guest()],
        bible_version="test.1",
        run_id="run_test_01",
    )
    path = write_package(package, tmp_path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    restored = Package[Guest].model_validate(raw)

    assert restored.items == package.items
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.bible_version == "test.1"


def test_filename_comes_from_the_kind(tmp_path: Path) -> None:
    """파일이 옮겨져도 정체를 잃지 않게 봉투의 kind가 파일명을 정한다."""
    package = build_package(
        kind=PackageKind.GUESTS, items=[_guest()], bible_version="v", run_id="r"
    )
    assert write_package(package, tmp_path).name == "guests.json"


def test_written_json_is_readable_by_humans(tmp_path: Path) -> None:
    """out/을 git에 커밋하므로 diff가 읽혀야 한다 (계약 4절)."""
    package = build_package(
        kind=PackageKind.GUESTS, items=[_guest()], bible_version="v", run_id="r"
    )
    text = write_package(package, tmp_path).read_text(encoding="utf-8")

    assert "테스트" in text, "한글이 이스케이프됐다 — diff를 읽을 수 없다"
    assert "\n  " in text, "들여쓰기가 없다 — 한 줄짜리 diff가 된다"
    assert text.endswith("\n")


def test_missing_directory_is_created(tmp_path: Path) -> None:
    package = build_package(
        kind=PackageKind.GUESTS, items=[_guest()], bible_version="v", run_id="r"
    )
    target = tmp_path / "없던" / "경로"

    assert write_package(package, target).exists()


# ---------------------------------------------------------------- 생성 이력


def _outcome(**overrides: Any) -> CallOutcome:
    data: dict[str, Any] = {
        "ts": datetime.now(UTC),
        "tier": "quality",
        "model_id": "gpt-5.6-terra",
        "instruction_hash": "abc123",
        "schema_name": "GeneratedGuestBatch",
        "input_tokens": 100,
        "output_tokens": 50,
        "latency_ms": 1200,
        "attempt": 1,
        "outcome": "ok",
    }
    data.update(overrides)
    return CallOutcome.model_validate(data)


def test_records_are_appended_one_per_line(tmp_path: Path) -> None:
    """JSONL인 이유는 붙여쓰기가 안전해서다. 중간에 끊겨도 그때까지가 온전히 남는다."""
    recorder = JsonlRecorder(tmp_path, "run_test_01")
    recorder.record(_outcome())
    recorder.record(_outcome(outcome="retry", attempt=2))

    lines = recorder.path.read_text(encoding="utf-8").strip().split("\n")

    assert len(lines) == 2
    assert json.loads(lines[1])["outcome"] == "retry"


def test_prompt_body_is_stored_apart(tmp_path: Path) -> None:
    """로그가 프롬프트로 뒤덮이면 읽을 수 없다 (규약 9절)."""
    recorder = JsonlRecorder(tmp_path, "run_test_01")
    recorder.record(_outcome())
    recorder.save_prompt("abc123", "긴 지시문", "긴 맥락")

    assert "긴 지시문" not in recorder.path.read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "prompts" / "abc123.json").read_text(encoding="utf-8"))
    assert saved["instruction"] == "긴 지시문"


def test_same_prompt_is_saved_once(tmp_path: Path) -> None:
    """지시문은 요청마다 같다. 자리 80개면 같은 파일을 80번 쓸 이유가 없다."""
    recorder = JsonlRecorder(tmp_path, "run_test_01")
    recorder.save_prompt("abc123", "지시문", "맥락 1")
    recorder.save_prompt("abc123", "지시문", "맥락 2")

    saved = json.loads((tmp_path / "prompts" / "abc123.json").read_text(encoding="utf-8"))
    assert saved["context"] == "맥락 1"


def test_recording_failure_does_not_break_generation(tmp_path: Path) -> None:
    """기록은 보조 장치다. 여기서 예외를 올리면 돈을 들여 만든 생성물을 잃는다."""
    recorder = JsonlRecorder(tmp_path, "run_test_01")
    recorder.path.parent.chmod(0o500)
    try:
        recorder.record(_outcome())  # 예외가 나오면 안 된다
    finally:
        recorder.path.parent.chmod(0o700)


# ---------------------------------------------------------------- 어댑터 연결


class _StubResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from types import SimpleNamespace

        return SimpleNamespace(
            output_parsed=_Answer(text="좋다"),
            usage=SimpleNamespace(input_tokens=1234, output_tokens=567),
        )


class _StubClient:
    def __init__(self) -> None:
        self.responses = _StubResponses()


class _SpyRecorder:
    def __init__(self) -> None:
        self.records: list[CallOutcome] = []
        self.prompts: list[str] = []

    def record(self, call: CallOutcome) -> None:
        self.records.append(call)

    def save_prompt(self, instruction_hash: str, instruction: str, context: str) -> None:
        self.prompts.append(instruction_hash)


async def test_adapter_records_tokens_and_tier() -> None:
    """이 로그가 「AI 활용 기술 문서」의 실측 데이터가 된다 — 호출 수·비용·재생성률."""
    spy = _SpyRecorder()
    adapter = OpenAiLlm(cast(AsyncOpenAI, _StubClient()), provenance=spy)

    await adapter.generate(instruction="지시", context="맥락", schema=_Answer, tier=Tier.QUALITY)

    assert len(spy.records) == 1
    record = spy.records[0]
    assert record.input_tokens == 1234
    assert record.output_tokens == 567
    assert record.tier == "quality"
    assert record.outcome == "ok"
    assert record.schema_name == "_Answer"


async def test_adapter_stores_the_prompt_body_by_hash() -> None:
    spy = _SpyRecorder()
    adapter = OpenAiLlm(cast(AsyncOpenAI, _StubClient()), provenance=spy)

    await adapter.generate(instruction="지시", context="맥락", schema=_Answer, tier=Tier.FAST)

    assert spy.prompts == [spy.records[0].instruction_hash]


async def test_adapter_works_without_a_recorder() -> None:
    """기록은 선택이다. 테스트와 실호출 없는 실행이 기본값으로 돈다."""
    adapter = OpenAiLlm(cast(AsyncOpenAI, _StubClient()), provenance=NullRecorder())

    result = await adapter.generate(
        instruction="지시", context="맥락", schema=_Answer, tier=Tier.QUALITY
    )

    assert result.text == "좋다"
