import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bluetooth_service import (
    BluetoothService,
    auto_released_player,
    bluetooth_recovery,
    choose_adapter,
    merge_devices,
    sinkless_speaker,
)
from app.routers.status import describe_playback
from app.bridge import BridgeClient
from app.settings import read_project_version

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


def test_project_version_comes_from_the_version_file():
    assert read_project_version() == "0.2.0"


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

    def reconnect(self, mac, player_name=""):
        self.reconnected = (mac, player_name)
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


def build_client(agent=None, webhook_url=""):
    import tempfile
    root = Path(tempfile.mkdtemp())
    settings = Settings(
        bridge_url="http://bridge.invalid",
        bridge_token="",
        api_token="",
        agent_socket="/tmp/audio-endpoint-test.sock",
        config_dir=root / "config",
        data_dir=Path("/tmp"),
        host_proc=Path("/proc"),
        host_sys=Path("/sys"),
        hostname_file=Path("/etc/hostname"),
        os_release_file=Path("/etc/os-release"),
        web_dist=Path("/tmp/missing-web"),
        project_version="0.1.0",
        web_port=80,
        airplay_webhook_url=webhook_url,
        airplay_flag=root / "airplay-playing",
        airplay_run=root / "airplay",
        supervise_airplay=False,
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


def test_playing_session_is_visible_for_one_speaker():
    status = {
        "player_name": "JBL Bar 2.1 @ audio-suite",
        "bluetooth_mac": "88:D0:39:0D:4B:FC",
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": True,
        "ma_connected": False,
        "has_sink": False,
        "sink_name": "",
    }
    view = describe_playback(status)
    assert view["session_connected"] is True
    assert view["players"][0]["playing"] is True
    assert view["players"][0]["has_sink"] is False
    assert sinkless_speaker(status) == "88:D0:39:0D:4B:FC"


def test_connected_speaker_without_audio_asks_for_the_music_profile():
    mac = "88:D0:39:0D:4B:FC"
    bridge = type("Bridge", (), {})()
    bridge.config_data = {"BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "JBL"}]}

    def status():
        return {
            "bluetooth_mac": mac,
            "bluetooth_connected": True,
            "player_name": "JBL",
            "has_sink": False,
            "sink_name": "",
        }

    bridge.status = status
    bridge.config = lambda: bridge.config_data
    bridge.paired = lambda: [{"mac": mac, "name": "JBL"}]
    fixed = []
    service = BluetoothService(bridge, audio_fix=lambda value: fixed.append(value))
    service.status()
    assert fixed == [mac]
    service.status()
    assert fixed == [mac]


def test_auto_released_speaker_is_reclaimed_once():
    bridge = type("Bridge", (), {})()
    bridge.config_data = {
        "BLUETOOTH_DEVICES": [{"mac": "88:D0:39:0D:4B:FC", "player_name": "JBL", "released": True, "released_by": "auto"}]
    }

    def status():
        return {
            "player_name": "JBL Bar 2.1 @ audio-suite",
            "bluetooth_mac": "88:D0:39:0D:4B:FC",
            "bluetooth_connected": False,
            "has_sink": True,
            "sink_name": "bluez_output.88_D0_39_0D_4B_FC.1",
            "bt_released_by": "auto",
        }

    bridge.status = status
    bridge.config = lambda: bridge.config_data
    bridge.paired = lambda: []
    reclaimed = []
    bridge.set_bt_management = lambda name, enabled: reclaimed.append((name, enabled)) or {"success": True}
    service = BluetoothService(bridge)
    service.status()
    service.status()
    assert reclaimed == [("JBL Bar 2.1 @ audio-suite", True)]
    assert auto_released_player({"bt_released_by": "user", "player_name": "JBL"}, {}) == ""


def test_auto_released_player_uses_the_live_name_when_the_flag_is_only_in_config():
    mac = "88:D0:39:0D:4B:FC"
    status = {
        "player_name": "JBL Bar 2.1 @ audio-suite",
        "bluetooth_mac": mac,
        "bluetooth_connected": False,
    }
    config = {
        "BLUETOOTH_DEVICES": [
            {"mac": mac, "player_name": "JBL Bar 2.1", "released": True, "released_by": "auto"}
        ]
    }
    assert auto_released_player(status, config) == "JBL Bar 2.1 @ audio-suite"
    assert bluetooth_recovery(status, config, set()) == [
        {"mac": mac, "player_name": "JBL Bar 2.1 @ audio-suite", "reclaim": True, "urgent": False}
    ]
    connected = dict(status)
    connected["bluetooth_connected"] = True
    assert bluetooth_recovery(connected, config, set()) == []


def test_play_without_a_bluetooth_link_asks_for_reconnect():
    mac = "88:D0:39:0D:4B:FC"
    status = {
        "player_name": "JBL Bar 2.1 @ audio-suite",
        "bluetooth_mac": mac,
        "bluetooth_connected": False,
        "playing": False,
    }
    config = {"BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "JBL Bar 2.1", "released": False}]}
    assert bluetooth_recovery(status, config, {mac}) == [
        {"mac": mac, "player_name": "JBL Bar 2.1 @ audio-suite", "reclaim": False, "urgent": True}
    ]
    status["playing"] = True
    assert bluetooth_recovery(status, config, set())[0]["urgent"] is True
    idle = bluetooth_recovery(
        {"player_name": "JBL Bar 2.1 @ audio-suite", "bluetooth_mac": mac, "bluetooth_connected": False, "playing": False},
        config,
        set(),
    )
    assert idle == []


def test_console_pipewire_is_masked_so_the_speaker_stays_with_the_endpoint(tmp_path):
    home = tmp_path / "orangepi"
    home.mkdir()
    (tmp_path / ".env").write_text("AUDIO_UID=997\n", encoding="utf-8")
    calls = []

    def runner(args):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = f"orangepi:x:1000:1000::/Users/unused:/bin/bash\n" if args[:2] == ["getent", "passwd"] else ""
            stderr = ""

        if args[:2] == ["getent", "passwd"]:
            Result.stdout = f"orangepi:x:1000:1000::{home}:/bin/bash\n"
        return Result()

    HostControl(tmp_path, runner).claim_bluetooth_audio()
    link = home / ".config" / "systemd" / "user" / "wireplumber.service"
    assert link.is_symlink()
    assert link.readlink() == Path("/dev/null")
    assert any(args[:3] == ["runuser", "-u", "audioendpoint"] and "wireplumber.service" in args for args in calls)


def test_bluetooth_audio_rejects_a_bad_mac(tmp_path):
    calls = []

    def runner(args):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    result = dispatch({"action": "bluetooth-audio", "mac": "caixa"}, HostControl(tmp_path, runner))
    assert result["ok"] is False
    assert calls == []


def test_the_playing_speaker_is_the_one_on_the_panel():
    devices = merge_devices(
        {
            "devices": [
                {
                    "bluetooth_mac": "88:D0:39:0D:4B:FC",
                    "bluetooth_connected": True,
                    "player_name": "JBL Bar 2.1 @ audio-suite",
                    "playing": False,
                },
                {
                    "bluetooth_mac": "4C:14:84:D5:47:59",
                    "bluetooth_connected": True,
                    "player_name": "SOM-pop @ audio-suite",
                    "playing": True,
                },
            ]
        },
        [
            {"mac": "88:D0:39:0D:4B:FC", "name": "JBL Bar 2.1"},
            {"mac": "4C:14:84:D5:47:59", "name": "SOM-pop"},
        ],
        {
            "BLUETOOTH_DEVICES": [
                {"mac": "88:D0:39:0D:4B:FC", "player_name": "JBL Bar 2.1"},
                {"mac": "4C:14:84:D5:47:59", "player_name": "SOM-pop"},
            ]
        },
    )
    playing = [device for device in devices if device["playing"]]
    assert [device["name"] for device in playing] == ["SOM-pop"]
    assert playing[0]["bridge_player"] == "SOM-pop @ audio-suite"
    from app.bluetooth_service import connected_device

    assert connected_device(devices)["name"] == "SOM-pop"


def test_connect_names_the_player_when_several_are_paired():
    bridge = FakeBridge()
    BluetoothService(bridge).connect("4C:14:84:D5:47:59", "SOM-pop @ audio-suite")
    assert bridge.reconnected == ("4C:14:84:D5:47:59", "SOM-pop @ audio-suite")


def test_one_speaker_reports_the_bluetooth_link():
    devices = merge_devices(
        {
            "bluetooth_mac": "88:D0:39:0D:4B:FC",
            "bluetooth_connected": True,
            "player_name": "JBL Bar 2.1",
        },
        [{"mac": "88:D0:39:0D:4B:FC", "name": "JBL Bar 2.1"}],
        {"BLUETOOTH_DEVICES": [{"mac": "88:D0:39:0D:4B:FC", "player_name": "JBL Bar 2.1", "enabled": True}]},
    )
    assert devices[0]["connected"] is True
    assert devices[0]["announced"] is True


def test_saved_speaker_without_a_running_player_asks_for_a_restart():
    bridge = type("Bridge", (), {})()
    bridge.config_data = {"BLUETOOTH_DEVICES": [{"mac": "88:D0:39:0D:4B:FC", "player_name": "JBL Bar 2.1"}]}
    bridge.restarts = []

    def status():
        return {"devices": []}

    def config():
        return bridge.config_data

    def paired():
        return [{"mac": "88:D0:39:0D:4B:FC", "name": "JBL Bar 2.1"}]

    bridge.status = status
    bridge.config = config
    bridge.paired = paired
    service = BluetoothService(bridge, restarter=lambda: bridge.restarts.append("bridge"))
    first = service.status()
    assert first["devices"][0]["connected"] is False
    assert first["devices"][0]["announced"] is False
    assert bridge.restarts == ["bridge"]
    service.status()
    assert bridge.restarts == ["bridge"]


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


def test_update_pulls_the_bridge_and_builds_the_local_image(tmp_path):
    calls = []

    def runner(args):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    HostControl(tmp_path, runner)._update()
    docker_calls = [args for args in calls if args and args[0] == "docker"]
    assert docker_calls[0][-3:] == ["pull", "sendspin-bridge", "shairport-sync"]
    assert docker_calls[1][-3:] == ["up", "-d", "--build"]
    status = json.loads((tmp_path / "data" / "update-status.json").read_text())
    assert status["state"] == "succeeded"


def test_agent_unit_keeps_the_socket_directory():
    text = (Path(__file__).resolve().parents[1] / "agent" / "audio-endpoint-agent.service").read_text(encoding="utf-8")
    assert "RuntimeDirectory=" not in text
    assert "/run/audio-endpoint" in text
    policy = (Path(__file__).resolve().parents[1] / "agent" / "wireplumber" / "51-a2dp-sink.lua").read_text(encoding="utf-8")
    assert "a2dp_source" in policy


def test_agent_rejects_unknown_actions():
    response = dispatch({"action": "bash"}, HostControl("/tmp"))
    assert response["ok"] is False
    assert response["error"] == "unknown_action"
