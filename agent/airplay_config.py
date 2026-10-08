"""Remove a configuração única antiga. Cada caixa passa a ter o próprio arquivo, gerado pela API."""

import re
import socket
from pathlib import Path

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
FALLBACK_NAME = "audio-endpoint"


def read_hostname(path: Path) -> str:
    text = ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    hostname = text.splitlines()[0].strip().lower() if text else ""
    if HOSTNAME_RE.match(hostname) and hostname not in {"localhost", "localhost.localdomain"}:
        return hostname
    fallback = socket.gethostname().split(".")[0].strip().lower()
    if HOSTNAME_RE.match(fallback) and fallback not in {"localhost", "localhost.localdomain"}:
        return fallback
    return FALLBACK_NAME


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/audio-endpoint/config/shairport-sync.conf")
    if target.is_file():
        target.unlink()
