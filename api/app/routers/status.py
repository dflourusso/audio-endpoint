from fastapi import APIRouter, Request

from app.bridge import BridgeError
from app.redact import redact

router = APIRouter(prefix="/api")


@router.get("/sendspin/status")
def sendspin_status(request: Request):
    bridge = request.app.state.bridge
    try:
        status = bridge.status()
        health = bridge.health()
    except BridgeError as exc:
        return {
            "reachable": False,
            "message": exc.message,
            "listening": False,
            "connected": False,
            "bluetooth_connected": False,
            "player_name": None,
            "startup": None,
        }
    startup = status.get("startup_progress") if isinstance(status.get("startup_progress"), dict) else None
    return {
        "reachable": True,
        "listening": (startup or {}).get("phase") in {None, "ready"} or (startup or {}).get("status") == "complete" or bool(health),
        "connected": bool(status.get("connected") or status.get("ma_connected")),
        "bluetooth_connected": bool(status.get("bluetooth_connected")),
        "player_name": status.get("player_name"),
        "startup": startup,
        "health": redact(health),
    }


@router.get("/music-assistant/status")
def music_assistant_status(request: Request):
    bridge = request.app.state.bridge
    try:
        status = bridge.status()
        runtime = bridge.runtime_info()
    except BridgeError as exc:
        return {"reachable": False, "message": exc.message}
    devices = []
    for device in status.get("devices") or []:
        if isinstance(device, dict):
            devices.append(
                {
                    "player_name": device.get("player_name"),
                    "connected": bool(device.get("connected")),
                    "mac": device.get("mac"),
                }
            )
    return {
        "reachable": True,
        "ma_connected": bool(status.get("ma_connected")),
        "player_name": status.get("player_name"),
        "players": devices,
        "runtime_mode": status.get("runtime_mode") or runtime.get("mode") or runtime.get("runtime_mode"),
        "guidance": redact(status.get("operator_guidance")),
        "startup": status.get("startup_progress"),
        "spotify_connect": "Configurado no Music Assistant, não neste aparelho.",
    }
