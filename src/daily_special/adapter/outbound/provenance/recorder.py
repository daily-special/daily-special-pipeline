"""생성 이력 기록 (규약 9절).

JSONL로 남기는 이유는 붙여쓰기가 안전하고 사람이 grep할 수 있기 때문이다. 생성이
중간에 끊겨도 그때까지의 기록이 온전히 남는다.

**기록 실패가 생성을 멈추지 않는다.** 이것은 보조 장치이고, 여기서 예외를 올리면
돈을 들여 만든 생성물을 로그 때문에 잃는다.
"""

import contextlib
import json
from pathlib import Path

from daily_special.application.port.provenance import CallOutcome


class JsonlRecorder:
    """`out/provenance/<run_id>.jsonl`에 한 줄씩 붙인다."""

    def __init__(self, root: Path, run_id: str) -> None:
        self._path = root / f"{run_id}.jsonl"
        self._prompt_dir = root / "prompts"
        self._saved: set[str] = set()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prompt_dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def record(self, call: CallOutcome) -> None:
        line = call.model_dump_json()
        # 기록은 보조 장치다. 여기서 멈추면 이미 만든 생성물을 잃는다.
        with contextlib.suppress(OSError), self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def save_prompt(self, instruction_hash: str, instruction: str, context: str) -> None:
        if instruction_hash in self._saved:
            return
        self._saved.add(instruction_hash)

        payload = {"instruction": instruction, "context": context}
        with contextlib.suppress(OSError):
            (self._prompt_dir / f"{instruction_hash}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


class NullRecorder:
    """아무것도 남기지 않는다. 테스트와 실호출 없는 실행의 기본값이다."""

    def record(self, call: CallOutcome) -> None:
        return None

    def save_prompt(self, instruction_hash: str, instruction: str, context: str) -> None:
        return None
