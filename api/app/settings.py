import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bridge_url: str
    bridge_token: str
    api_token: str
    agent_socket: str
    config_dir: Path
    data_dir: Path
    host_proc: Path
    host_sys: Path
    hostname_file: Path
    os_release_file: Path
    web_dist: Path
    project_version: str
    web_port: int
    airplay_webhook_url: str = ""
    airplay_flag: Path = Path("/run/audio-endpoint/airplay-playing")
    airplay_run: Path = Path("/run/audio-endpoint/airplay")
    supervise_airplay: bool = True


def read_project_version() -> str:
    candidates = (
        Path("/app/VERSION"),
        Path(__file__).resolve().parents[2] / "VERSION",
    )
    for path in candidates:
        try:
            version = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version
    return os.environ.get("PROJECT_VERSION", "").strip() or "0.2.0"


def load_settings() -> Settings:
    version = read_project_version()
    return Settings(
        bridge_url=os.environ.get("BRIDGE_URL", "http://127.0.0.1:8080").rstrip("/"),
        bridge_token=os.environ.get("BRIDGE_TOKEN", "").strip(),
        api_token=os.environ.get("API_TOKEN", "").strip(),
        agent_socket=os.environ.get("AGENT_SOCKET", "/run/audio-endpoint/agent.sock"),
        config_dir=Path(os.environ.get("CONFIG_DIR", "/data/config")),
        data_dir=Path(os.environ.get("DATA_DIR", "/data/state")),
        host_proc=Path(os.environ.get("HOST_PROC", "/proc")),
        host_sys=Path(os.environ.get("HOST_SYS", "/sys")),
        hostname_file=Path(os.environ.get("HOST_HOSTNAME_FILE", "/etc/hostname")),
        os_release_file=Path(os.environ.get("HOST_OS_RELEASE", "/etc/os-release")),
        web_dist=Path(os.environ.get("WEB_DIST", "/app/web/dist")),
        project_version=version,
        web_port=int(os.environ.get("WEB_PORT", "80")),
        airplay_webhook_url=os.environ.get("AIRPLAY_WEBHOOK_URL", ""),
        airplay_flag=Path(os.environ.get("AIRPLAY_FLAG", "/run/audio-endpoint/airplay-playing")),
    )
