"""在 FastAPI 解析 JSON 之前认证，保证 401 > 403 > 400。"""
from fastapi.routing import APIRoute

from app.core.deps import get_current_user, require_admin
from app.database import SessionLocal


class AuthenticatedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        def calls(dependency):
            yield dependency.call
            for child in dependency.dependencies:
                yield from calls(child)

        dependencies = set(calls(self.dependant))
        protected = get_current_user in dependencies
        admin_only = require_admin in dependencies

        async def authenticated(request):
            if protected:
                async with SessionLocal() as db:
                    user = await get_current_user(request, db)
                    if admin_only:
                        await require_admin(user)
            return await handler(request)

        return authenticated
