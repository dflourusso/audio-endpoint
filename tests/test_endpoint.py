import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bluetooth_service import choose_adapter
from app.bridge import BridgeClient

from app.audio import list_outputs, select_output, OutputNotSupported
from app.fleet import remove_device, upsert_device
from app.main import create_app
from app.redact import redact
from app.settings import Settings
from app.system_info import cpu_percent, memory
from app.validation import ValidationError, normalize_hostname, normalize_mac
from audio_endpoint_agent import HostControl, dispatch, update_hosts


def test_hostname_accepts_room_names():
    assert normalize_hostname("audio-sala") == "audio-sala"


def test_hostname_rejects_dots_and_uppercase():
    with pytest.raises(ValidationError):
        normalize_hostname("Audio.Sala")


def test_mac_is_normalized():
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"


def test_upsert_device_registers_once():
    config, changed = upsert_device({}, "AA:BB:CC:DD:EE:FF", "Sala")
    assert changed
    again, changed_again = upsert_device(config, "AA:BB:CC:DD:EE:FF", "Sala")
    assert not changed_again
    assert len(again["BLUETOOTH_DEVICES"]) == 1


def test_remove_device():
    config, _ = upsert_device({}, "AA:BB:CC:DD:EE:FF", "Sala")
    updated, changed = remove_device(config, "AA:BB:CC:DD:EE:FF")
    assert changed
    assert updated["BLUETOOTH_DEVICES"] == []


def test_local_cards_are_visible_but_not_selectable():
    text = " 0 [HDMI           ]: HDA-Intel - HDA Intel HDMI\n 1 [Device         ]: USB-Audio - USB Audio\n"
    outputs = list_outputs(text)
    assert outputs[0]["id"] == "bluetooth"
    assert outputs[0]["selectable"] is True
    kinds = {item["kind"] for item in outputs[1:]}
    assert kinds == {"hdmi", "usb"}
    assert all(item["selectable"] is False for item in outputs[1:])
    with pytest.raises(OutputNotSupported):
        select_output("alsa:1")


def test_redact_hides_tokens():
    assert redact({"MA_API_TOKEN": "segredo", "player": "sala"})["MA_API_TOKEN"] == "***"


def test_cpu_and_memory_parsers():
    samples = iter(["cpu  10 0 0 90 0 0 0 0\n", "cpu  20 0 0 100 0 0 0 0\n"])
    percent = cpu_percent(lambda: next(samples), sleep=lambda _delay: None)
    assert percent == 50.0
    mem = memory("MemTotal: 1024 kB\nMemAvailable: 512 kB\n")
    assert mem["percent"] == 50.0


def test_hosts_file_gains_mdns_name():
    updated = update_hosts("127.0.0.1 localhost\n", "audio-sala")
    assert "127.0.1.1\taudio-sala" in updated


class FakeBridge:
    def __init__(self):
        self.config_data = {"BLUETOOTH_DEVICES": [], "BRIDGE_NAME": ""}
        self.removed = []
        self.restarted = 0
        self.adapters_data = [{"id": "hci0", "mac": "11:22:33:44:55:66", "name": "onboard", "powered": True}]
        self.scan_calls = []
        self.pair_calls = []

    def version(self):
        return {"version": "2.75.0"}

    def status(self):
        return {
            "connected": True,
            "ma_connected": False,
            "bluetooth_connected": False,
            "player_name": "Sala",
            "devices": [],
            "startup_progress": {"status": "complete", "phase": "ready"},
        }

    def health(self):
        return {"ok": True}

    def runtime_info(self):
        return {"runtime_mode": "production"}

    def config(self):
        return json.loads(json.dumps(self.config_data))

    def save_config(self, config):
        self.config_data = config
        return {}

    def adapters(self):
        return list(self.adapters_data)

    def scan(self, adapter):
        self.scan_calls.append(adapter)
        return {"job_id": "job-123456"}

    def scan_result(self, job_id):
        return {
            "status": "done",
            "devices": [{"mac": "AA:BB:CC:DD:EE:FF", "name": "Caixa", "adapter": "hci0"}],
        }

    def paired(self):
        return []

    def pair_new(self, mac, adapter=""):
        self.pair_calls.append((mac, adapter))
        return {"job_id": "pair-123456"}

    def pair_result(self, job_id):
        return {"status": "done", "success": True, "mac": "AA:BB:CC:DD:EE:FF"}

    def reconnect(self, mac):
        return {"ok": True}

    def disconnect(self, mac):
        return {"ok": True}

    def remove(self, mac):
        self.removed.append(mac)
        return {"ok": True}

    def restart(self):
        self.restarted += 1
        return {}

    def logs(self, lines):
        return {"logs": "bridge ok"}


class FakeAgent:
    def __init__(self):
        self.calls = []

    def call(self, action, **fields):
        self.calls.append((action, fields))
        if action == "logs":
            return {"ok": True, "text": "agente"}
        if action == "update-status":
            return {"ok": True, "state": "idle"}
        return {"ok": True, "message": "ok", "hostname": fields.get("hostname")}


