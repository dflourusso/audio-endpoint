import time

from app.bridge import BridgeClient, BridgeError
from app.fleet import remove_device, set_bridge_name, upsert_device
from app.validation import normalize_mac


def _name_of(device: dict) -> str:
    for key in ("name", "alias", "player_name"):
        value = device.get(key)
        if value:
            return str(value).strip()
    return ""


def _live_mac(device: dict) -> str:
    return str(device.get("mac") or device.get("bluetooth_mac") or "").strip().upper()


def _bluetooth_connected(device: dict) -> bool:
    if not device:
        return False
    if "bluetooth_connected" in device:
        return bool(device["bluetooth_connected"])
    return device.get("connected") is True


def live_rows(status: dict | None) -> list[dict]:
    if not isinstance(status, dict):
        return []
    listed = status.get("devices")
    if isinstance(listed, list):
        rows = [item for item in listed if isinstance(item, dict) and _live_mac(item)]
        if rows:
            return rows
    if _live_mac(status):
        return [status]
    return []


def sinkless_speaker(status: dict | None) -> str:
    for device in live_rows(status):
        if not _bluetooth_connected(device):
            continue
        sink = str(device.get("sink_name") or "")
        if device.get("has_sink") and "bluez" in sink.lower():
            continue
        return _live_mac(device)
    return ""


def missing_players(config: dict, status: dict | None) -> bool:
    fleet = {
        str(device.get("mac", "")).strip().upper()
        for device in (config.get("BLUETOOTH_DEVICES") or [])
        if isinstance(device, dict) and device.get("mac")
    }
    if not fleet:
        return False
    live = {_live_mac(device) for device in live_rows(status)}
    return not fleet <= live


def merge_devices(status: dict, paired: list, config: dict) -> list[dict]:
    fleet = {
        str(device.get("mac", "")).upper(): device
        for device in (config.get("BLUETOOTH_DEVICES") or [])
        if isinstance(device, dict) and device.get("mac")
    }
    runtime = {_live_mac(device): device for device in live_rows(status) if _live_mac(device)}
    found: dict[str, dict] = {}
    for item in paired:
        if not isinstance(item, dict) or not item.get("mac"):
            continue
        mac = str(item["mac"]).upper()
        live = runtime.get(mac, {})
        saved = fleet.get(mac, {})
        found[mac] = _device_view(mac, item, saved, live, mac in fleet)
    for mac, saved in fleet.items():
        if mac in found:
            continue
        found[mac] = _device_view(mac, {}, saved, runtime.get(mac, {}), True)
    return sorted(found.values(), key=lambda device: (not device["connected"], device["name"].lower()))


def _device_view(mac: str, item: dict, saved: dict, live: dict, in_fleet: bool) -> dict:
    connected = _bluetooth_connected(live) or _bluetooth_connected(item)
    return {
        "mac": mac,
        "name": _name_of(item) or _name_of(saved) or _name_of(live) or mac,
        "player_name": saved.get("player_name") or _name_of(live) or _name_of(item) or mac,
        "paired": bool(item),
        "connected": connected,
        "in_fleet": in_fleet,
        "announced": bool(live),
        "enabled": saved.get("enabled", True) if in_fleet else False,
    }


