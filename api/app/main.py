from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent_client import AgentClient, AgentError
from app.bluetooth_service import BluetoothService
from app.bridge import BridgeClient, BridgeError
from app.routers import audio, bluetooth, maintenance, status, system, wifi
from app.settings import load_settings

SETUP_ALLOWED = {
    "/api/health",
    "/api/wifi/status",
    "/api/wifi/networks",
    "/api/wifi/connect",
    "/api/wifi/refresh",
}


def create_app(settings=None, bridge=None, agent=None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Audio Endpoint", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.bridge = bridge or BridgeClient(settings.bridge_url, settings.bridge_token)
    app.state.agent = agent or AgentClient(settings.agent_socket)
    app.state.bluetooth = BluetoothService(app.state.bridge)

    @app.middleware("http")
    async def api_token(request: Request, call_next):
        token = settings.api_token
        path = request.url.path
        if token and path.startswith("/api/") and path != "/api/health":
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse(status_code=401, content={"error": "unauthorized", "message": "Não autorizado."})
        if path.startswith("/api/") and path not in SETUP_ALLOWED:
            try:
                wifi_state = request.app.state.agent.call("wifi-status")
            except AgentError:
                wifi_state = None
            if wifi_state and wifi_state.get("mode") == "access-point":
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "setup_mode",
                        "message": "Durante a configuração do Wi-Fi só é possível escolher a rede.",
                    },
                )
        return await call_next(request)

    @app.exception_handler(BridgeError)
    async def bridge_error(_request: Request, exc: BridgeError):
        return JSONResponse(status_code=exc.status_code, content={"error": "bridge_error", "message": exc.message})

    @app.exception_handler(AgentError)
    async def agent_error(_request: Request, exc: AgentError):
        return JSONResponse(status_code=503, content={"error": exc.code, "message": exc.message})

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.include_router(system.router)
    app.include_router(bluetooth.router)
    app.include_router(audio.router)
    app.include_router(status.router)
    app.include_router(maintenance.router)
    app.include_router(wifi.router)

    assets = settings.web_dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index():
        page = settings.web_dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse({"ok": True, "message": "Interface ainda não foi compilada."})

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": "not_found", "message": "Não encontrado."})
        candidate = settings.web_dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        page = settings.web_dist / "index.html"
        if page.is_file():
            return FileResponse(page)
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "Não encontrado."})

    return app


app = create_app()
