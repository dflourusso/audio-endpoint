import json
from pathlib import Path

import httpx

from airplay_link import decide, parse_inputs, parse_modules, parse_sinks
from app.airplay import AirPlaySwitch
from app.airplay_speakers import SpeakerStore, clean_webhook
from audio_endpoint_agent import HostControl, dispatch
from test_endpoint import build_client

SALA = "AA:BB:CC:DD:EE:01"
SUITE = "AA:BB:CC:DD:EE:02"


def _store(tmp_path):
    store = SpeakerStore(tmp_path / "config", tmp_path / "run")
    store.sync(
        [
            {"mac": SALA, "player_name": "Sala", "in_fleet": True},
            {"mac": SUITE, "player_name": 'Suíte "A"', "in_fleet": True},
        ],
        "audio-sala",
    )
    return store


def test_each_speaker_gets_its_own_airplay(tmp_path):
    store = _store(tmp_path)
    directory = tmp_path / "config" / "shairport"
    files = sorted(directory.glob("*.conf"))
    assert [path.name for path in files] == [
        "AA_BB_CC_DD_EE_01.conf",
        "AA_BB_CC_DD_EE_02.conf",
    ]
    sala = files[0].read_text(encoding="utf-8")
    suite = files[1].read_text(encoding="utf-8")
    assert 'name = "Sala @ audio-sala"' in sala
    assert 'port = 7000' in sala
    assert "airplay_device_id_offset = 0" in sala
    assert 'sink = "airplay_AA_BB_CC_DD_EE_01"' in sala
    assert "/notify.sh true AA:BB:CC:DD:EE:01" in sala
    assert 'name = "Suíte \\"A\\" @ audio-sala"' in suite
    assert "port = 7001" in suite
    assert "http://" not in sala
    assert "webhook" not in sala
    store.sync([{"mac": SALA, "player_name": "Sala", "in_fleet": True}], "audio-sala")
    assert sorted(path.name for path in directory.glob("*.conf")) == ["AA_BB_CC_DD_EE_01.conf"]
    store.sync([], "audio-sala")
    assert list(directory.glob("*.conf")) == []


def test_webhook_stays_out_of_the_bridge_and_survives_a_rename(tmp_path):
    store = _store(tmp_path)
    store.set_webhook(SUITE, "http://192.168.0.100:8123/api/webhook/suite")
    store.sync(
        [
            {"mac": SALA, "player_name": "Sala", "in_fleet": True},
            {"mac": SUITE, "player_name": "Soundbar", "in_fleet": True},
            {"mac": "AA:BB:CC:DD:EE:03", "player_name": "Fora", "in_fleet": False},
        ],
        "audio-suite",
    )
    assert store.webhook_for(SUITE) == "http://192.168.0.100:8123/api/webhook/suite"
    text = (tmp_path / "config" / "shairport" / "AA_BB_CC_DD_EE_02.conf").read_text(encoding="utf-8")
    assert 'name = "Soundbar @ audio-suite"' in text
    assert "192.168.0.100" not in text
    assert not (tmp_path / "config" / "shairport" / "AA_BB_CC_DD_EE_03.conf").exists()
    catalog = json.loads((tmp_path / "config" / "airplay-devices.json").read_text(encoding="utf-8"))
    assert catalog[1]["webhook"].endswith("/suite")
    again = SpeakerStore(tmp_path / "config", tmp_path / "run")
    assert again.webhook_for(SUITE).endswith("/suite")


