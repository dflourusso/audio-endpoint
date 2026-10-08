from fastapi import APIRouter, Request

from app.bluetooth_service import live_rows
from app.bridge import BridgeError
from app.redact import redact


def describe_playback(status: dict) -> dict:
    players = []
    for device in live_rows(status):
        sink = str(device.get("sink_name") or "")
        players.append(
            {
                "player_name": device.get("player_name"),
                "mac": device.get("mac") or device.get("bluetooth_mac"),
                "connected": bool(device.get("bluetooth_connected")),
                "session_connected": bool(device.get("server_connected")),
                "playing": bool(device.get("playing")),
                "sink": sink,
                "has_sink": bool(device.get("has_sink")) and "bluez" in sink.lower(),
            }
        )
    session = any(player["session_connected"] or player["playing"] for player in players)
    name = status.get("player_name")
    if not name and players:
        name = players[0]["player_name"]
    return {"players": players, "session_connected": session, "player_name": name}

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
    playback = describe_playback(status)
    return {
        "reachable": True,
        "listening": (startup or {}).get("phase") in {None, "ready"} or (startup or {}).get("status") == "complete" or bool(health),
        "connected": bool(status.get("connected") or status.get("ma_connected") or playback["session_connected"]),
        "bluetooth_connected": bool(status.get("bluetooth_connected")),
        "player_name": playback["player_name"],
        "session_connected": playback["session_connected"],
        "playing": any(player["playing"] for player in playback["players"]),
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
    playback = describe_playback(status)
    return {
        "reachable": True,
        "ma_connected": bool(status.get("ma_connected")) or playback["session_connected"],
        "session_connected": playback["session_connected"],
        "playing": any(player["playing"] for player in playback["players"]),
        "player_name": playback["player_name"],
        "players": playback["players"],
        "runtime_mode": status.get("runtime_mode") or runtime.get("mode") or runtime.get("runtime_mode"),
        "guidance": redact(status.get("operator_guidance")),
        "startup": status.get("startup_progress"),
    }
