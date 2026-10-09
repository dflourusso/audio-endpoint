"""Cala o Sendspin da caixa enquanto o AirPlay dela está ativo.

O Shairport abre o sink Bluetooth pelo nome. Este processo emudece
os outros fluxos dessa caixa, com wpctl, sobe o volume do sink se ele
estiver baixo demais para se ouvir, e devolve tudo quando a sessão acaba.
"""

import json
import os
import sys
import time
from pathlib import Path

DEVICES_PATH = Path("/run/audio-endpoint/airplay/devices.json")
READY_DIR = Path("/run/audio-endpoint/airplay/ready")

STREAM = "Stream/Output/Audio"
SINK_VOLUME_FLOOR = 0.40
SINK_VOLUME_TARGET = 0.80


def bluez_sink_name(token: str) -> str:
    return f"bluez_output.{token}.1"


def airplay_application(token: str) -> str:
    return f"AirPlay {token}"


def _json_documents(text: str) -> list:
    decoder = json.JSONDecoder()
    index = 0
    documents = []
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        document, index = decoder.raw_decode(text, index)
        documents.append(document)
    return documents


def parse_dump(text: str) -> tuple[list[dict], list[dict]]:
    lists = [document for document in _json_documents(text) if isinstance(document, list)]
    if not lists:
        raise ValueError("pw-dump sem lista")
    # O pw-dump às vezes cola um segundo JSON depois do registro completo.
    payload = max(lists, key=len)
    nodes = []
    links = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        if kind == "PipeWire:Interface:Node":
            props = info.get("props") if isinstance(info.get("props"), dict) else {}
            target = props.get("target.object") or props.get("node.target") or props.get("pulse.device") or ""
            nodes.append(
                {
                    "id": item.get("id"),
                    "name": str(props.get("node.name") or ""),
                    "media": str(props.get("media.class") or ""),
                    "application": str(props.get("application.name") or ""),
                    "target": str(target),
                    "state": str(info.get("state") or "").lower(),
                }
            )
        elif kind == "PipeWire:Interface:Link":
            links.append(
                {
                    "output": info.get("output-node-id"),
                    "input": info.get("input-node-id"),
                }
            )
    return nodes, links


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


def _sink_id(nodes: list[dict], sink_name: str):
    for node in nodes:
        if node.get("name") == sink_name:
            return node.get("id")
    return None


def _linked_to(node_id, sink_id, links: list[dict]) -> bool:
    if node_id is None or sink_id is None:
        return False
    return any(link.get("output") == node_id and link.get("input") == sink_id for link in links)


def _on_sink(node: dict, sink_name: str, sink_id, links: list[dict]) -> bool:
    if node.get("media") != STREAM:
        return False
    target = str(node.get("target") or "")
    if target == sink_name or (sink_id is not None and target == str(sink_id)):
        return True
    if _linked_to(node.get("id"), sink_id, links):
        return True
    blob = f"{node.get('name') or ''} {node.get('application') or ''}"
    return sink_name in blob


def _streams(nodes: list[dict], links: list[dict], token: str) -> list[dict]:
    sink_name = bluez_sink_name(token)
    sink_id = _sink_id(nodes, sink_name)
    return [node for node in nodes if _on_sink(node, sink_name, sink_id, links)]


def decide(
    nodes: list[dict],
    speakers: list[dict],
    saved: set[str] | None = None,
    links: list[dict] | None = None,
) -> dict:
    saved = set(saved or [])
    links = list(links or [])
    names = {node["name"] for node in nodes}
    silence = []
    restore = []
    present = []
    boost = []
    for speaker in speakers:
        token = speaker["token"]
        sink_name = bluez_sink_name(token)
        if sink_name in names:
            present.append(token)
        streams = _streams(nodes, links, token)
        airplay = [node for node in streams if node.get("application") == airplay_application(token)]
        active = bool(speaker.get("playing")) or any(node.get("state") == "running" for node in airplay)
        if active:
            sink_id = _sink_id(nodes, sink_name)
            if sink_id is not None:
                boost.append({"id": sink_id, "token": token})
            for node in streams:
                if node in airplay or node.get("id") is None:
                    continue
                silence.append({"id": node["id"], "token": token})
        elif token in saved:
            restore.append(token)
    return {"silence": silence, "restore": restore, "present": present, "boost": boost}


