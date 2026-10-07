from app.bridge import BridgeClient, BridgeError
from app.fleet import remove_device, set_bridge_name, upsert_device
from app.validation import normalize_mac


def _name_of(device: dict) -> str:
    for key in ("name", "alias", "player_name"):
        value = device.get(key)
        if value:
            return str(value).strip()
    return ""


def merge_devices(status: dict, paired: list, config: dict) -> list[dict]:
    fleet = {
        str(device.get("mac", "")).upper(): device
        for device in (config.get("BLUETOOTH_DEVICES") or [])
        if isinstance(device, dict) and device.get("mac")
    }
    runtime = {}
    for device in status.get("devices") or []:
        if isinstance(device, dict) and device.get("mac"):
            runtime[str(device["mac"]).upper()] = device
    found: dict[str, dict] = {}
    for item in paired:
        if not isinstance(item, dict) or not item.get("mac"):
            continue
        mac = str(item["mac"]).upper()
        live = runtime.get(mac, {})
        saved = fleet.get(mac, {})
        connected = bool(item.get("connected") or live.get("bluetooth_connected") or live.get("connected"))
        found[mac] = {
            "mac": mac,
            "name": _name_of(item) or _name_of(saved) or _name_of(live) or mac,
            "player_name": saved.get("player_name") or _name_of(live) or _name_of(item) or mac,
            "paired": True,
            "connected": connected,
            "in_fleet": mac in fleet,
            "enabled": saved.get("enabled", True) if mac in fleet else False,
        }
    for mac, saved in fleet.items():
        if mac in found:
            continue
        live = runtime.get(mac, {})
        found[mac] = {
            "mac": mac,
            "name": saved.get("player_name") or _name_of(live) or mac,
            "player_name": saved.get("player_name") or mac,
            "paired": False,
            "connected": bool(live.get("bluetooth_connected") or live.get("connected")),
            "in_fleet": True,
            "enabled": bool(saved.get("enabled", True)),
        }
    return sorted(found.values(), key=lambda device: (not device["connected"], device["name"].lower()))


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
    def __init__(self, bridge: BridgeClient):
        self.bridge = bridge

    def status(self) -> dict:
        snapshot = self._safe_status()
        config = self._safe_config()
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
