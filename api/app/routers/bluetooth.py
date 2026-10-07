from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.validation import ValidationError, normalize_job_id, normalize_mac

router = APIRouter(prefix="/api/bluetooth")


class PairBody(BaseModel):
    mac: str
    name: str = ""
    adapter: str = ""


class MacBody(BaseModel):
    mac: str


@router.get("/status")
def bluetooth_status(request: Request):
    return request.app.state.bluetooth.status()


@router.get("/devices")
def bluetooth_devices(request: Request):
    return {"devices": request.app.state.bluetooth.status()["devices"]}


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
    return request.app.state.bluetooth.connect(mac)


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


def _error(exc: ValidationError):
    return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
