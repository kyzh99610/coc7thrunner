from __future__ import annotations

import sys


def main() -> int:
    command_text = sys.stdin.read().strip()
    if command_text == ".rc 图书馆使用70":
        print('<测试调查员>的"图书馆使用"检定结果为: D100=24/70 困难成功')
        return 0
    if command_text == ".ra b2 图书馆使用70":
        print('<测试调查员>的"图书馆使用"检定结果为: b2=15/70, ([D100=75, 奖励 1 5]) 困难成功!')
        return 0
    if command_text == ".ra p1 图书馆使用70":
        print('<测试调查员>的"图书馆使用"检定结果为: p=84/70, ([D100=24, 惩罚 8]) 失败')
        return 0
    if command_text == ".rc 教育75":
        print('<测试调查员>的"教育"检定结果为: D100=35/75 困难成功')
        return 0
    if command_text == ".ra b1 教育75":
        print('<测试调查员>的"教育"检定结果为: b=12/75, ([D100=52, 奖励 1]) 极难成功!')
        return 0
    if command_text == ".ra p2 教育75":
        print('<测试调查员>的"教育"检定结果为: p2=95/75, ([D100=25, 惩罚 9 7]) 失败')
        return 0
    if command_text == ".ra b1 话术50":
        print('<测试调查员>的"话术"检定结果为: b=24/50, ([D100=74, 奖励 2]) 成功')
        return 0
    print('<测试调查员>的"默认检定"检定结果为: D100=55/80 成功')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
