from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from coc_runner.application.dice_execution import (
    DiceExecutionResult,
    DiceStyleSubprocessPayload,
    LocalDiceExecutionBackend,
)


def main() -> int:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        print("missing dice subprocess payload", file=sys.stderr)
        return 2
    try:
        payload = DiceStyleSubprocessPayload.model_validate_json(raw_payload)
    except ValidationError as exc:
        print(exc, file=sys.stderr)
        return 2

    backend = LocalDiceExecutionBackend()
    result = backend.execute_check(payload.request).model_copy(
        update={"backend_name": "dice_style_subprocess_bridge"}
    )
    sys.stdout.write(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
