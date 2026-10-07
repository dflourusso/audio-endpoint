from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/wifi")


class WifiConnectBody(BaseModel):
    ssid: str
    password: str = ""


@router.get("/status")
def wifi_status(request: Request):
    return request.app.state.agent.call("wifi-status")


@router.get("/networks")
def wifi_networks(request: Request):
    return request.app.state.agent.call("wifi-networks")


@router.post("/refresh")
def wifi_refresh(request: Request):
    return request.app.state.agent.call("wifi-refresh")


@router.post("/connect")
def wifi_connect(body: WifiConnectBody, request: Request):
    return request.app.state.agent.call("wifi-connect", ssid=body.ssid, password=body.password)
