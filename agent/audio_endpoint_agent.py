#!/usr/bin/env python3
"""Agente local do audio-endpoint. Só executa ações fixas, sem shell livre."""

import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from pathlib import Path

from wifi_setup import WifiInputError, WifiManager

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
LOG_SOURCES = {
    "agent": "audio-endpoint-agent.service",
    "bluetooth": "bluetooth.service",
    "avahi": "avahi-daemon.service",
}
ROOT = Path(os.environ.get("AUDIO_ENDPOINT_ROOT", "/opt/audio-endpoint"))
SOCKET_PATH = Path(os.environ.get("AGENT_SOCKET", "/run/audio-endpoint/agent.sock"))


def valid_hostname(value: str) -> str | None:
    hostname = (value or "").strip().lower()
    if hostname in {"localhost", "localhost.localdomain"} or not HOSTNAME_RE.match(hostname):
        return None
    return hostname


def update_hosts(text: str, hostname: str) -> str:
    lines = []
    found = False
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "127.0.1.1":
            lines.append(f"127.0.1.1\t{hostname}")
            found = True
        else:
            lines.append(line)
    if not found:
        if lines and lines[-1] == "":
            lines.pop()
        lines.append(f"127.0.1.1\t{hostname}")
    result = "\n".join(lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class HostControl:
    def __init__(self, root: Path = ROOT, runner=None):
        self.root = Path(root)
        self.run = runner or _run
        self._update_lock = threading.Lock()
        self._updating = False
        self.wifi = WifiManager(self.run)

    def set_hostname(self, hostname: str) -> None:
        completed = self.run(["hostnamectl", "set-hostname", hostname])
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "hostnamectl falhou")
        hosts = Path("/etc/hosts")
        if hosts.is_file():
            hosts.write_text(update_hosts(hosts.read_text(encoding="utf-8"), hostname), encoding="utf-8")
        self.restart_avahi()

    def restart_avahi(self) -> None:
        self._systemctl("restart", "avahi-daemon")

    def restart_bluetooth(self) -> None:
        self._systemctl("restart", "bluetooth")

    def restart_bridge(self) -> None:
        self._compose("restart", "sendspin-bridge")

    def restart_app(self) -> None:
        threading.Timer(1.0, lambda: self._compose("restart", "audio-endpoint")).start()

    def reboot(self) -> None:
        threading.Timer(1.0, lambda: self.run(["systemctl", "reboot"])).start()

    def logs(self, source: str, lines: int) -> str:
        unit = LOG_SOURCES[source]
        completed = self.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"]
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "journalctl falhou")
        return completed.stdout

    def begin_update(self) -> dict:
        with self._update_lock:
            if self._updating:
                return {"ok": True, "started": False, "state": "running"}
            self._updating = True
        threading.Thread(target=self._update, daemon=True).start()
        return {"ok": True, "started": True, "state": "running"}

    def update_status(self) -> dict:
        path = self.root / "data" / "update-status.json"
        if not path.is_file():
            return {"ok": True, "state": "idle"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"ok": True, "state": "unknown"}
        data["ok"] = True
        return data

    def _update(self) -> None:
        status_path = self.root / "data" / "update-status.json"
        try:
            self._write_update("running", "Atualização iniciada")
            porcelain = self.run(["git", "-C", str(self.root), "status", "--porcelain"])
            if porcelain.returncode != 0:
                self._write_update("failed", porcelain.stderr.strip() or "git status falhou")
                return
            if porcelain.stdout.strip():
                self._write_update("failed", "A árvore Git tem alterações locais. A atualização foi cancelada.")
                return
            self._backup()
            pull = self.run(["git", "-C", str(self.root), "pull", "--ff-only"])
            if pull.returncode != 0:
                self._write_update("failed", pull.stderr.strip() or pull.stdout.strip() or "git pull falhou")
                return
            pulled = self.run(["docker", "compose", "--project-directory", str(self.root), "pull"])
            if pulled.returncode != 0:
                self._write_update("failed", pulled.stderr.strip() or "docker compose pull falhou")
                return
            up = self.run(["docker", "compose", "--project-directory", str(self.root), "up", "-d", "--build"])
            if up.returncode != 0:
                self._write_update("failed", up.stderr.strip() or "docker compose up falhou")
                return
            self._write_update("succeeded", "Atualização concluída")
        except Exception as exc:
            write_json(status_path, {"state": "failed", "message": str(exc), "at": _now()})
        finally:
            with self._update_lock:
                self._updating = False

    def _backup(self) -> None:
        stamp = time.strftime("%Y%m%d%H%M%S")
        destination = self.root / "data" / "backups" / stamp
        destination.mkdir(parents=True, exist_ok=True)
        env_file = self.root / ".env"
        if env_file.is_file():
            shutil.copy2(env_file, destination / ".env")
        for folder in ("config", "bridge"):
            source = self.root / folder
            if source.is_dir():
                shutil.copytree(source, destination / folder, dirs_exist_ok=True)

    def _write_update(self, state: str, message: str) -> None:
        write_json(
            self.root / "data" / "update-status.json",
            {"state": state, "message": message, "at": _now()},
        )

    def _systemctl(self, action: str, unit: str) -> None:
        completed = self.run(["systemctl", action, unit])
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"systemctl {action} {unit} falhou")

    def _compose(self, *args: str) -> None:
        completed = self.run(["docker", "compose", "--project-directory", str(self.root), *args])
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "docker compose falhou")


