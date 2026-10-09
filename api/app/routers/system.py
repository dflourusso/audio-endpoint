import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.system_info import (
    cpu_percent,
    default_interface,
    hostname_of,
    ipv4,
    mac_address,
    memory,
    os_name,
    read_text,
    temperature_c,
    uptime_seconds,
)
from app.validation import ValidationError, normalize_hostname

router = APIRouter(prefix="/api/system")


class HostnameBody(BaseModel):
    hostname: str


@router.get("")
def system_status(request: Request):
    settings = request.app.state.settings
    proc = settings.host_proc
    hostname = hostname_of(read_text(settings.hostname_file))
    interface = default_interface(read_text(proc / "net" / "route"))
    disk_path = settings.data_dir if settings.data_dir.exists() else Path("/")
    usage = shutil.disk_usage(disk_path)
    bridge_version = None
    try:
        version = request.app.state.bridge.version()
        bridge_version = version.get("version") or version.get("current") or version
    except Exception:
        bridge_version = None
    return {
        "hostname": hostname,
        "mdns": f"{hostname}.local" if hostname else None,
        "ip": ipv4(),
        "mac": mac_address(settings.host_sys, interface),
        "interface": interface,
        "cpu_percent": cpu_percent(lambda: read_text(proc / "stat")),
        "memory": memory(read_text(proc / "meminfo")),
        "disk": {
            "total_gb": round(usage.total / 1024**3, 1),
            "used_gb": round(usage.used / 1024**3, 1),
            "percent": round(usage.used * 100 / usage.total, 1) if usage.total else None,
        },
        "temperature_c": temperature_c(settings.host_sys),
        "uptime_seconds": uptime_seconds(read_text(proc / "uptime")),
        "os": os_name(read_text(settings.os_release_file)),
        "project_version": settings.project_version,
        "bridge_version": bridge_version,
    }


@router.post("/hostname")
def change_hostname(body: HostnameBody, request: Request):
    try:
        hostname = normalize_hostname(body.hostname)
    except ValidationError as exc:
        return _error(exc)
    request.app.state.agent.call("set-hostname", hostname=hostname)
    warning = None
    try:
        request.app.state.bluetooth.set_bridge_hostname(hostname)
    except Exception:
        warning = "O hostname do Linux mudou, mas o nome do bridge não foi atualizado."
    return {"ok": True, "hostname": hostname, "mdns": f"{hostname}.local", "warning": warning}


def _error(exc: ValidationError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"error": exc.code, "message": exc.message})
