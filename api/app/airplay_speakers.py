"""Um AirPlay por caixa da frota. O webhook fica fora do config do bridge."""

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
NAME_LIMIT = 50


def clean_webhook(value: str) -> str:
    url = (value or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return url


def sink_token(mac: str) -> str:
    return mac.replace(":", "_")


def escape_libconfig(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _clean_label(value: str) -> str:
    return " ".join((value or "").split())


def airplay_label(player_name: str, hostname: str) -> str:
    player = _clean_label(player_name) or "Caixa"
    host = _clean_label(hostname)
    label = f"{player} @ {host}" if host else player
    return label[:NAME_LIMIT]


def assign_names(rows: list[dict], hostname: str) -> dict[str, str]:
    labels = {row["mac"]: airplay_label(row.get("name") or "", hostname) for row in rows}
    grouped: dict[str, list[str]] = {}
    for mac, label in labels.items():
        grouped.setdefault(label, []).append(mac)
    for label, macs in grouped.items():
        if len(macs) < 2:
            continue
        for mac in macs:
            suffix = mac.replace(":", "")[-4:]
            room = NAME_LIMIT - 1 - len(suffix)
            base = label[:room].rstrip()
            labels[mac] = f"{base} {suffix}"[:NAME_LIMIT]
    return labels


def assign_ports(macs: list[str]) -> dict[str, tuple[int, int]]:
    return {mac: (7000 + index, index) for index, mac in enumerate(sorted(macs))}


def render_speaker_config(name: str, mac: str, port: int, offset: int) -> str:
    token = sink_token(mac)
    safe_name = escape_libconfig(name)
    return f"""// Gerado pelo audio-endpoint. Alterações manuais são substituídas.
general =
{{
  name = "{safe_name}";
  port = {port};
  airplay_device_id_offset = {offset};
  output_backend = "pa";
  interpolation = "basic";
  service_type = "classic";
  mdns_backend = "avahi";
}};

sessioncontrol =
{{
  run_this_before_play_begins = "/bin/sh /notify.sh true {mac}";
  run_this_after_play_ends = "/bin/sh /notify.sh false {mac}";
  wait_for_completion = "no";
}};

pa =
{{
  sink = "bluez_output.{token}.1";
  application_name = "AirPlay {token}";
}};
"""


class SpeakerStore:
    def __init__(self, config_dir: Path, run_dir: Path):
        self.config_dir = Path(config_dir)
        self.run_dir = Path(run_dir)
        self._rows: list[dict] = self._load()
        self._playing = self._load_playing()
        self._hostname = ""

    def webhook_for(self, mac: str) -> str:
        row = self._row(mac)
        return row["webhook"] if row else ""

    def name_for(self, mac: str) -> str:
        mac = (mac or "").strip().upper()
        return assign_names(self._rows, self._hostname).get(mac, "")

    def names(self) -> list[str]:
        labels = assign_names(self._rows, self._hostname)
        return [labels[row["mac"]] for row in self._rows if row["mac"] in labels]

    def playing(self, mac: str) -> bool:
        return bool(self._playing.get((mac or "").strip().upper()))

    def active_macs(self) -> set[str]:
        return {mac for mac, active in self._playing.items() if active}

    def any_playing(self) -> bool:
        return any(self._playing.get(row["mac"]) for row in self._rows)

    def any_webhook(self) -> bool:
        return any(row["webhook"] for row in self._rows)

    def sync(self, devices: list[dict], hostname: str) -> bool:
        hostname = _clean_label(hostname)
        incoming = []
        seen = set()
        for device in devices:
            if not isinstance(device, dict) or not device.get("in_fleet", True):
                continue
            mac = str(device.get("mac") or "").strip().upper()
            if not MAC_RE.fullmatch(mac) or mac in seen:
                continue
            seen.add(mac)
            previous = self._row(mac)
            name = _clean_label(str(device.get("player_name") or device.get("name") or ""))
            incoming.append(
                {
                    "mac": mac,
                    "name": name or (previous["name"] if previous else "Caixa"),
                    "webhook": previous["webhook"] if previous else "",
                }
            )
        incoming.sort(key=lambda row: row["mac"])
        changed = incoming != self._rows or hostname != self._hostname
        self._hostname = hostname
        self._rows = incoming
        self._playing = {mac: flag for mac, flag in self._playing.items() if mac in seen}
        self._persist()
        return changed

    def set_webhook(self, mac: str, url: str) -> None:
        row = self._row(mac)
        if row is None:
            raise KeyError(mac)
        row["webhook"] = clean_webhook(url)
        self._write_catalog()

    def set_playing(self, mac: str, active: bool) -> None:
        mac = (mac or "").strip().upper()
        self._playing[mac] = bool(active)
        self._write_devices()

    def republish(self, hostname: str) -> None:
        self._hostname = _clean_label(hostname)
        self._persist()

    def _row(self, mac: str) -> dict | None:
        mac = (mac or "").strip().upper()
        for row in self._rows:
            if row["mac"] == mac:
                return row
        return None

    def _catalog_path(self) -> Path:
        return self.config_dir / "airplay-devices.json"

    def _conf_dir(self) -> Path:
        return self.config_dir / "shairport"

    def _devices_path(self) -> Path:
        return self.run_dir / "devices.json"

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._catalog_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            mac = str(item.get("mac") or "").strip().upper()
            if not MAC_RE.fullmatch(mac):
                continue
            rows.append(
                {
                    "mac": mac,
                    "name": _clean_label(str(item.get("name") or "")) or "Caixa",
                    "webhook": clean_webhook(str(item.get("webhook") or "")),
                }
            )
        rows.sort(key=lambda row: row["mac"])
        return rows

    def _load_playing(self) -> dict[str, bool]:
        try:
            data = json.loads(self._devices_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        playing = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("mac"):
                    playing[str(item["mac"]).upper()] = bool(item.get("playing"))
        return playing

    def _persist(self) -> None:
        self._write_catalog()
        self._write_devices()
        self._write_confs()

    def _write_catalog(self) -> None:
        path = self._catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._rows, ensure_ascii=False, indent=2) + "\n"
        if path.is_file() and path.read_text(encoding="utf-8") == text:
            return
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)

    def _write_devices(self) -> None:
        payload = [
            {
                "mac": row["mac"],
                "token": sink_token(row["mac"]),
                "playing": bool(self._playing.get(row["mac"])),
            }
            for row in self._rows
        ]
        path = self._devices_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            if path.is_file() and path.read_text(encoding="utf-8") == text:
                return
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except OSError:
            return

    def _write_confs(self) -> None:
        directory = self._conf_dir()
        directory.mkdir(parents=True, exist_ok=True)
        names = assign_names(self._rows, self._hostname)
        ports = assign_ports([row["mac"] for row in self._rows])
        keep = set()
        for row in self._rows:
            token = sink_token(row["mac"])
            keep.add(f"{token}.conf")
            port, offset = ports[row["mac"]]
            rendered = render_speaker_config(names[row["mac"]], row["mac"], port, offset)
            destination = directory / f"{token}.conf"
            current = destination.read_text(encoding="utf-8") if destination.is_file() else ""
            if current == rendered:
                continue
            temporary = destination.with_suffix(".conf.tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(destination)
        for path in directory.glob("*.conf"):
            if path.name not in keep:
                path.unlink()
        obsolete = self.config_dir / "shairport-sync.conf"
        if obsolete.is_file():
            obsolete.unlink()