def test_airplay_mutes_only_the_speaker_that_is_playing():
    speakers = [
        {"token": "AA_BB_CC_DD_EE_01", "playing": True},
        {"token": "AA_BB_CC_DD_EE_02", "playing": False},
    ]
    sinks = parse_sinks(json.dumps([
        {"index": 1, "name": "airplay_AA_BB_CC_DD_EE_01", "state": "RUNNING"},
        {"index": 2, "name": "airplay_AA_BB_CC_DD_EE_02", "state": "RUNNING"},
        {"index": 3, "name": "bluez_output.AA_BB_CC_DD_EE_01.1", "state": "RUNNING"},
        {"index": 4, "name": "bluez_output.AA_BB_CC_DD_EE_02.1", "state": "IDLE"},
    ]))
    inputs = parse_inputs(json.dumps([
        {"index": 10, "sink": 3, "mute": False, "ownerModule": 8, "properties": {"application.name": "Sendspin"}},
        {"index": 11, "sink": 4, "mute": False, "ownerModule": 8, "properties": {"application.name": "Sendspin"}},
        {"index": 12, "sink": 3, "mute": False, "ownerModule": 9, "properties": {"application.name": "Loopback"}},
    ]))
    modules = parse_modules(json.dumps([
        {"name": "module-null-sink", "index": 20, "argument": "sink_name=airplay_AA_BB_CC_DD_EE_01"},
        {"name": "module-null-sink", "index": 21, "argument": "sink_name=airplay_AA_BB_CC_DD_EE_02"},
        {"name": "module-loopback", "index": 9, "argument": "source=airplay_AA_BB_CC_DD_EE_01.monitor sink=bluez_output.AA_BB_CC_DD_EE_01.1"},
        {"name": "module-loopback", "index": 10, "argument": "source=airplay_AA_BB_CC_DD_EE_02.monitor sink=bluez_output.AA_BB_CC_DD_EE_02.1"},
    ]))
    decision = decide(sinks, inputs, modules, speakers)
    assert decision["mute"] == [10]
    assert decision["unmute"] == []
    assert decision["load_null"] == []
    assert decision["load_loopback"] == []
    assert decision["unload"] == []


def test_missing_speaker_keeps_its_sink_and_drops_the_loopback():
    sinks = parse_sinks('[{"index": 1, "name": "airplay_AA_BB_CC_DD_EE_01", "state": "RUNNING"}]')
    modules = parse_modules(
        '[{"name": "module-loopback", "index": 4, "argument": "source=airplay_AA_BB_CC_DD_EE_01.monitor sink=bluez_output.AA_BB_CC_DD_EE_01.1"}]'
    )
    decision = decide(sinks, [], modules, [{"token": "AA_BB_CC_DD_EE_01", "playing": True}])
    assert decision["unload"] == [4]
    assert decision["load_loopback"] == []
    assert decision["mute"] == []
    gone = decide(sinks, [], modules, [])
    assert gone["unload"] == [4]


def test_webhook_fires_only_for_the_speaker_that_started(tmp_path):
    store = _store(tmp_path)
    store.set_webhook(SUITE, "http://192.168.0.100:8123/api/webhook/suite")
    posts = []
    switch = AirPlaySwitch(store, poster=lambda url: posts.append(url))
    assert switch.set_active(SALA, True) is True
    assert switch.set_active(SUITE, True) is True
    assert switch.set_active(SUITE, True) is False
    assert switch.set_active(SUITE, False) is False
    assert posts == ["http://192.168.0.100:8123/api/webhook/suite"]
    devices = json.loads((tmp_path / "run" / "devices.json").read_text(encoding="utf-8"))
    playing = {item["mac"]: item["playing"] for item in devices}
    assert playing[SALA] is True
    assert playing[SUITE] is False


def test_music_assistant_drops_only_the_overlapping_speaker(tmp_path):
    store = _store(tmp_path)
    switch = AirPlaySwitch(store, poster=lambda _url: (_ for _ in ()).throw(AssertionError("webhook")))
    switch.set_active(SALA, True)
    switch.set_active(SUITE, True)
    assert switch.observe(set()) == []
    assert switch.observe({SALA}) == []
    assert switch.observe(set()) == []
    assert switch.observe({SALA}) == [SALA]
    assert switch.observe({SALA}) == [SALA]
    switch.confirm_drop(SALA)
    assert store.playing(SALA) is False
    assert store.playing(SUITE) is True
    assert switch.observe({SALA}) == []


def test_sendspin_starting_alone_does_not_drop_airplay(tmp_path):
    store = _store(tmp_path)
    switch = AirPlaySwitch(store)
    assert switch.observe(set()) == []
    assert switch.observe({SALA}) == []


