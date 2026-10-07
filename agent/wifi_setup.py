"""Ciclo de Wi-Fi: tenta a rede salva e, se falhar, abre um ponto de acesso temporário."""

import re
import threading
import time

AP_SSID = "audio-setup"
AP_NAME = "audio-setup"
AP_ADDRESS = "192.168.4.1"
AP_CIDR = "192.168.4.1/24"
CLIENT_WAIT = 180
AP_WAIT = 300

CLIENT = "client"
TRY = "try-client"
ACCESS_POINT = "access-point"
HOLD = "hold"


class WifiInputError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def advance(phase: str, elapsed: float, uplink: bool, stations: int, client_wait: float = CLIENT_WAIT, ap_wait: float = AP_WAIT) -> str:
    if uplink:
        return CLIENT
    if phase == CLIENT:
        return TRY
    if phase == TRY:
        if elapsed >= client_wait:
            return ACCESS_POINT
        return TRY
    if phase == ACCESS_POINT:
        if stations > 0:
            return HOLD
        if elapsed >= ap_wait:
            return TRY
        return ACCESS_POINT
    if phase == HOLD:
        if stations > 0:
            return HOLD
        return TRY
    return TRY


def has_default_route(text: str) -> bool:
    return any(line.startswith("default") for line in text.splitlines())


def station_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith("Station "))


def parse_wifi_device(text: str) -> str:
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1].strip() == "wifi" and parts[0].strip():
            return parts[0].strip()
    return "wlan0"


def parse_wifi_list(text: str) -> list[dict]:
    networks = []
    seen = set()
    for line in text.splitlines():
        if not line or line.startswith("SSID"):
            continue
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 2:
            continue
        ssid = parts[0].replace("\\:", ":").strip()
        if not ssid or ssid == AP_SSID or ssid in seen:
            continue
        seen.add(ssid)
        signal = parts[1].replace("\\:", ":").strip()
        security = parts[2].replace("\\:", ":").strip() if len(parts) > 2 else ""
        networks.append({"ssid": ssid, "signal": signal, "security": security or "aberta"})
    return networks


def valid_ssid(value: str) -> str:
    ssid = (value or "").strip()
    if ssid == AP_SSID or not ssid or len(ssid) > 32 or any(char in ssid for char in "\n\r\x00"):
        raise WifiInputError("Nome de rede inválido.")
    return ssid


def valid_password(value: str) -> str:
    password = value or ""
    if any(char in password for char in "\n\r\x00"):
        raise WifiInputError("Senha inválida.")
    if password and not 8 <= len(password) <= 63:
        raise WifiInputError("A senha do Wi-Fi precisa ter entre 8 e 63 caracteres.")
    return password


