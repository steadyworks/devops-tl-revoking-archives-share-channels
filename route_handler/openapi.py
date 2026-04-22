from fastapi.responses import JSONResponse

from openapi_override import (  # type: ignore[attr-defined]
    build_swift_openapi,
)
from route_handler.base import RouteHandler, unauthenticated_route


class OpenAPIAPIHandler(RouteHandler):
    def register_routes(self) -> None:
        self.router.add_api_route(
            "/openapi-swift.json",
            self.openapi_swift,
            methods=["PUT"],
            response_model=None,
        )

    @unauthenticated_route
    async def openapi_swift(self) -> JSONResponse:
        return JSONResponse(build_swift_openapi(self.app))  # pyright: ignore[reportArgumentType]
