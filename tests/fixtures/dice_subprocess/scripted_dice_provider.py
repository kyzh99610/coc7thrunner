from __future__ import annotations

import sys


def main() -> int:
    command_text = sys.stdin.read().strip()
    if command_text == ".rc 图书馆使用70":
        print('<测试调查员>的"图书馆使用"检定结果为: D100=24/70 困难成功')
        return 0
    if command_text == ".rc 教育75":
        print('<测试调查员>的"教育"检定结果为: D100=35/75 困难成功')
        return 0
    print('<测试调查员>的"默认检定"检定结果为: D100=55/80 成功')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
