import threading

from app.bluetooth_service import RecoveryClock, watch_bluetooth


def test_recovery_retries_after_the_interval_and_play_does_not_wait():
    clock = RecoveryClock(30)
    assert clock.ready("AA:BB:CC:DD:EE:02", False, 0) is True
    assert clock.ready("AA:BB:CC:DD:EE:02", False, 10) is False
    assert clock.ready("AA:BB:CC:DD:EE:02", False, 30) is True
    assert clock.ready("AA:BB:CC:DD:EE:01", True, 31) is True
    assert clock.ready("AA:BB:CC:DD:EE:01", True, 32) is False


def test_watch_reclaims_and_reconnects_a_released_speaker():
    mac = "88:D0:39:0D:4B:FC"
    calls = []
    stop = threading.Event()

    class Bridge:
        def status(self):
            return {
                "player_name": "JBL Bar 2.1 @ audio-suite",
                "bluetooth_mac": mac,
                "bluetooth_connected": False,
                "playing": False,
            }

        def config(self):
            return {
                "BLUETOOTH_DEVICES": [
                    {"mac": mac, "player_name": "JBL Bar 2.1", "released": True, "released_by": "auto"}
                ]
            }

    def recover(action):
        calls.append(action)
        stop.set()

    watch_bluetooth(Bridge(), recover, stop, interval=0.01, recover_interval=30)
    assert calls == [
        {"mac": mac, "player_name": "JBL Bar 2.1 @ audio-suite", "reclaim": True, "urgent": False}
    ]