def prepare_ready_dir(path: Path, uid: int | None = None, gid: int | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if uid is None:
        return
    os.chown(path, uid, -1 if gid is None else gid)


def mark_ready(speakers: list[dict], nodes: list[dict], ready_dir: Path = READY_DIR) -> None:
    names = {node["name"] for node in nodes}
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
            if bluez_sink_name(token) in names:
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


class VolumeMemory:
    def __init__(self) -> None:
        self.by_token: dict[str, float] = {}
        self.sink_by_token: dict[str, float] = {}

    def remember(self, token: str, volume: float | None) -> None:
        if token in self.by_token or volume is None or volume <= 0:
            return
        self.by_token[token] = volume

    def pop(self, token: str) -> float | None:
        return self.by_token.pop(token, None)

    def remember_sink(self, token: str, volume: float | None) -> None:
        if token in self.sink_by_token or volume is None or volume <= 0:
            return
        self.sink_by_token[token] = volume

    def pop_sink(self, token: str) -> float | None:
        return self.sink_by_token.pop(token, None)


def read_volume(text: str) -> float | None:
    for token in text.split():
        try:
            value = float(token)
        except ValueError:
            continue
        if 0 <= value <= 2:
            return value
    return None


def format_volume(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def apply_once(
    run,
    devices_path: Path = DEVICES_PATH,
    ready_dir: Path = READY_DIR,
    memory: VolumeMemory | None = None,
) -> None:
    if memory is None:
        memory = VolumeMemory()
    nodes, links = parse_dump(_output(run, ["pw-dump"]))
    speakers = read_speakers(devices_path)
    mark_ready(speakers, nodes, ready_dir)
    decision = decide(
        nodes,
        speakers,
        saved=set(memory.by_token) | set(memory.sink_by_token),
        links=links,
    )
    for item in decision["silence"]:
        node_id = str(item["id"])
        memory.remember(item["token"], read_volume(_text(run, ["wpctl", "get-volume", node_id])))
        run(["wpctl", "set-mute", node_id, "1"])
        run(["wpctl", "set-volume", node_id, "0"])
    for item in decision["boost"]:
        node_id = str(item["id"])
        current = read_volume(_text(run, ["wpctl", "get-volume", node_id]))
        memory.remember_sink(item["token"], current)
        if current is None or current < SINK_VOLUME_FLOOR:
            # region agent log
            print(
                f"airplay-link: sink-volume {item['token']} {current} -> {SINK_VOLUME_TARGET}",
                file=sys.stderr,
                flush=True,
            )
            # endregion
            run(["wpctl", "set-mute", node_id, "0"])
            run(["wpctl", "set-volume", node_id, format_volume(SINK_VOLUME_TARGET)])
    for token in decision["restore"]:
        volume = memory.pop(token)
        sink_volume = memory.pop_sink(token)
        for node in _streams(nodes, links, token):
            if node.get("id") is None or node.get("application") == airplay_application(token):
                continue
            node_id = str(node["id"])
            if volume is not None:
                run(["wpctl", "set-volume", node_id, format_volume(volume)])
            run(["wpctl", "set-mute", node_id, "0"])
        sink_id = _sink_id(nodes, bluez_sink_name(token))
        if sink_id is not None and sink_volume is not None:
            run(["wpctl", "set-volume", str(sink_id), format_volume(sink_volume)])


def _output(run, args: list[str]) -> str:
    completed = run(args)
    if getattr(completed, "returncode", 1) != 0:
        raise RuntimeError(getattr(completed, "stderr", "") or "pw-dump falhou")
    return completed.stdout or ""


def _text(run, args: list[str]) -> str:
    completed = run(args)
    return getattr(completed, "stdout", "") or ""


def main() -> None:
    once = "--once" in sys.argv[1:]
    memory = VolumeMemory()

    def run(args: list[str]):
        import subprocess

        return subprocess.run(args, capture_output=True, text=True, check=False)

    if once:
        try:
            apply_once(run, memory=memory)
        except Exception as exc:
            print(f"airplay-link: {exc}", file=sys.stderr)
        return
    while True:
        try:
            apply_once(run, memory=memory)
        except Exception as exc:
            print(f"airplay-link: {exc}", file=sys.stderr)
        time.sleep(1)


if __name__ == "__main__":
    main()
