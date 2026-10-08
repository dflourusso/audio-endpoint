"""Estado da sessão AirPlay de cada caixa e a troca com o Sendspin dela."""

import threading

import httpx

from app.airplay_speakers import MAC_RE, SpeakerStore
from app.routers.status import describe_playback


def is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost"}


def playing_macs(status: dict) -> set[str]:
    if not isinstance(status, dict):
        return set()
    playback = describe_playback(status)
    macs = set()
    for player in playback["players"]:
        mac = str(player.get("mac") or "").strip().upper()
        if player.get("playing") and MAC_RE.fullmatch(mac):
            macs.add(mac)
    return macs


class AirPlaySwitch:
    """Quem começou por último fica com aquela caixa. O webhook só sai no início do AirPlay dela."""

    def __init__(self, store: SpeakerStore, poster=None):
        self.store = store
        self._poster = poster or _post_webhook
        self._sendspin: dict[str, bool | None] = {}
        self._pending: set[str] = set()

    def set_active(self, mac: str, active: bool) -> bool:
        mac = (mac or "").strip().upper()
        started = active and not self.store.playing(mac)
        self.store.set_playing(mac, active)
        if not active:
            self._pending.discard(mac)
        if started:
            url = self.store.webhook_for(mac)
            if url:
                threading.Thread(target=self._poster, args=(url,), daemon=True).start()
        return started

    def observe(self, playing: set[str]) -> list[str]:
        drops = []
        known = set(self._sendspin) | set(playing) | set(self._pending)
        for mac in sorted(known):
            previous = self._sendspin.get(mac)
            now = mac in playing
            self._sendspin[mac] = now
            if mac in self._pending:
                drops.append(mac)
                continue
            if previous is None:
                continue
            if self.store.playing(mac) and now and not previous:
                self._pending.add(mac)
                drops.append(mac)
        return drops

    def confirm_drop(self, mac: str) -> None:
        mac = (mac or "").strip().upper()
        self._pending.discard(mac)
        self.store.set_playing(mac, False)


def _post_webhook(url: str) -> None:
    try:
        httpx.post(url, json={"active": True, "source": "airplay"}, timeout=2.0, follow_redirects=False)
    except httpx.HTTPError:
        return


def supervise(bridge, switch: AirPlaySwitch, take_over, stop: threading.Event, released=None, interval: float = 1.0) -> None:
    while not stop.is_set():
        try:
            macs = playing_macs(bridge.status())
        except Exception:
            macs = None
        if macs is not None:
            for mac in switch.observe(macs):
                try:
                    take_over(mac)
                except Exception:
                    continue
                switch.confirm_drop(mac)
                if released is not None:
                    try:
                        released()
                    except Exception:
                        pass
        stop.wait(interval)


__all__ = ["AirPlaySwitch", "is_loopback", "playing_macs", "supervise"]