def choose_adapter(adapters: list) -> str:
    rows = [
        item
        for item in adapters
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if not rows:
        raise BridgeError("Nenhum adaptador Bluetooth apareceu. O rádio da placa não está visível para o bridge.")
    powered = [item for item in rows if item.get("powered")]
    chosen = (powered or rows)[0]
    return str(chosen["id"]).strip()


def connected_device(devices: list[dict]) -> dict | None:
    for device in devices:
        if device.get("connected"):
            return {"mac": device["mac"], "name": device["name"]}
    return None


class BluetoothService:
    def __init__(self, bridge: BridgeClient, restarter=None, audio_fix=None):
        self.bridge = bridge
        self._restarter = restarter
        self._audio_fix = audio_fix
        self._players_restarted_at = 0.0
        self._audio_fixed_at = 0.0

    def status(self) -> dict:
        snapshot = self._safe_status()
        config = self._safe_config()
        if snapshot is not None and missing_players(config, snapshot):
            self._restart_players_once()
        elif snapshot is not None:
            self._fix_audio_once(sinkless_speaker(snapshot))
        paired = self._safe_paired()
        devices = merge_devices(snapshot, paired, config)
        return {
            "bridge_reachable": snapshot is not None,
            "connected_device": connected_device(devices),
            "paired_count": sum(1 for device in devices if device["paired"]),
            "devices": devices,
        }

    def scan(self) -> dict:
        adapter = choose_adapter(self.bridge.adapters())
        result = self.bridge.scan(adapter)
        job_id = str(result.get("job_id") or "")
        if not job_id:
            raise BridgeError("O bridge não iniciou a busca Bluetooth.")
        return {"job_id": job_id, "adapter": adapter}

    def scan_result(self, job_id: str) -> dict:
        result = self.bridge.scan_result(job_id)
        devices = []
        for device in result.get("devices") or []:
            if isinstance(device, dict) and device.get("mac"):
                found = {
                    "mac": str(device["mac"]).upper(),
                    "name": _name_of(device) or str(device["mac"]).upper(),
                }
                adapter = str(device.get("adapter") or "").strip()
                if adapter:
                    found["adapter"] = adapter
                devices.append(found)
        return {"status": result.get("status") or "running", "devices": devices, "error": result.get("error")}

    def start_pair(self, mac: str, adapter: str = "") -> dict:
        result = self.bridge.pair_new(normalize_mac(mac), adapter.strip())
        job_id = str(result.get("job_id") or "")
        if not job_id:
            raise BridgeError("O bridge não iniciou o pareamento.")
        return {"job_id": job_id}

    def finish_pair(self, job_id: str, mac: str, player_name: str) -> dict:
        result = self.bridge.pair_result(job_id)
        status = result.get("status") or "running"
        if status != "done":
            return {"status": status, "success": False, "registered": False}
        success = result.get("success") is True and not result.get("error")
        registered = False
        if success:
            registered = self._register(normalize_mac(mac), player_name)
        return {
            "status": "done",
            "success": success,
            "registered": registered,
            "error": result.get("error"),
            "mac": normalize_mac(mac),
        }

    def connect(self, mac: str) -> dict:
        mac = normalize_mac(mac)
        self.bridge.reconnect(mac)
        return {"ok": True, "mac": mac}

    def disconnect(self, mac: str) -> dict:
        mac = normalize_mac(mac)
        self.bridge.disconnect(mac)
        return {"ok": True, "mac": mac}

    def forget(self, mac: str) -> dict:
        mac = normalize_mac(mac)
        config, changed = remove_device(self.bridge.config(), mac)
        if changed:
            self.bridge.save_config(config)
        self.bridge.remove(mac)
        if changed:
            self._restart_quietly()
        return {"ok": True, "mac": mac}

    def set_bridge_hostname(self, hostname: str) -> None:
        config, changed = set_bridge_name(self.bridge.config(), hostname)
        if changed:
            self.bridge.save_config(config)

    def _register(self, mac: str, player_name: str) -> bool:
        config, changed = upsert_device(self.bridge.config(), mac, player_name or mac)
        if not changed:
            return True
        self.bridge.save_config(config)
        self._restart_quietly()
        return True

    def _fix_audio_once(self, mac: str) -> None:
        if not mac or self._audio_fix is None:
            return
        now = time.monotonic()
        if now - self._audio_fixed_at < 60:
            return
        self._audio_fixed_at = now
        try:
            self._audio_fix(mac)
        except Exception:
            return

    def _restart_players_once(self) -> None:
        now = time.monotonic()
        if now - self._players_restarted_at < 60:
            return
        self._players_restarted_at = now
        if self._restarter is not None:
            try:
                self._restarter()
                return
            except Exception:
                pass
        self._restart_quietly()

    def _restart_quietly(self) -> None:
        try:
            self.bridge.restart()
        except BridgeError:
            return

    def _safe_status(self) -> dict | None:
        try:
            return self.bridge.status()
        except BridgeError:
            return None

    def _safe_config(self) -> dict:
        try:
            return self.bridge.config()
        except BridgeError:
            return {}

    def _safe_paired(self) -> list:
        try:
            return self.bridge.paired()
        except BridgeError:
            return []
