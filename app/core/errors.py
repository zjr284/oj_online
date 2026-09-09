"""统一异常与响应格式（api.md）。

- 所有 JSON 响应含 code 字段，与 HTTP 状态码一致；
- 错误格式：{"code": <状态码>, "msg": <提示>, "data": null}；
- 异常处理顺序：401 > 403 > 400 > 429 > 409 > 404 > 500；
- FastAPI 默认的参数校验错误是 422，按 FAQ 要求统一映射为 400。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """业务异常：携带 HTTP 状态码与提示信息，由全局处理器转换为标准响应。"""

    def __init__(self, status: int, msg: str):
        """保存待转换为 JSON 响应的 HTTP 状态码与安全提示。"""
        super().__init__(msg)
        self.status = status
        self.msg = msg


def ok(data=None, msg: str = "success") -> dict:
    """成功响应的统一格式（api.md 示例默认 msg 为 "success"）。"""
    return {"code": 200, "msg": msg, "data": data}


def _resp(status: int, msg: str) -> JSONResponse:
    """生成符合 API 统一格式的错误响应。"""
    return JSONResponse(status_code=status, content={"code": status, "msg": msg, "data": None})


def register_exception_handlers(app: FastAPI) -> None:
    """在 app 上注册全局异常处理器。"""

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        """把显式业务异常转换为约定的 JSON 错误体。"""
        return _resp(exc.status, exc.msg)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        """将 FastAPI 默认 422 参数错误统一映射为 400。"""
        # 参数校验失败统一返回 400（而不是 FastAPI 默认的 422）
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query"))
        detail = f"invalid field {loc}: {first.get('msg', 'invalid request')}" if loc else "invalid request"
        return _resp(400, detail)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        """保持框架产生的 HTTP 错误也符合统一响应结构。"""
        return _resp(exc.status_code, str(exc.detail))

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        """记录未预期异常，但不向客户端暴露堆栈细节。"""
        # 兜底：未预期异常返回 500，不向客户端泄露内部细节
        logger.exception("unhandled exception on %s", request.url.path)
        return _resp(500, "internal server error")
