"""生成查找区间的大规模测试配置；大数据留在忽略目录，不提交到 Git。

运行：.venv/bin/python scripts/build_find_range_stress.py
输出可经题目编辑 API 导入；不会改写已有题库文件。
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build_stress_problem(n=200_000, q=50_000):
    if not 2 <= n <= 1_000_000 or not 1 <= q <= 100_000:
        raise ValueError('require 2 <= n <= 1000000 and 1 <= q <= 100000')
    problem = json.loads((ROOT / 'data/problems/find_range.json').read_text(encoding='utf-8'))
    # 中间缺失值迫使逐元素搜索检查整个数组，不能仅用首尾范围判断提前退出。
    middle = n // 2
    array = ' '.join(['6'] * middle + ['8'] * (n - middle))
    queries, expected = [], []
    for i in range(q):
        if i % 100 == 0:
            queries.append('6')
            expected.append(f'1 {middle}')
        elif i % 100 == 1:
            queries.append('8')
            expected.append(f'{middle + 1} {n}')
        else:
            queries.append('7')
            expected.append('-1 -1')
    problem['testcases'].append({
        'id': 'stress',
        'input': f'{n} {q}\n{array}\n' + '\n'.join(queries) + '\n',
        'output': '\n'.join(expected) + '\n',
    })
    return problem


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/generated/find_range.json')
    parser.add_argument('--n', type=int, default=200_000)
    parser.add_argument('--q', type=int, default=50_000)
    args = parser.parse_args()
    problem = build_stress_problem(args.n, args.q)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(problem, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Generated {len(problem["testcases"])} cases: {args.output}')


if __name__ == '__main__':
    main()