def build_client(agent=None):
    settings = Settings(
        bridge_url="http://bridge.invalid",
        bridge_token="",
        api_token="",
        agent_socket="/tmp/audio-endpoint-test.sock",
        config_dir=__import__("pathlib").Path("/tmp"),
        data_dir=__import__("pathlib").Path("/tmp"),
        host_proc=__import__("pathlib").Path("/proc"),
        host_sys=__import__("pathlib").Path("/sys"),
        hostname_file=__import__("pathlib").Path("/etc/hostname"),
        os_release_file=__import__("pathlib").Path("/etc/os-release"),
        web_dist=__import__("pathlib").Path("/tmp/missing-web"),
        project_version="0.1.0",
        web_port=80,
    )
    bridge = FakeBridge()
    agent = agent or FakeAgent()
    app = create_app(settings, bridge, agent)
    return TestClient(app), bridge, agent


def test_api_rejects_a_hostname_with_a_dot():
    client, _bridge, agent = build_client()
    response = client.post("/api/system/hostname", json={"hostname": "audio.sala"})
    assert response.status_code == 400
    assert not any(call[0] == "set-hostname" for call in agent.calls)


def test_hostname_is_stored_in_lowercase():
    client, bridge, agent = build_client()
    response = client.post("/api/system/hostname", json={"hostname": "Audio-Sala"})
    assert response.status_code == 200
    assert response.json()["hostname"] == "audio-sala"
    hostname_calls = [call for call in agent.calls if call[0] == "set-hostname"]
    assert hostname_calls == [("set-hostname", {"hostname": "audio-sala"})]
    assert bridge.config_data["BRIDGE_NAME"] == "audio-sala"


def test_scan_sends_the_only_adapter():
    client, bridge, _agent = build_client()
    response = client.post("/api/bluetooth/scan")
    assert response.status_code == 200
    assert bridge.scan_calls == ["hci0"]
    found = client.get("/api/bluetooth/scan/job-123456")
    assert found.json()["devices"][0]["adapter"] == "hci0"
    paired = client.post(
        "/api/bluetooth/pair",
        json={"mac": "aa:bb:cc:dd:ee:ff", "name": "Caixa", "adapter": "hci0"},
    )
    assert paired.status_code == 200
    assert bridge.pair_calls == [("AA:BB:CC:DD:EE:FF", "hci0")]


def test_scan_without_an_adapter_stays_in_portuguese():
    client, bridge, _agent = build_client()
    bridge.adapters_data = []
    response = client.post("/api/bluetooth/scan")
    assert response.status_code == 502
    assert "rádio" in response.json()["message"]
    assert bridge.scan_calls == []


def test_scan_prefers_the_first_powered_adapter():
    assert choose_adapter(
        [
            {"id": "hci0", "powered": False},
            {"id": "hci1", "powered": True},
            {"id": "hci2", "powered": True},
        ]
    ) == "hci1"


def test_bridge_scan_posts_adapter_and_audio_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"job_id": "job-123456"})

    client = BridgeClient("http://bridge.invalid", transport=httpx.MockTransport(handler))
    assert client.scan("hci0")["job_id"] == "job-123456"
    assert seen == {"path": "/api/bt/scan", "body": {"adapter": "hci0", "audio_only": True}}


def test_pair_result_registers_the_speaker_in_the_fleet():
    client, bridge, _agent = build_client()
    response = client.get("/api/bluetooth/pair/pair-123456", params={"mac": "aa:bb:cc:dd:ee:ff", "name": "Sala"})
    assert response.status_code == 200
    assert response.json()["registered"] is True
    assert bridge.config_data["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert bridge.restarted == 1


def test_setup_mode_blocks_reboot_and_allows_wifi_status():
    class SetupAgent(FakeAgent):
        def call(self, action, **fields):
            if action == "wifi-status":
                return {"ok": True, "mode": "access-point", "address": "192.168.4.1", "ssid": "audio-setup"}
            return super().call(action, **fields)

    client, _bridge, _agent = build_client(SetupAgent())
    blocked = client.post("/api/maintenance/reboot", json={})
    assert blocked.status_code == 403
    opened = client.get("/api/wifi/status")
    assert opened.status_code == 200
    assert opened.json()["address"] == "192.168.4.1"
    client, _bridge, _agent = build_client()
    response = client.post("/api/audio/output", json={"id": "alsa:0"})
    assert response.status_code == 409
    assert response.json()["error"] == "output_not_supported"


def test_update_does_not_pull_a_dirty_tree(tmp_path):
    calls = []

    def runner(args):
        calls.append(args)
        class Result:
            returncode = 0
            stdout = " M README.md\n" if "status" in args else ""
            stderr = ""
        return Result()

    control = HostControl(tmp_path, runner)
    control._update()
    assert (tmp_path / "data" / "update-status.json").is_file()
    status = json.loads((tmp_path / "data" / "update-status.json").read_text())
    assert status["state"] == "failed"
    assert not any(args[0] == "docker" for args in calls)


def test_agent_rejects_unknown_actions():
    response = dispatch({"action": "bash"}, HostControl("/tmp"))
    assert response["ok"] is False
    assert response["error"] == "unknown_action"
