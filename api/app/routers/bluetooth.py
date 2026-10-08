from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agent_client import AgentError
from app.airplay_speakers import clean_webhook
from app.system_info import hostname_of, read_text
from app.validation import ValidationError, normalize_job_id, normalize_mac

router = APIRouter(prefix="/api/bluetooth")


class PairBody(BaseModel):
    mac: str
    name: str = ""
    adapter: str = ""


class MacBody(BaseModel):
    mac: str
    player_name: str = ""


class WebhookBody(BaseModel):
    mac: str
    webhook: str = ""


@router.get("/status")
def bluetooth_status(request: Request):
    return _with_airplay(request, request.app.state.bluetooth.status())


@router.get("/devices")
def bluetooth_devices(request: Request):
    payload = _with_airplay(request, request.app.state.bluetooth.status())
    return {"devices": payload["devices"]}


@router.post("/webhook")
def bluetooth_webhook(body: WebhookBody, request: Request):
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return _error(exc)
    raw = (body.webhook or "").strip()
    if raw and not clean_webhook(raw):
        return JSONResponse(status_code=400, content={"error": "invalid_webhook", "message": "Webhook inválido."})
    try:
        request.app.state.speakers.set_webhook(mac, raw)
    except KeyError:
        return JSONResponse(
            status_code=400,
            content={"error": "unknown_device", "message": "Essa caixa ainda não está no AirPlay. Atualize a página."},
        )
    return {"ok": True}


@router.post("/scan")
def bluetooth_scan(request: Request):
    return request.app.state.bluetooth.scan()


@router.get("/scan/{job_id}")
def bluetooth_scan_result(job_id: str, request: Request):
    try:
        job_id = normalize_job_id(job_id)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.scan_result(job_id)


@router.post("/pair")
def bluetooth_pair(body: PairBody, request: Request):
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.start_pair(mac, body.adapter)


@router.get("/pair/{job_id}")
def bluetooth_pair_result(job_id: str, mac: str, request: Request, name: str = ""):
    try:
        job_id = normalize_job_id(job_id)
        mac = normalize_mac(mac)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.finish_pair(job_id, mac, name)


@router.post("/connect")
def bluetooth_connect(body: MacBody, request: Request):
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.connect(mac, body.player_name)


@router.post("/disconnect")
def bluetooth_disconnect(body: MacBody, request: Request):
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.disconnect(mac)


@router.post("/forget")
def bluetooth_forget(body: MacBody, request: Request):
    try:
        mac = normalize_mac(body.mac)
    except ValidationError as exc:
        return _error(exc)
    return request.app.state.bluetooth.forget(mac)


def _with_airplay(request: Request, payload: dict) -> dict:
    speakers = request.app.state.speakers
    if payload.get("bridge_reachable") and payload.get("fleet_known"):
        hostname = hostname_of(read_text(request.app.state.settings.hostname_file)) or ""
        if speakers.sync(payload.get("devices") or [], hostname):
            try:
                request.app.state.agent.call("airplay-audio", timeout=5)
            except AgentError:
                pass
    for device in payload.get("devices") or []:
        in_fleet = bool(device.get("in_fleet"))
        device["airplay_webhook"] = speakers.webhook_for(device["mac"]) if in_fleet else ""
        device["airplay_name"] = speakers.name_for(device["mac"]) if in_fleet else ""
    return payload


def _error(exc: ValidationError):
    return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
