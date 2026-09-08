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

LEGACY_PROBLEM_IDS = {"P1001": "1001", "sum_2": "1002", "find_range": "1003"}


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

    async def migrate_numeric_ids(self) -> dict[str, str]:
        """把旧的非数字题目文件安全迁移为数字编号，并返回引用映射。"""
        def _migrate() -> dict[str, str]:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            mapping = dict(LEGACY_PROBLEM_IDS)
            used = {
                path.stem
                for path in self.base_dir.glob("*.json")
                if re.fullmatch(PROBLEM_ID_RE, path.stem)
            }
            next_id = max([1000, *(int(value) for value in used)]) + 1

            paths = sorted(
                self.base_dir.glob("*.json"),
                key=lambda path: (
                    not bool(re.fullmatch(PROBLEM_ID_RE, path.stem)),
                    int(path.stem) if re.fullmatch(PROBLEM_ID_RE, path.stem) else path.stem,
                ),
            )
            for path in paths:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                old_id = str(raw.get("id", path.stem))
                if re.fullmatch(PROBLEM_ID_RE, old_id) and path.stem == old_id:
                    continue
                new_id = mapping.get(old_id)
                if not new_id or new_id in used:
                    while str(next_id) in used:
                        next_id += 1
                    new_id = str(next_id)
                    next_id += 1
                    mapping[old_id] = new_id
                used.add(new_id)
                raw["id"] = new_id
                target = self.base_dir / f"{new_id}.json"
                self._replace(target, json.dumps(raw, ensure_ascii=False, indent=2))
                if path != target:
                    path.unlink(missing_ok=True)
            return mapping

        return await self._locked(_migrate)

    async def list_problems(self) -> list[dict]:
        """返回 [{id, title}]，供题目列表页使用。"""
        def _read() -> list[dict]:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            items: list[dict] = []
            paths = sorted(
                self.base_dir.glob("*.json"),
                key=lambda path: (
                    not bool(re.fullmatch(PROBLEM_ID_RE, path.stem)),
                    int(path.stem) if re.fullmatch(PROBLEM_ID_RE, path.stem) else path.stem,
                ),
            )
            for path in paths:
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

    async def create(self, cfg: ProblemConfig, *, assign_new_id: bool = False) -> str:
        """创建题目并返回实际题号。

        普通创建保持严格判重；AI 导入等明确的“另存为新题目”场景可启用
        ``assign_new_id``，在请求题号已占用时原子选择后续可用数字题号。
        """
        def _write() -> str:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            requested_id = cfg.id
            max_problem_id = 10**18 - 1

            while True:
                requested_path = self.base_dir / f"{requested_id}.json"
                if not requested_path.exists() and not requested_path.is_symlink():
                    candidate_id = requested_id
                elif not assign_new_id:
                    candidate_id = requested_id
                else:
                    # 从原题号的下一个编号开始找。最多检查“现有文件数 + 1”个
                    # 连续编号就必然能找到空位，无需扫描整个 18 位编号空间。
                    occupied = {
                        path.stem
                        for path in self.base_dir.glob("*.json")
                        if re.fullmatch(PROBLEM_ID_RE, path.stem)
                    }
                    start = (int(requested_id) + 1) % (max_problem_id + 1)
                    candidate_id = ""
                    for offset in range(len(occupied) + 1):
                        value = (start + offset) % (max_problem_id + 1)
                        possible = str(value)
                        possible_path = self.base_dir / f"{possible}.json"
                        if (possible not in occupied and not possible_path.exists()
                                and not possible_path.is_symlink()):
                            candidate_id = possible
                            break
                    if not candidate_id:  # 实际文件系统中不可达，保留明确错误兜底。
                        raise ApiError(409, "no available problem id")

                saved = (cfg if candidate_id == cfg.id
                         else cfg.model_copy(update={"id": candidate_id}))
                path = self._path(candidate_id)
                try:
                    # "x" 独占创建：即使存在多进程竞争也绝不覆盖已有题目。
                    with open(path, "x", encoding="utf-8") as f:
                        f.write(self._dumps(saved))
                    return candidate_id
                except FileExistsError:
                    if not assign_new_id:
                        raise ApiError(409, "problem already exists")
                    # 另一个进程刚占用了候选题号，以下一编号重新分配。
                    requested_id = str((int(candidate_id) + 1) % (max_problem_id + 1))

        return await self._locked(_write)

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
