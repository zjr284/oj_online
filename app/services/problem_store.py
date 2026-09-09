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


def _problem_sort_key(path: Path) -> tuple:
    """数字题号按数值排序，其余合法字符串题号按字典序排序。"""
    return (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem.casefold(), path.stem)


class ProblemStore:
    """基于 JSON 文件的题目存储实现。"""

    def __init__(self, base_dir: Path | None = None):
        """指定题库目录，并初始化保护同进程文件操作的可重入锁。"""
        self.base_dir = base_dir or config.PROBLEMS_DIR
        self._lock = threading.RLock()

    def _path(self, problem_id: str) -> Path:
        """把已校验题号映射为题库 JSON 路径，并拒绝符号链接。"""
        if not re.fullmatch(PROBLEM_ID_RE, problem_id):
            raise ApiError(400, "invalid problem id")
        path = self.base_dir / f"{problem_id}.json"
        if path.is_symlink():
            raise ApiError(400, "invalid problem path")
        return path

    async def _locked(self, operation):
        """在线程中串行执行同步文件操作，避免阻塞异步路由。"""
        def run():
            """在题库锁持有期间执行一次读取、写入或迁移操作。"""
            with self._lock:
                return operation()
        return await asyncio.to_thread(run)

    async def migrate_safe_ids(self) -> dict[str, str]:
        """仅修复旧数据中的不安全题号，并返回需要同步的引用映射。
        """
        def _migrate() -> dict[str, str]:
            """扫描旧题库，修正不安全或冲突的文件题号。"""
            self.base_dir.mkdir(parents=True, exist_ok=True)
            mapping: dict[str, str] = {}
            used = {
                path.stem
                for path in self.base_dir.glob("*.json")
                if re.fullmatch(PROBLEM_ID_RE, path.stem)
            }
            numeric = [int(value) for value in used if value.isdigit()]
            next_id = max([1000, *numeric]) + 1

            paths = sorted(self.base_dir.glob("*.json"), key=_problem_sort_key)
            for path in paths:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                old_id = str(raw.get("id", path.stem))
                if re.fullmatch(PROBLEM_ID_RE, old_id) and path.stem == old_id:
                    continue
                # 文件名与内容不一致时优先采用内容里的合法题号；只有题号
                # 本身不安全或目标已占用时才分配兼容的数字编号。
                new_id = old_id if re.fullmatch(PROBLEM_ID_RE, old_id) else None
                target_occupied = bool(new_id and new_id in used and new_id != path.stem)
                if not new_id or target_occupied:
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
            """读取所有合法 JSON 的题号和标题，跳过损坏文件。"""
            self.base_dir.mkdir(parents=True, exist_ok=True)
            items: list[dict] = []
            paths = sorted(self.base_dir.glob("*.json"), key=_problem_sort_key)
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
            """加载、校验并按调用场景保留题目原始限制字段。"""
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
        ``assign_new_id``，在请求题号已占用时原子选择后续可用题号。
        """
        def _write() -> str:
            """独占创建题目文件；另存模式下原子寻找未占用题号。"""
            self.base_dir.mkdir(parents=True, exist_ok=True)
            base_id = cfg.id
            max_problem_id = 10**18 - 1
            version = 1

            while True:
                if version == 1:
                    candidate_id = base_id
                elif base_id.isdigit():
                    candidate_id = str((int(base_id) + version - 1) % (max_problem_id + 1))
                else:
                    suffix = f"_{version}"
                    candidate_id = base_id[:64 - len(suffix)] + suffix

                saved = (cfg if candidate_id == cfg.id
                         else cfg.model_copy(update={"id": candidate_id}))
                try:
                    path = self._path(candidate_id)
                    # "x" 独占创建：即使存在多进程竞争也绝不覆盖已有题目。
                    with open(path, "x", encoding="utf-8") as f:
                        f.write(self._dumps(saved))
                    return candidate_id
                except ApiError:
                    # 自动另存时跳过被符号链接占用的候选名；普通创建保持
                    # 原有安全错误，不能把异常悄悄解释为普通重复。
                    if not assign_new_id:
                        raise
                    version += 1
                except FileExistsError:
                    if not assign_new_id:
                        raise ApiError(409, "problem already exists")
                    version += 1

        return await self._locked(_write)

    async def update(self, cfg: ProblemConfig, *, allow_visibility: bool = True) -> None:
        """覆盖已有题目，同时保护管理员维护的测试点公开设置。"""
        def _write() -> None:
            """在锁内读取现有配置、检查权限字段并原子写回。"""
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
        """删除一个存在的题目 JSON 文件。"""
        def _delete() -> None:
            """在锁内确认文件存在后移除，避免竞态误报。"""
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            path.unlink()

        await self._locked(_delete)

    async def set_public_cases(self, problem_id: str, public_cases: bool) -> None:
        """Step 5：设置测试点明细是否对普通用户可见。"""
        def _write():
            """在保留其余字段的前提下修改测试点公开标志。"""
            path = self._path(problem_id)
            if not path.is_file():
                raise ApiError(404, "problem not found")
            cfg = ProblemConfig.model_validate_json(path.read_text(encoding="utf-8"))
            cfg.public_cases = public_cases
            self._replace(path, self._dumps(cfg))
        await self._locked(_write)

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        """通过同目录临时文件和原子替换写入完整 JSON。"""
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
        """序列化题目，并保留“未显式设置限制”这一语义。"""
        data = cfg.model_dump(exclude_none=True)
        for field in ("time_limit", "memory_limit"):
            if field not in cfg.model_fields_set:
                data.pop(field, None)
        return json.dumps(data, ensure_ascii=False, indent=2)


# 全局单例：路由层直接使用
store = ProblemStore()