def dispatch(payload: dict, control: HostControl) -> dict:
    action = payload.get("action")
    try:
        if action == "set-hostname":
            hostname = valid_hostname(str(payload.get("hostname") or ""))
            if not hostname:
                return {"ok": False, "error": "invalid_hostname", "message": "Hostname inválido."}
            control.set_hostname(hostname)
            return {"ok": True, "hostname": hostname}
        if action == "restart-avahi":
            control.restart_avahi()
            return {"ok": True}
        if action == "restart-bluetooth":
            control.restart_bluetooth()
            return {"ok": True}
        if action == "restart-bridge":
            control.restart_bridge()
            return {"ok": True}
        if action == "restart-app":
            control.restart_app()
            return {"ok": True, "message": "A interface vai reiniciar em instantes."}
        if action == "reboot":
            control.reboot()
            return {"ok": True, "message": "O aparelho vai reiniciar."}
        if action == "logs":
            source = str(payload.get("source") or "")
            if source not in LOG_SOURCES:
                return {"ok": False, "error": "invalid_log_source", "message": "Origem de log inválida."}
            try:
                lines = int(payload.get("lines") or 100)
            except (TypeError, ValueError):
                return {"ok": False, "error": "invalid_lines", "message": "Número de linhas inválido."}
            if lines < 1 or lines > 200:
                return {"ok": False, "error": "invalid_lines", "message": "Peça entre 1 e 200 linhas."}
            return {"ok": True, "text": control.logs(source, lines)}
        if action == "update":
            return control.begin_update()
        if action == "update-status":
            return control.update_status()
        if action == "wifi-status":
            return control.wifi.status()
        if action == "wifi-networks":
            return control.wifi.network_list()
        if action == "wifi-refresh":
            return control.wifi.refresh()
        if action == "wifi-connect":
            try:
                return control.wifi.connect(str(payload.get("ssid") or ""), str(payload.get("password") or ""))
            except WifiInputError as exc:
                return {"ok": False, "error": "invalid_wifi", "message": exc.message}
    except Exception as exc:
        return {"ok": False, "error": "agent_error", "message": str(exc)}
    return {"ok": False, "error": "unknown_action", "message": "Ação desconhecida."}


class AgentHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.read(65536)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send({"ok": False, "error": "invalid_json", "message": "Pedido inválido."})
            return
        if not isinstance(payload, dict):
            self._send({"ok": False, "error": "invalid_json", "message": "Pedido inválido."})
            return
        self._send(dispatch(payload, self.server.control))

    def _send(self, payload: dict) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class AgentServer(socketserver.ThreadingUnixStreamServer):
    def __init__(self, path: Path, control: HostControl):
        self.control = control
        if path.exists():
            path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(path), AgentHandler)
        os.chmod(path, 0o660)


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main() -> None:
    control = HostControl(ROOT)
    threading.Thread(target=control.wifi.supervise, name="wifi-setup", daemon=True).start()
    server = AgentServer(SOCKET_PATH, control)
    server.serve_forever()


if __name__ == "__main__":
    main()
