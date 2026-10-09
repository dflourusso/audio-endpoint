"""Cria a saída de cada AirPlay e liga só à caixa Bluetooth daquele MAC."""

import json
import sys
import time
from pathlib import Path

DEVICES_PATH = Path("/run/audio-endpoint/airplay/devices.json")
READY_DIR = Path("/run/audio-endpoint/airplay/ready")


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
                "state": str(row.get("state") or "").upper(),
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
                "argument": row["argument"],
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


def airplay_application(token: str) -> str:
    return f"AirPlay {token}"


def _on_speaker(item: dict, bluez: dict) -> bool:
    sink = item.get("sink")
    if isinstance(sink, dict):
        if sink.get("index") is not None and str(sink.get("index")) == str(bluez.get("index")):
            return True
        name = str(sink.get("name") or "")
        return bool(name) and name == bluez.get("name")
    if sink is None:
        return False
    if bluez.get("index") is not None and str(sink) == str(bluez.get("index")):
        return True
    return str(sink) == bluez.get("name")


def _is_airplay(item: dict, token: str) -> bool:
    return item.get("application") == airplay_application(token)


def _airplay_stream(inputs: list[dict], token: str) -> dict | None:
    return next((item for item in inputs if _is_airplay(item, token)), None)


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
    move = []
    mute = []
    unmute = []

    for row in modules:
        if row["name"] != "module-null-sink" or "sink_name=airplay_" not in row["argument"]:
            continue
        sink_name = _argument_value(row["argument"], "sink_name")
        token = sink_name.removeprefix("airplay_")
        if token not in wanted and row.get("index") is not None:
            unload.append(row["index"])

    for item in loopbacks:
        if item.get("index") is not None:
            unload.append(item["index"])

    for speaker in speakers:
        token = speaker["token"]
        hold = f"airplay_{token}"
        if hold not in sink_names:
            load_null.append(hold)
        bluez = _bluez_for(sinks, token)
        stream = _airplay_stream(inputs, token)
        active = bool(speaker["playing"]) or (stream is not None and stream.get("state") == "RUNNING")
        if stream is not None and stream.get("index") is not None:
            if stream.get("mute"):
                unmute.append(stream["index"])
            if bluez is not None and not _on_speaker(stream, bluez):
                move.append({"index": stream["index"], "sink": bluez["name"]})
        if bluez is None:
            continue
        for item in inputs:
            if item.get("index") is None or not _on_speaker(item, bluez):
                continue
            if _is_airplay(item, token):
                continue
            if active and not item.get("mute"):
                mute.append(item["index"])
            if not active and item.get("mute"):
                unmute.append(item["index"])

    return {
        "load_null": load_null,
        "unload": _unique(unload),
        "load_loopback": [],
        "move": move,
        "mute": _unique(mute),
        "unmute": _unique(unmute),
    }


def mark_ready(speakers: list[dict], sinks: list[dict], ready_dir: Path = READY_DIR) -> None:
    names = {sink["name"] for sink in sinks}
    try:
        ready_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    wanted = set()
    for speaker in speakers:
        token = str(speaker.get("token") or "").strip()
        if not token:
            continue
        wanted.add(token)
        path = ready_dir / token
        try:
            if f"airplay_{token}" in names:
                if not path.exists():
                    path.write_text(token + "\n", encoding="utf-8")
            elif path.exists():
                path.unlink()
        except OSError:
            return
    try:
        for path in ready_dir.iterdir():
            if path.is_file() and path.name not in wanted:
                path.unlink()
    except OSError:
        return


def apply_once(run, devices_path: Path = DEVICES_PATH, ready_dir: Path = READY_DIR) -> None:
    sinks = parse_sinks(_output(run, ["pactl", "-f", "json", "list", "sinks"]))
    inputs = parse_inputs(_output(run, ["pactl", "-f", "json", "list", "sink-inputs"]))
    modules = parse_modules(_output(run, ["pactl", "-f", "json", "list", "modules"]))
    speakers = read_speakers(devices_path)
    mark_ready(speakers, sinks, ready_dir)
    decision = decide(sinks, inputs, modules, speakers)
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
    for item in decision["move"]:
        run(["pactl", "move-sink-input", str(item["index"]), item["sink"]])
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