def test_session_endpoint_is_local_and_hides_the_webhook():
    import asyncio

    client, bridge, agent = build_client(webhook_url="http://192.168.0.100:8123/api/webhook/secret-from-env")
    bridge.config_data = {
        "BLUETOOTH_DEVICES": [
            {"mac": SALA, "player_name": "Sala"},
            {"mac": SUITE, "player_name": "Suite"},
        ]
    }
    bridge.status = lambda: {
        "devices": [
            {"mac": SALA, "player_name": "Sala", "bluetooth_connected": True, "playing": False},
            {"mac": SUITE, "player_name": "Suite", "bluetooth_connected": False, "playing": False},
        ]
    }
    posts = []
    client.app.state.airplay._poster = lambda url: posts.append(url)
    listed = client.get("/api/bluetooth/status")
    assert listed.status_code == 200
    saved = client.post("/api/bluetooth/webhook", json={"mac": SUITE, "webhook": "http://192.168.0.100:8123/api/webhook/secret-value"})
    assert saved.status_code == 200
    rejected = client.post("/api/bluetooth/webhook", json={"mac": SUITE, "webhook": "file:///tmp/hook"})
    assert rejected.status_code == 400

    async def post(host, payload):
        transport = httpx.ASGITransport(app=client.app, client=(host, 1234))
        async with httpx.AsyncClient(transport=transport, base_url="http://audio.local") as http:
            return await http.post("/api/airplay/session", json=payload)

    denied = asyncio.run(post("192.168.1.20", {"active": True, "mac": SUITE}))
    assert denied.status_code == 403
    started = asyncio.run(post("127.0.0.1", {"active": True, "mac": SUITE}))
    assert started.status_code == 200
    assert started.json()["playing"] is True
    other = asyncio.run(post("127.0.0.1", {"active": True, "mac": SALA}))
    assert other.status_code == 200
    assert posts == ["http://192.168.0.100:8123/api/webhook/secret-value"]
    assert any(call[0] == "airplay-audio" for call in agent.calls)
    status = client.get("/api/airplay/status")
    body = status.json()
    assert body["playing"] is True
    assert "Suite" in " ".join(body["names"])
    assert "Sala" in " ".join(body["names"])
    assert body["webhook_configured"] is True
    assert "secret-value" not in status.text
    assert "secret-from-env" not in status.text
    music = client.get("/api/music-assistant/status")
    assert "spotify_connect" not in music.json()
    page = client.get("/api/bluetooth/status")
    suite = next(device for device in page.json()["devices"] if device["mac"] == SUITE)
    assert suite["airplay_webhook"].endswith("/secret-value")
    assert "secret-value" not in (tmp_path_text(client))


def tmp_path_text(client):
    confs = client.app.state.settings.config_dir / "shairport"
    return "\n".join(path.read_text(encoding="utf-8") for path in confs.glob("*.conf"))


def test_drop_airplay_stops_only_that_process(tmp_path):
    calls = []

    def runner(args):
        calls.append(args)

        class Result:
            returncode = 0
            stdout = "true\n"
            stderr = ""

        return Result()

    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    (pid_dir / "AA_BB_CC_DD_EE_02").write_text("4321\n", encoding="utf-8")
    control = HostControl(tmp_path, runner)
    control.airplay_pid_dir = pid_dir
    assert dispatch({"action": "drop-airplay"}, control)["error"] == "invalid_mac"
    assert dispatch({"action": "drop-airplay", "mac": "aa:bb:cc:dd:ee:02"}, control)["ok"] is True
    assert calls == [["docker", "exec", "shairport-sync", "kill", "-TERM", "4321"]]
    status = dispatch({"action": "airplay-status"}, control)
    assert status["running"] is True
    assert "name" not in status
    assert dispatch({"action": "bash"}, control)["error"] == "unknown_action"


def test_invalid_webhook_is_ignored(tmp_path):
    assert clean_webhook("file:///tmp/hook") == ""
    assert clean_webhook("") == ""
    store = _store(tmp_path)
    switch = AirPlaySwitch(store, poster=lambda url: (_ for _ in ()).throw(AssertionError(url)))
    assert switch.set_active(SALA, True) is True
