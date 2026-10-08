from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent_client import AgentError
from app.airplay import is_loopback
from app.validation import ValidationError, normalize_mac

router = APIRouter(prefix="/api")


class SessionBody(BaseModel):
    active: bool
    mac: str = ""


@router.get("/airplay/status")
def airplay_status(request: Request):
    speakers = request.app.state.speakers
    names = speakers.names()
    advertising = False
    try:
        result = request.app.state.agent.call("airplay-status", timeout=3)
        advertising = bool(result.get("running")) and bool(names)
    except AgentError:
        advertising = False
    return {
        "advertising": advertising,
        "names": names,
        "name": ", ".join(names) if names else None,
        "playing": speakers.any_playing(),
        "webhook_configured": speakers.any_webhook(),
    }


@router.post("/airplay/session")
def airplay_session(body: SessionBody, request: Request):
    host = request.client.host if request.client else ""
    if not is_loopback(host):
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "A sessão AirPlay só é atualizada neste aparelho."},
        )
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
    request.app.state.airplay.set_active(mac, body.active)
    try:
        request.app.state.agent.call("airplay-audio", timeout=5)
    except AgentError:
        pass
    return {"ok": True, "playing": request.app.state.speakers.playing(mac)}
