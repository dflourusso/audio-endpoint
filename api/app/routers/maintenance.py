from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.validation import ValidationError, normalize_log_lines, normalize_log_source, normalize_restart_target

router = APIRouter(prefix="/api")


class RestartBody(BaseModel):
    target: str


@router.get("/logs")
def logs(request: Request, source: str = "bridge", lines: int = 100):
    try:
        source = normalize_log_source(source)
        lines = normalize_log_lines(lines)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
    if source == "bridge":
        payload = request.app.state.bridge.logs(lines)
        text = payload.get("logs") or payload.get("text") or payload.get("lines")
        if isinstance(text, list):
            text = "\n".join(str(line) for line in text)
        return {"source": source, "text": text or ""}
    result = request.app.state.agent.call("logs", source=source, lines=lines)
    return {"source": source, "text": result.get("text") or ""}


@router.post("/maintenance/restart")
def restart(body: RestartBody, request: Request):
    try:
        target = normalize_restart_target(body.target)
    except ValidationError as exc:
        return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
    action = {"app": "restart-app", "bluetooth": "restart-bluetooth", "sendspin": "restart-bridge"}[target]
    return request.app.state.agent.call(action)


@router.post("/maintenance/reboot")
def reboot(request: Request):
    return request.app.state.agent.call("reboot")


@router.post("/maintenance/update")
def update(request: Request):
    return request.app.state.agent.call("update")


@router.get("/maintenance/update")
def update_status(request: Request):
    return request.app.state.agent.call("update-status")
