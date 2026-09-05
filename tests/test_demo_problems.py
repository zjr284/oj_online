"""校验实际随仓库发布的题库，并用大规模数据区分二分与线性扫描。"""
import bisect
import json
from pathlib import Path

import pytest

from app.judge.runner import JudgeRunner, OutputComparer
from app.schemas.problem import ProblemConfig
from scripts.build_find_range_stress import build_stress_problem

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('problem_id', ['P1001', 'sum_2', 'find_range'])
def test_shipped_problem_samples_and_cases(problem_id):
    problem = ProblemConfig.model_validate_json(
        (ROOT / 'data/problems' / f'{problem_id}.json').read_text()).model_dump()
    for case in problem['samples'] + problem['testcases']:
        values = list(map(int, case['input'].split()))
        if problem_id in ('P1001', 'sum_2'):
            assert len(values) == 2
            assert all(-10**9 <= value <= 10**9 for value in values)
            answer = str(sum(values))
        else:
            n, q = values[:2]
            array, queries = values[2:n+2], values[n+2:]
            assert 1 <= n <= 10**6 and 1 <= q <= 10**5
            assert len(array) == n and len(queries) == q
            assert array == sorted(array)
            assert all(-10**9 <= value <= 10**9 for value in array + queries)
            answer = '\n'.join(f'{bisect.bisect_left(array, x) + 1} {bisect.bisect_right(array, x)}'
                               if x in array else '-1 -1' for x in queries)
        assert OutputComparer.compare(answer, case['output'])[0]


PYTHON_BINARY = '''import sys, bisect
v=list(map(int, sys.stdin.buffer.read().split()))
n,q=v[:2]
a=v[2:n+2]
answer=[]
for x in v[n+2:]:
    l=bisect.bisect_left(a,x)
    r=bisect.bisect_right(a,x)
    answer.append(f'{l+1} {r}' if l<r else '-1 -1')
print('\\n'.join(answer))
'''
CPP_BINARY = '''#include <algorithm>
#include <iostream>
#include <vector>
int main() {
    std::ios::sync_with_stdio(false); std::cin.tie(nullptr);
    int n,q,x; std::cin >> n >> q;
    std::vector<int> a(n); for(auto &v:a) std::cin >> v;
    while(q--) {
        std::cin >> x;
        auto l=std::lower_bound(a.begin(),a.end(),x), r=std::upper_bound(a.begin(),a.end(),x);
        if(l==r) std::cout << "-1 -1\\n";
        else std::cout << (l-a.begin()+1) << " " << (r-a.begin()) << "\\n";
    }
}
'''
PYTHON_LINEAR = '''import sys
v=list(map(int, sys.stdin.buffer.read().split()))
n,q=v[:2]
a=v[2:n+2]
for x in v[n+2:]:
    indexes=[i+1 for i,value in enumerate(a) if value==x]
    print(indexes[0],indexes[-1]) if indexes else print('-1 -1')
'''


@pytest.mark.parametrize('language,code,expected', [
    ({'file_ext': '.py', 'run_cmd': 'python3 {src}'}, PYTHON_BINARY, 'AC'),
    ({'file_ext': '.cpp', 'compile_cmd': 'g++ -O2 -std=c++14 {src} -o {exe}',
      'run_cmd': '{exe}'}, CPP_BINARY, 'AC'),
    ({'file_ext': '.py', 'run_cmd': 'python3 {src}'}, PYTHON_LINEAR, 'TLE'),
])
async def test_generated_stress_distinguishes_algorithms(tmp_path, language, code, expected):
    problem = build_stress_problem()
    engine = JudgeRunner(language, problem, tmp_path)
    compiled, message = await engine.compile(code)
    assert compiled, message
    result = await engine.run_case(problem['testcases'][-1], 6)
    assert result.result == expected, result.detail
