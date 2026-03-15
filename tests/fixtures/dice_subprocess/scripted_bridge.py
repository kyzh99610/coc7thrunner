from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from coc_runner.domain.dice import D100Roll, RollOutcome  # noqa: E402


def _build_roll(*, total: int, target: int, outcome: str) -> D100Roll:
    selected_tens = 0 if total == 100 else total // 10
    unit_die = 0 if total == 100 else total % 10
    return D100Roll(
        unit_die=unit_die,
        tens_dice=[selected_tens],
        selected_tens=selected_tens,
        total=total,
        target=target,
        outcome=RollOutcome(outcome),
    )


def main() -> int:
    payload = json.loads(sys.stdin.read())
    request = payload["request"]
    label = str(request["label"])
    target = int(request["target_value"])
    if label == "图书馆使用":
        roll = _build_roll(total=24, target=target, outcome="hard_success")
    elif label == "教育":
        roll = _build_roll(total=35, target=target, outcome="hard_success")
    else:
        roll = _build_roll(total=55, target=target, outcome="success")
    sys.stdout.write(
        json.dumps(
            {
                "backend_name": "dice_style_subprocess_bridge",
                "roll": roll.model_dump(mode="json"),
                "success": roll.total <= target,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
