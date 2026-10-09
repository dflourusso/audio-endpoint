import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent_client import AgentClient, AgentError
from app.bluetooth_service import BluetoothService, watch_bluetooth
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


def create_app(settings=None, bridge=None, agent=None, watch_bluetooth_loop=None) -> FastAPI:
    settings = settings or load_settings()
    if watch_bluetooth_loop is None:
        watch_bluetooth_loop = settings.watch_bluetooth

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        thread = None
        if watch_bluetooth_loop:
            def run_watch(stop: threading.Event) -> None:
                def recover(action: dict) -> None:
                    if action.get("reclaim"):
                        app.state.bridge.set_bt_management(action["player_name"], True)
                    app.state.bridge.reconnect(action["mac"], action["player_name"])

                watch_bluetooth(app.state.bridge, recover, stop)

            thread = threading.Thread(target=run_watch, args=(stop,), daemon=True, name="bluetooth-watch")
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=2)

    app = FastAPI(title="Audio Endpoint", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    app.state.bridge = bridge or BridgeClient(settings.bridge_url, settings.bridge_token)
    app.state.agent = agent or AgentClient(settings.agent_socket)
    app.state.bluetooth = BluetoothService(
        app.state.bridge,
        restarter=lambda: app.state.agent.call("restart-bridge"),
        audio_fix=lambda mac: app.state.agent.call("bluetooth-audio", mac=mac),
    )

    wifi_mode = {"at": 0.0, "mode": None}

    def current_wifi_mode() -> str | None:
        now = time.monotonic()
        if wifi_mode["mode"] is not None and now - wifi_mode["at"] < 5:
            return wifi_mode["mode"]
        try:
            state = app.state.agent.call("wifi-status", timeout=2)
        except AgentError:
            return wifi_mode["mode"]
        mode = state.get("mode")
        wifi_mode["at"] = now
        wifi_mode["mode"] = mode
        return mode

    @app.middleware("http")
    async def api_token(request: Request, call_next):
        token = settings.api_token
        path = request.url.path
        if token and path.startswith("/api/") and path != "/api/health":
            header = request.headers.get("authorization", "")
            if header != f"Bearer {token}":
                return JSONResponse(status_code=401, content={"error": "unauthorized", "message": "Não autorizado."})
        if path.startswith("/api/") and path not in SETUP_ALLOWED:
            if current_wifi_mode() == "access-point":
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
