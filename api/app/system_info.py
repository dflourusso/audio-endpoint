import socket
import time
from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def cpu_times(stat_text: str) -> tuple[int, int] | None:
    for line in stat_text.splitlines():
        if line.startswith("cpu "):
            numbers = [int(part) for part in line.split()[1:] if part.isdigit()]
            if len(numbers) < 4:
                return None
            idle = numbers[3] + (numbers[4] if len(numbers) > 4 else 0)
            return sum(numbers), idle
    return None


def cpu_percent(read_stat, sleep=time.sleep, delay: float = 0.15) -> float | None:
    first = cpu_times(read_stat())
    if first is None:
        return None
    sleep(delay)
    second = cpu_times(read_stat())
    if second is None:
        return None
    total = second[0] - first[0]
    idle = second[1] - first[1]
    if total <= 0:
        return None
    return round((total - idle) * 100 / total, 1)


def memory(meminfo: str) -> dict | None:
    values: dict[str, int] = {}
    for line in meminfo.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        number = raw.strip().split()[0]
        if number.isdigit():
            values[key] = int(number)
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total:
        return None
    used = total - available if available is not None else None
    percent = round(used * 100 / total, 1) if used is not None else None
    return {
        "total_mb": round(total / 1024),
        "used_mb": round(used / 1024) if used is not None else None,
        "percent": percent,
    }


def uptime_seconds(text: str) -> float | None:
    part = text.split()
    if not part:
        return None
    try:
        return float(part[0])
    except ValueError:
        return None


def temperature_c(sys_root: Path) -> float | None:
    thermal = sys_root / "class" / "thermal"
    if not thermal.is_dir():
        return None
    for zone in sorted(thermal.glob("thermal_zone*/temp")):
        raw = read_text(zone).strip()
        if raw.lstrip("-").isdigit():
            return round(int(raw) / 1000, 1)
    return None


def default_interface(route_text: str) -> str | None:
    for line in route_text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "00000000":
            return parts[0]
    return None


def mac_address(sys_root: Path, interface: str | None) -> str | None:
    if not interface:
        return None
    raw = read_text(sys_root / "class" / "net" / interface / "address").strip()
    return raw.upper() if raw else None


def ipv4() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    if address.startswith("127."):
        return None
    return address


def os_name(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def hostname_of(text: str) -> str:
    return text.strip().split()[0] if text.strip() else ""
