from app.validation import normalize_mac


def device_mac(device: dict) -> str:
    return str(device.get("mac", "")).strip().upper()


def upsert_device(config: dict, mac: str, player_name: str) -> tuple[dict, bool]:
    mac = normalize_mac(mac)
    name = (player_name or mac).strip() or mac
    devices = list(config.get("BLUETOOTH_DEVICES") or [])
    changed = False
    found = False
    updated: list[dict] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        if device_mac(device) == mac:
            found = True
            current = dict(device)
            if current.get("player_name") != name or current.get("enabled") is False:
                current["player_name"] = name
                current["enabled"] = True
                changed = True
            updated.append(current)
        else:
            updated.append(dict(device))
    if not found:
        updated.append(
            {
                "mac": mac,
                "player_name": name,
                "enabled": True,
                "static_delay_ms": 300,
            }
        )
        changed = True
    new_config = dict(config)
    new_config["BLUETOOTH_DEVICES"] = updated
    return new_config, changed


def remove_device(config: dict, mac: str) -> tuple[dict, bool]:
    mac = normalize_mac(mac)
    devices = [device for device in (config.get("BLUETOOTH_DEVICES") or []) if isinstance(device, dict)]
    kept = [dict(device) for device in devices if device_mac(device) != mac]
    changed = len(kept) != len(devices)
    new_config = dict(config)
    new_config["BLUETOOTH_DEVICES"] = kept
    return new_config, changed


def set_bridge_name(config: dict, hostname: str) -> tuple[dict, bool]:
    new_config = dict(config)
    changed = new_config.get("BRIDGE_NAME") != hostname
    new_config["BRIDGE_NAME"] = hostname
    return new_config, changed