class WifiManager:
    def __init__(self, run, client_wait: float = CLIENT_WAIT, ap_wait: float = AP_WAIT, sleeper=time.sleep, clock=time.monotonic):
        self.execute = run
        self.client_wait = client_wait
        self.ap_wait = ap_wait
        self.sleep = sleeper
        self.clock = clock
        self.phase = TRY
        self.mode = "client"
        self.message = ""
        self.networks: list[dict] = []
        self.hold = False
        self._stop = False
        self._lock = threading.Lock()
        self._timer = clock()

    def status(self) -> dict:
        uplink = self._uplink_unlocked()
        with self._lock:
            return {
                "ok": True,
                "mode": self.mode,
                "uplink": uplink,
                "ssid": AP_SSID,
                "address": AP_ADDRESS,
                "message": self.message,
            }

    def network_list(self) -> dict:
        with self._lock:
            return {"ok": True, "networks": list(self.networks), "mode": self.mode}

    def refresh(self) -> dict:
        with self._lock:
            self.hold = True
            was_ap = self.mode == "access-point"
        try:
            if was_ap:
                self._stop_ap()
            self._scan()
            if was_ap:
                self._start_ap()
        finally:
            with self._lock:
                self.hold = False
                self._timer = self.clock()
        return self.network_list()

    def connect(self, ssid: str, password: str) -> dict:
        ssid = valid_ssid(ssid)
        password = valid_password(password)
        with self._lock:
            self.hold = True
            self.message = ""
        try:
            self._stop_ap()
            interface = self._interface()
            command = ["nmcli", "device", "wifi", "connect", ssid, "ifname", interface]
            if password:
                command.extend(["password", password])
            completed = self.execute(command)
            if completed.returncode == 0 and self._uplink_unlocked():
                with self._lock:
                    self.phase = CLIENT
                    self.mode = "client"
                    self.message = ""
                    self._timer = self.clock()
                return {"ok": True, "connected": True}
            message = (completed.stderr or completed.stdout or "Não foi possível entrar nessa rede.").strip()
            self._start_ap()
            with self._lock:
                self.phase = ACCESS_POINT
                self.mode = "access-point"
                self.message = _public_wifi_error(message)
                self._timer = self.clock()
            return {"ok": False, "error": "wifi_failed", "message": self.message}
        finally:
            with self._lock:
                self.hold = False

    def supervise(self) -> None:
        while not self._stop:
            if self.hold:
                self.sleep(1)
                continue
            uplink = self._uplink_unlocked()
            stations = self._stations() if self.phase in {ACCESS_POINT, HOLD} else 0
            elapsed = self.clock() - self._timer
            nxt = advance(self.phase, elapsed, uplink, stations, self.client_wait, self.ap_wait)
            if nxt != self.phase:
                self._transition(self.phase, nxt)
                with self._lock:
                    self.phase = nxt
                    self.mode = "access-point" if nxt in {ACCESS_POINT, HOLD} else "client"
                    self._timer = self.clock()
            self.sleep(2)

    def _transition(self, previous: str, nxt: str) -> None:
        if nxt == ACCESS_POINT:
            self._scan()
            self._start_ap()
        elif nxt == TRY and previous in {ACCESS_POINT, HOLD}:
            self._stop_ap()
        elif nxt == CLIENT and previous in {ACCESS_POINT, HOLD}:
            self._stop_ap()

    def _scan(self) -> None:
        completed = self.execute(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"])
        if completed.returncode == 0:
            with self._lock:
                self.networks = parse_wifi_list(completed.stdout)

    def _start_ap(self) -> None:
        interface = self._interface()
        self.execute(["nmcli", "device", "disconnect", interface])
        self.execute(["nmcli", "connection", "delete", AP_NAME])
        added = self.execute(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "wifi",
                "ifname",
                interface,
                "con-name",
                AP_NAME,
                "autoconnect",
                "no",
                "ssid",
                AP_SSID,
            ]
        )
        if added.returncode != 0:
            return
        self.execute(
            [
                "nmcli",
                "connection",
                "modify",
                AP_NAME,
                "802-11-wireless.mode",
                "ap",
                "802-11-wireless.band",
                "bg",
                "ipv4.method",
                "shared",
                "ipv4.addresses",
                AP_CIDR,
                "ipv6.method",
                "disabled",
                "wifi-sec.key-mgmt",
                "none",
            ]
        )
        self.execute(["nmcli", "connection", "up", AP_NAME])

    def _stop_ap(self) -> None:
        interface = self._interface()
        self.execute(["nmcli", "connection", "down", AP_NAME])
        self.execute(["nmcli", "device", "connect", interface])

    def _interface(self) -> str:
        completed = self.execute(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
        if completed.returncode != 0:
            return "wlan0"
        return parse_wifi_device(completed.stdout)

    def _uplink_unlocked(self) -> bool:
        completed = self.execute(["ip", "-4", "route", "show", "default"])
        if completed.returncode != 0:
            return False
        return has_default_route(completed.stdout)

    def _stations(self) -> int:
        interface = self._interface()
        completed = self.execute(["iw", "dev", interface, "station", "dump"])
        if completed.returncode != 0:
            return 0
        return station_count(completed.stdout)


def _public_wifi_error(message: str) -> str:
    lowered = message.lower()
    if "secret" in lowered or "password" in lowered or "psk" in lowered:
        return "A senha não foi aceita."
    if len(message) > 180:
        return "Não foi possível entrar nessa rede."
    return message or "Não foi possível entrar nessa rede."
