"""题目存储服务（Step 1）。

按实验要求：每题一个 JSON 文件，存于 data/problems/。
设计要点：
- 所有磁盘 IO 通过 asyncio.to_thread 放入线程池，接口保持全程异步；
- 读写均经 Pydantic 校验，损坏的配置文件抛出明确错误；
- 后续若要迁移到数据库存储，只需替换本类实现，路由层无需改动。
"""
import asyncio
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from app import config
from app.core.errors import ApiError
from app.schemas.problem import PROBLEM_ID_RE, ProblemConfig


class ProblemStore:
    """基于 JSON 文件的题目存储实现。"""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or config.PROBLEMS_DIR
        self._lock = threading.RLock()

    def _path(self, problem_id: str) -> Path:
        if not re.fullmatch(PROBLEM_ID_RE, problem_id):
            raise ApiError(400, "invalid problem id")
        path = self.base_dir / f"{problem_id}.json"
        if path.is_symlink():
            raise ApiError(400, "invalid problem path")
        return path

    async def _locked(self, operation):
        def run():
            with self._lock:
                return operation()
        return await asyncio.to_thread(run)

    async def list_problems(self) -> list[dict]:
        """返回 [{id, title}]，供题目列表页使用。"""
        def _read() -> list[dict]:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            items: list[dict] = []
            for path in sorted(self.base_dir.glob("*.json")):
                try:
                    data = ProblemConfig.model_validate_json(path.read_text(encoding="utf-8"))
                except (ValueError, ValidationError):
                    continue   # 损坏的配置文件跳过，不影响整体列表
                if path.stem == data.id and not path.is_symlink():
                    items.append({"id": data.id, "title": data.title})
            return items

        return await self._locked(_read)

    async def get(self, problem_id: str, *, for_judge: bool = False) -> dict:
        """返回题目全字段；可选字段缺失时填充默认值。"""
        def _read() -> dict:
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                # exclude_none：未提供编号的测试点不带 "id": null，与 api.md 示例一致
                data = ProblemConfig.model_validate(raw).model_dump(exclude_none=True)
                if data["id"] != problem_id:
                    raise ValueError("problem id mismatch")
                if for_judge:
                    # API 展示系统默认值；评测仍须区分“未设置”，以回退到语言限制。
                    for field in ("time_limit", "memory_limit"):
                        if field not in raw:
                            data.pop(field, None)
                return data
            except (ValueError, ValidationError):
                # 损坏的配置文件：视为服务器数据异常（500），不向客户端泄露内部细节
                raise ApiError(500, f"problem config corrupted: {problem_id}")

        return await self._locked(_read)

    async def create(self, cfg: ProblemConfig) -> None:
        def _write() -> None:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(cfg.id)
            try:
                # "x" 独占创建：并发提交同一 id 时恰好一个成功，其余稳定返回 409
                with open(path, "x", encoding="utf-8") as f:
                    f.write(self._dumps(cfg))
            except FileExistsError:
                raise ApiError(409, "problem already exists")

        await self._locked(_write)

    async def update(self, cfg: ProblemConfig, *, allow_visibility: bool = True) -> None:
        def _write() -> None:
            path = self._path(cfg.id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            raw = json.loads(path.read_text(encoding="utf-8"))
            public = bool(raw.get("public_cases", False))
            if "public_cases" in cfg.model_fields_set and cfg.public_cases != public:
                if not allow_visibility:
                    raise ApiError(403, "admin permission required to change log visibility")
            else:
                # 普通题目编辑不应隐式重置管理员设置的可见性。
                cfg.public_cases = public
            self._replace(path, self._dumps(cfg))

        await self._locked(_write)

    async def delete(self, problem_id: str) -> None:
        def _delete() -> None:
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            path.unlink()

        await self._locked(_delete)

    async def set_public_cases(self, problem_id: str, public_cases: bool) -> None:
        """Step 5：设置测试点明细是否对普通用户可见。"""
        def _write():
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            cfg = ProblemConfig.model_validate_json(path.read_text(encoding="utf-8"))
            cfg.public_cases = public_cases
            self._replace(path, self._dumps(cfg))
        await self._locked(_write)

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        # 同目录临时文件 + rename，避免读到被截断或只写了一半的 JSON。
        fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    @staticmethod
    def _dumps(cfg: ProblemConfig) -> str:
        data = cfg.model_dump(exclude_none=True)
        for field in ("time_limit", "memory_limit"):
            if field not in cfg.model_fields_set:
                data.pop(field, None)
        return json.dumps(data, ensure_ascii=False, indent=2)


# 全局单例：路由层直接使用
store = ProblemStore()
