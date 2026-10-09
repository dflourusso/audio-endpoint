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
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
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

    def retire_airplay(self) -> None:
        leftover = Path("/etc/pipewire/pipewire.conf.d/10-audio-endpoint-airplay.conf")
        if leftover.is_file():
            leftover.unlink()
        unit = Path(self._audio_home()) / ".config" / "systemd" / "user" / "audio-endpoint-airplay-link.service"
        self._as_audio_user(["systemctl", "--user", "disable", "--now", "audio-endpoint-airplay-link.service"])
        if unit.is_file():
            unit.unlink()
            self._as_audio_user(["systemctl", "--user", "daemon-reload"])

    def _audio_home(self) -> str:
        completed = self.run(["getent", "passwd", "audioendpoint"])
        if completed.returncode == 0:
            parts = completed.stdout.split(":")
            if len(parts) >= 6 and parts[5].startswith("/"):
                return parts[5]
        return "/var/lib/audioendpoint"

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
            pulled = self.run(
                [
                    "docker",
                    "compose",
                    "--project-directory",
                    str(self.root),
                    "pull",
                    "sendspin-bridge",
                ]
            )
            if pulled.returncode != 0:
                self._write_update("failed", pulled.stderr.strip() or "docker compose pull falhou")
                return
            up = self.run(
                ["docker", "compose", "--project-directory", str(self.root), "up", "-d", "--build", "--remove-orphans"]
            )
            if up.returncode != 0:
                self._write_update("failed", up.stderr.strip() or "docker compose up falhou")
                return
            self._write_update("succeeded", "Atualização concluída")
            threading.Timer(1.0, self._reload_agent).start()
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

    def install_unit(self) -> None:
        source = self.root / "agent" / "audio-endpoint-agent.service"
        target = Path("/etc/systemd/system/audio-endpoint-agent.service")
        if not source.is_file():
            return
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        if current != source.read_text(encoding="utf-8"):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            self.run(["systemctl", "daemon-reload"])

    def reconnect_app(self) -> None:
        # The panel bind-mounts /run/audio-endpoint. Recreating that directory
        # leaves the container on the old, empty one, so restart it after the socket exists.
        self.run(["docker", "restart", "audio-endpoint"])

    def _reload_agent(self) -> None:
        try:
            self.install_unit()
        except Exception:
            pass
        self.run(["systemctl", "restart", "audio-endpoint-agent"])

    def ensure_a2dp_policy(self) -> None:
        source = self.root / "agent" / "wireplumber" / "51-a2dp-sink.lua"
        if not source.is_file():
            return
        desired = source.read_text(encoding="utf-8")
        target = Path("/etc/wireplumber/bluetooth.lua.d/51-audio-endpoint-a2dp.lua")
        if target.is_file() and target.read_text(encoding="utf-8") == desired:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desired, encoding="utf-8")
        self._restart_user_wireplumber()

    def claim_bluetooth_audio(self) -> None:
        changed = False
        for name, uid, gid, home in self._other_users():
            if self._mask_pipewire(home, uid, gid):
                changed = True
            if self._stop_user_pipewire(name, uid):
                changed = True
        if changed:
            self._restart_user_wireplumber()

    def _other_users(self) -> list[tuple[str, str, str, str]]:
        audio_uid = self._audio_uid()
        completed = self.run(["getent", "passwd"])
        if completed.returncode != 0:
            return []
        users = []
        for line in completed.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 6 or not parts[2].isdigit() or not parts[3].isdigit():
                continue
            name, uid, gid, home = parts[0], parts[2], parts[3], parts[5]
            if uid == audio_uid or name in {"audioendpoint", "root", "nobody"} or int(uid) < 1000:
                continue
            if not home.startswith("/") or home in {"/", "/root", "/nonexistent"}:
                continue
            users.append((name, uid, gid, home))
        return users

    def _mask_pipewire(self, home: str, uid: str, gid: str) -> bool:
        directory = Path(home) / ".config" / "systemd" / "user"
        directory.mkdir(parents=True, exist_ok=True)
        changed = False
        for unit in ("pipewire.socket", "pipewire.service", "pipewire-pulse.socket", "pipewire-pulse.service", "wireplumber.service"):
            link = directory / unit
            if link.is_symlink() and os.readlink(link) == "/dev/null":
                continue
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to("/dev/null")
            changed = True
        if os.geteuid() == 0:
            os.chown(directory, int(uid), int(gid))
            for child in directory.iterdir():
                os.lchown(child, int(uid), int(gid))
        return changed

    def _stop_user_pipewire(self, name: str, uid: str) -> bool:
        if not Path(f"/run/user/{uid}").is_dir():
            return False
        active = self._as_user(name, uid, ["systemctl", "--user", "is-active", "wireplumber.service"])
        if active.stdout.strip() != "active":
            return False
        self._as_user(
            name,
            uid,
            [
                "systemctl",
                "--user",
                "stop",
                "pipewire.socket",
                "pipewire.service",
                "pipewire-pulse.socket",
                "pipewire-pulse.service",
                "wireplumber.service",
            ],
        )
        return True

    def _audio_uid(self) -> str:
        env_file = self.root / ".env"
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("AUDIO_UID="):
                    uid = line.split("=", 1)[1].strip()
                    if uid.isdigit():
                        return uid
        completed = self.run(["id", "-u", "audioendpoint"])
        if completed.returncode == 0 and completed.stdout.strip().isdigit():
            return completed.stdout.strip()
        return "1000"

    def _as_audio_user(self, args: list[str]):
        return self._as_user("audioendpoint", self._audio_uid(), args)

    def _as_user(self, name: str, uid: str, args: list[str]):
        return self.run(
            [
                "runuser",
                "-u",
                name,
                "--",
                "env",
                f"XDG_RUNTIME_DIR=/run/user/{uid}",
                f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
                *args,
            ]
        )

    def _restart_user_wireplumber(self) -> None:
        self._as_audio_user(["systemctl", "--user", "restart", "wireplumber.service"])

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
        if action == "bluetooth-audio":
            mac = str(payload.get("mac") or "").strip().upper()
            if not _MAC_RE.fullmatch(mac):
                return {"ok": False, "error": "invalid_mac", "message": "MAC inválido."}
            control.claim_bluetooth_audio()
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
    try:
        control.install_unit()
    except Exception:
        pass
    try:
        control.ensure_a2dp_policy()
    except Exception:
        pass
    try:
        control.retire_airplay()
    except Exception:
        pass
    try:
        control.claim_bluetooth_audio()
    except Exception:
        pass
    threading.Thread(target=control.wifi.supervise, name="wifi-setup", daemon=True).start()
    server = AgentServer(SOCKET_PATH, control)
    threading.Timer(1.0, control.reconnect_app).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
