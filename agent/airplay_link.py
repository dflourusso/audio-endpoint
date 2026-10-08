"""Cria a saída de cada AirPlay e liga só à caixa Bluetooth daquele MAC."""

import json
import sys
import time
from pathlib import Path

DEVICES_PATH = Path("/run/audio-endpoint/airplay/devices.json")


def _load_json(text: str):
    start = next((index for index, char in enumerate(text) if char in "[{"), None)
    if start is None:
        raise ValueError("sem json")
    return json.loads(text[start:])


def parse_sinks(text: str) -> list[dict]:
    payload = _load_json(text)
    rows = payload if isinstance(payload, list) else payload.get("sinks", [])
    sinks = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        name = str(row.get("name") or properties.get("node.name") or "")
        if not name:
            continue
        sinks.append(
            {
                "index": row.get("index"),
                "name": name,
                "state": str(row.get("state") or "").upper(),
            }
        )
    return sinks


def parse_inputs(text: str) -> list[dict]:
    payload = _load_json(text)
    rows = payload if isinstance(payload, list) else payload.get("sinkInputs", payload.get("sink-inputs", []))
    inputs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        owner = row.get("ownerModule", row.get("owner_module", row.get("module")))
        inputs.append(
            {
                "index": row.get("index"),
                "sink": row.get("sink"),
                "mute": bool(row.get("mute")),
                "owner_module": owner,
                "application": str(properties.get("application.name") or ""),
                "media": str(properties.get("media.name") or ""),
            }
        )
    return inputs


def parse_modules(text: str) -> list[dict]:
    payload = _load_json(text)
    rows = payload if isinstance(payload, list) else payload.get("modules", [])
    modules = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        properties = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        modules.append(
            {
                "index": row.get("index"),
                "name": str(row.get("name") or ""),
                "argument": str(row.get("argument") or properties.get("argument") or ""),
            }
        )
    return modules


def _argument_value(argument: str, key: str) -> str:
    prefix = f"{key}="
    for part in argument.split():
        if part.startswith(prefix):
            return part.split("=", 1)[1]
    return ""


def parse_loopbacks(modules: list[dict]) -> list[dict]:
    found = []
    for row in modules:
        if row["name"] != "module-loopback" or "source=airplay_" not in row["argument"]:
            continue
        found.append(
            {
                "index": row["index"],
                "source": _argument_value(row["argument"], "source"),
                "sink_name": _argument_value(row["argument"], "sink"),
            }
        )
    return found


def read_speakers(path: Path = DEVICES_PATH) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    speakers = []
    for item in data:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        speakers.append({"token": token, "playing": bool(item.get("playing"))})
    return speakers


def _bluez_for(sinks: list[dict], token: str) -> dict | None:
    needle = token.upper()
    matches = [sink for sink in sinks if "bluez" in sink["name"].lower() and needle in sink["name"].upper()]
    return matches[0] if matches else None


def _is_loopback(item: dict, loopback_module) -> bool:
    if loopback_module is not None and item.get("owner_module") is not None:
        if str(item.get("owner_module")) == str(loopback_module):
            return True
    label = f"{item.get('application', '')} {item.get('media', '')}".lower()
    return "loopback" in label


def _unique(items: list) -> list:
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def decide(sinks: list[dict], inputs: list[dict], modules: list[dict], speakers: list[dict]) -> dict:
    loopbacks = parse_loopbacks(modules)
    sink_names = {sink["name"] for sink in sinks}
    wanted = {speaker["token"] for speaker in speakers}
    load_null = []
    unload = []
    load_loopback = []
    mute = []
    unmute = []

    for row in modules:
        if row["name"] != "module-null-sink" or "sink_name=airplay_" not in row["argument"]:
            continue
        sink_name = _argument_value(row["argument"], "sink_name")
        token = sink_name.removeprefix("airplay_")
        if token not in wanted and row.get("index") is not None:
            unload.append(row["index"])

    for speaker in speakers:
        token = speaker["token"]
        hold = f"airplay_{token}"
        hold_ready = hold in sink_names
        if not hold_ready:
            load_null.append(hold)
        source = f"{hold}.monitor"
        current = next((item for item in loopbacks if item["source"] == source), None)
        bluez = _bluez_for(sinks, token) if hold_ready else None
        if bluez is None:
            if current is not None and current.get("index") is not None:
                unload.append(current["index"])
            continue
        if current is None:
            load_loopback.append({"source": source, "sink": bluez["name"]})
        elif current.get("sink_name") != bluez["name"]:
            if current.get("index") is not None:
                unload.append(current["index"])
            load_loopback.append({"source": source, "sink": bluez["name"]})
        module = None if current is None or current.get("index") in unload else current.get("index")
        for item in inputs:
            if item.get("sink") != bluez["index"] or item.get("index") is None:
                continue
            if _is_loopback(item, module):
                continue
            if speaker["playing"] and not item.get("mute"):
                mute.append(item["index"])
            if not speaker["playing"] and item.get("mute"):
                unmute.append(item["index"])

    for item in loopbacks:
        token = item["source"].removeprefix("airplay_").removesuffix(".monitor")
        if token not in wanted and item.get("index") is not None:
            unload.append(item["index"])

    return {
        "load_null": load_null,
        "unload": _unique(unload),
        "load_loopback": load_loopback,
        "mute": _unique(mute),
        "unmute": _unique(unmute),
    }


def apply_once(run, devices_path: Path = DEVICES_PATH) -> None:
    sinks = parse_sinks(_output(run, ["pactl", "-f", "json", "list", "sinks"]))
    inputs = parse_inputs(_output(run, ["pactl", "-f", "json", "list", "sink-inputs"]))
    modules = parse_modules(_output(run, ["pactl", "-f", "json", "list", "modules"]))
    decision = decide(sinks, inputs, modules, read_speakers(devices_path))
    for index in decision["unload"]:
        run(["pactl", "unload-module", str(index)])
    for name in decision["load_null"]:
        run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                f"sink_name={name}",
                "rate=44100",
                "channels=2",
            ]
        )
    for item in decision["load_loopback"]:
        run(
            [
                "pactl",
                "load-module",
                "module-loopback",
                f"source={item['source']}",
                f"sink={item['sink']}",
                "source_dont_move=true",
                "sink_dont_move=true",
            ]
        )
    for index in decision["mute"]:
        run(["pactl", "set-sink-input-mute", str(index), "1"])
    for index in decision["unmute"]:
        run(["pactl", "set-sink-input-mute", str(index), "0"])


def _output(run, args: list[str]) -> str:
    completed = run(args)
    if getattr(completed, "returncode", 1) != 0:
        raise RuntimeError(getattr(completed, "stderr", "") or "pactl falhou")
    return completed.stdout


def main() -> None:
    once = "--once" in sys.argv[1:]

    def run(args: list[str]):
        import subprocess

        return subprocess.run(args, capture_output=True, text=True, check=False)

    if once:
        try:
            apply_once(run)
        except Exception:
            return
        return
    while True:
        try:
            apply_once(run)
        except Exception:
            pass
        time.sleep(1)


if __name__ == "__main__":
    main()
