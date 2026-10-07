from wifi_setup import (
    ACCESS_POINT,
    CLIENT,
    HOLD,
    TRY,
    WifiManager,
    advance,
    has_default_route,
    parse_wifi_list,
    station_count,
    valid_password,
    valid_ssid,
    WifiInputError,
)


def test_power_loss_returns_to_the_saved_network_when_nobody_joins():
    assert advance(TRY, 179, uplink=False, stations=0) == TRY
    assert advance(TRY, 180, uplink=False, stations=0) == ACCESS_POINT
    assert advance(ACCESS_POINT, 299, uplink=False, stations=0) == ACCESS_POINT
    assert advance(ACCESS_POINT, 300, uplink=False, stations=0) == TRY
    assert advance(TRY, 10, uplink=True, stations=0) == CLIENT


def test_a_phone_on_the_access_point_keeps_it_open():
    assert advance(ACCESS_POINT, 10, uplink=False, stations=1) == HOLD
    assert advance(HOLD, 600, uplink=False, stations=1) == HOLD
    assert advance(HOLD, 1, uplink=False, stations=0) == TRY


def test_wifi_list_hides_the_setup_network():
    text = "Casa:80:WPA2\naudio-setup:100:\nOutra\\:Rede:40:WPA2\n"
    networks = parse_wifi_list(text)
    assert [item["ssid"] for item in networks] == ["Casa", "Outra:Rede"]
    assert has_default_route("default via 192.168.1.1 dev wlan0\n")
    assert not has_default_route("")
    assert station_count("Station aa:bb:cc:dd:ee:ff (on wlan0)\n") == 1


def test_wifi_password_rules():
    assert valid_ssid("Casa") == "Casa"
    assert valid_password("") == ""
    try:
        valid_password("curta")
        raise AssertionError("senha curta deveria falhar")
    except WifiInputError:
        pass


class Result:
    def __init__(self, code=0, stdout="", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


def test_failed_connection_brings_the_access_point_back():
    calls = []

    def execute(args):
        calls.append(args)
        if args[:4] == ["nmcli", "device", "wifi", "connect"]:
            return Result(code=4, stderr="Secrets were required")
        if args[:4] == ["ip", "-4", "route", "show"]:
            return Result(stdout="")
        if args[:3] == ["nmcli", "-t", "-f"]:
            return Result(stdout="wlan0:wifi\n")
        return Result()

    wifi = WifiManager(execute, sleeper=lambda _seconds: None, clock=lambda: 0)
    result = wifi.connect("Casa", "senha-errada")
    assert result["ok"] is False
    assert result["message"] == "A senha não foi aceita."
    assert ["nmcli", "connection", "up", "audio-setup"] in calls
    connect = next(args for args in calls if args[:4] == ["nmcli", "device", "wifi", "connect"])
    assert connect[-2:] == ["password", "senha-errada"]
    assert "senha-errada" not in result["message"]


def test_the_setup_name_is_rejected_before_nmcli(tmp_path):
    from audio_endpoint_agent import HostControl, dispatch

    calls = []

    def run(args, timeout=20):
        calls.append(args)
        return Result()

    control = HostControl(tmp_path, run)
    result = dispatch({"action": "wifi-connect", "ssid": "audio-setup", "password": ""}, control)
    assert result["ok"] is False
    assert calls == []
