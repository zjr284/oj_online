"""题目存储服务（Step 1）。

按实验要求：每题一个 JSON 文件，存于 data/problems/。
设计要点：
- 所有磁盘 IO 通过 asyncio.to_thread 放入线程池，接口保持全程异步；
- 读写均经 Pydantic 校验，损坏的配置文件抛出明确错误；
- 后续若要迁移到数据库存储，只需替换本类实现，路由层无需改动。
"""
import asyncio
import json
from pathlib import Path

from pydantic import ValidationError

from app import config
from app.core.errors import ApiError
from app.schemas.problem import ProblemConfig


class ProblemStore:
    """基于 JSON 文件的题目存储实现。"""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or config.PROBLEMS_DIR

    def _path(self, problem_id: str) -> Path:
        return self.base_dir / f"{problem_id}.json"

    async def list_problems(self) -> list[dict]:
        """返回 [{id, title}]，供题目列表页使用。"""
        def _read() -> list[dict]:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            items: list[dict] = []
            for path in sorted(self.base_dir.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue   # 损坏的配置文件跳过，不影响整体列表
                if "id" in data and "title" in data:
                    items.append({"id": data["id"], "title": data["title"]})
            return items

        return await asyncio.to_thread(_read)

    async def get(self, problem_id: str) -> dict:
        """返回题目全字段；可选字段缺失时填充默认值。"""
        def _read() -> dict:
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            raw = json.loads(path.read_text(encoding="utf-8"))
            try:
                return ProblemConfig.model_validate(raw).model_dump()
            except ValidationError:
                raise ApiError(500, f"problem config corrupted: {problem_id}")

        return await asyncio.to_thread(_read)

    async def create(self, cfg: ProblemConfig) -> None:
        def _write() -> None:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(cfg.id)
            if path.is_file():
                raise ApiError(409, "problem already exists")
            path.write_text(self._dumps(cfg), encoding="utf-8")

        await asyncio.to_thread(_write)

    async def update(self, cfg: ProblemConfig) -> None:
        def _write() -> None:
            path = self._path(cfg.id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            path.write_text(self._dumps(cfg), encoding="utf-8")

        await asyncio.to_thread(_write)

    async def delete(self, problem_id: str) -> None:
        def _delete() -> None:
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            path.unlink()

        await asyncio.to_thread(_delete)

    async def set_public_cases(self, problem_id: str, public_cases: bool) -> None:
        """Step 5：设置测试点明细是否对普通用户可见。"""
        cfg = ProblemConfig.model_validate(await self.get(problem_id))
        cfg.public_cases = public_cases
        await self.update(cfg)

    @staticmethod
    def _dumps(cfg: ProblemConfig) -> str:
        return json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2)


# 全局单例：路由层直接使用
store = ProblemStore()
