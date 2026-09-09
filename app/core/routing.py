"""在 FastAPI 解析 JSON 之前认证，保证 401 > 403 > 400。"""
from fastapi.routing import APIRoute

from app.core.deps import get_current_user, require_admin
from app.database import SessionLocal


class AuthenticatedRoute(APIRoute):
    """在 JSON 校验前运行认证依赖，固定 401/403 优先级。"""
    def get_route_handler(self):
        """包装默认处理器，在请求体校验前预执行认证依赖。"""
        handler = super().get_route_handler()

        def calls(dependency):
            """深度遍历依赖树，收集路由声明的认证依赖。"""
            yield dependency.call
            for child in dependency.dependencies:
                yield from calls(child)

        dependencies = set(calls(self.dependant))
        protected = get_current_user in dependencies
        admin_only = require_admin in dependencies

        async def authenticated(request):
            """先验证会话/管理员资格，再委托 FastAPI 解析请求。"""
            if protected:
                async with SessionLocal() as db:
                    user = await get_current_user(request, db)
                    if admin_only:
                        await require_admin(user)
            return await handler(request)

        return authenticated
