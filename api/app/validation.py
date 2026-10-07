import re

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
JOB_RE = re.compile(r"^[A-Za-z0-9-]{8,80}$")
RESTART_TARGETS = frozenset({"app", "bluetooth", "sendspin"})
LOG_SOURCES = frozenset({"bridge", "agent", "bluetooth", "avahi"})


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_mac(value: str) -> str:
    mac = (value or "").strip().upper()
    if not MAC_RE.match(mac):
        raise ValidationError("invalid_mac", "Endereço MAC inválido.")
    return mac


def normalize_hostname(value: str) -> str:
    hostname = (value or "").strip().lower()
    if hostname in {"localhost", "localhost.localdomain"} or not HOSTNAME_RE.match(hostname):
        raise ValidationError(
            "invalid_hostname",
            "Use um hostname como audio-sala: letras minúsculas, números e hífen.",
        )
    return hostname


def normalize_job_id(value: str) -> str:
    job_id = (value or "").strip()
    if not JOB_RE.match(job_id):
        raise ValidationError("invalid_job", "Identificador de tarefa inválido.")
    return job_id


def normalize_restart_target(value: str) -> str:
    target = (value or "").strip().lower()
    if target not in RESTART_TARGETS:
        raise ValidationError("invalid_target", "Alvo de reinício inválido.")
    return target


def normalize_log_source(value: str) -> str:
    source = (value or "bridge").strip().lower()
    if source not in LOG_SOURCES:
        raise ValidationError("invalid_log_source", "Origem de log inválida.")
    return source


def normalize_log_lines(value: int | str | None) -> int:
    try:
        lines = int(value if value is not None else 100)
    except (TypeError, ValueError) as exc:
        raise ValidationError("invalid_lines", "Número de linhas inválido.") from exc
    if lines < 1 or lines > 200:
        raise ValidationError("invalid_lines", "Peça entre 1 e 200 linhas.")
    return lines
