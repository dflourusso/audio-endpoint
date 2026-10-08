#!/usr/bin/env bash
# Prepara um Orange Pi (Ubuntu Server ou Armbian) como endpoint de áudio.
set -euo pipefail

TARGET="/opt/audio-endpoint"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Este instalador roda no Orange Pi, com Linux."
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Execute com sudo ./install.sh"
  exit 1
fi

arch="$(uname -m)"
if [[ "${arch}" != "aarch64" ]]; then
  echo "Aviso: a arquitetura é ${arch}. O aparelho foi pensado para aarch64 (Orange Pi)."
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  ca-certificates curl python3 rsync \
  bluez avahi-daemon network-manager \
  pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin || apt-get install -y docker-compose-v2
fi

if ! id audioendpoint >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/audioendpoint --shell /usr/sbin/nologin audioendpoint
fi
usermod -aG audio,bluetooth audioendpoint || true

audio_uid="$(id -u audioendpoint)"
audio_gid="$(id -g audioendpoint)"

install -d /etc/wireplumber/bluetooth.lua.d
install -m 644 "${ROOT}/agent/wireplumber/51-a2dp-sink.lua" /etc/wireplumber/bluetooth.lua.d/51-audio-endpoint-a2dp.lua

loginctl enable-linger audioendpoint || true
systemctl start "user@${audio_uid}.service" || true
install -d -m 700 -o audioendpoint -g audioendpoint "/run/user/${audio_uid}"

if ! sudo -u audioendpoint \
  XDG_RUNTIME_DIR="/run/user/${audio_uid}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${audio_uid}/bus" \
  systemctl --user enable --now pipewire.service pipewire-pulse.service wireplumber.service
then
  echo "Aviso: o PipeWire do usuário audioendpoint não subiu agora. Depois do reboot, confira com systemctl --user status pipewire."
fi

python3 - <<'PY'
from pathlib import Path
path = Path("/etc/bluetooth/main.conf")
text = path.read_text(encoding="utf-8") if path.exists() else "[Policy]\n"
lines = []
in_policy = False
replaced = False
for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_policy = stripped.lower() == "[policy]"
    if in_policy and stripped.lower().startswith("autoenable"):
        lines.append("AutoEnable=true")
        replaced = True
    else:
        lines.append(line)
if not replaced:
    if not any(line.strip().lower() == "[policy]" for line in lines):
        lines.extend(["", "[Policy]"])
    lines.append("AutoEnable=true")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/audio-endpoint.conf <<'EOF'
[Journal]
SystemMaxUse=50M
EOF
systemctl restart systemd-journald || true
systemctl enable --now bluetooth.service avahi-daemon.service NetworkManager.service

if [[ "${ROOT}" != "${TARGET}" ]]; then
  install -d "${TARGET}"
  rsync -a \
    --exclude '.env' \
    --exclude 'config/*' \
    --exclude 'bridge/config.json' \
    --exclude 'data/*' \
    "${ROOT}/" "${TARGET}/"
fi

install -d -m 755 "${TARGET}/config" "${TARGET}/data/backups" "${TARGET}/bridge"
if [[ ! -f "${TARGET}/bridge/config.json" ]]; then
  cp "${TARGET}/bridge/config.json.example" "${TARGET}/bridge/config.json"
fi
chown -R audioendpoint:audioendpoint "${TARGET}/bridge"

if [[ ! -f "${TARGET}/.env" ]]; then
  cp "${TARGET}/.env.example" "${TARGET}/.env"
  python3 - "${TARGET}/.env" "${audio_uid}" "${audio_gid}" <<'PY'
import sys
from pathlib import Path
path, uid, gid = sys.argv[1:]
lines = Path(path).read_text(encoding="utf-8").splitlines()
wanted = {"AUDIO_UID": uid, "AUDIO_GID": gid}
seen = set()
updated = []
for line in lines:
    key = line.split("=", 1)[0]
    if key in wanted:
        updated.append(f"{key}={wanted[key]}")
        seen.add(key)
    else:
        updated.append(line)
for key, value in wanted.items():
    if key not in seen:
        updated.append(f"{key}={value}")
Path(path).write_text("\n".join(updated) + "\n", encoding="utf-8")
PY
  chmod 600 "${TARGET}/.env"
fi

python3 - "${TARGET}/.env" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.is_file() else ""
keys = set()
for line in text.splitlines():
    if not line or line.startswith("#") or "=" not in line:
        continue
    keys.add(line.split("=", 1)[0])
extra = []
if "SHAIRPORT_IMAGE" not in keys:
    extra.append("SHAIRPORT_IMAGE=mikebrady/shairport-sync:5.5.2")
if "AIRPLAY_WEBHOOK_URL" not in keys:
    extra.append("AIRPLAY_WEBHOOK_URL=")
if extra:
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(extra) + "\n", encoding="utf-8")
    path.chmod(0o600)
PY

install -m 644 "${TARGET}/agent/audio-endpoint-agent.service" /etc/systemd/system/audio-endpoint-agent.service
systemctl daemon-reload
systemctl enable --now audio-endpoint-agent.service

python3 "${TARGET}/agent/airplay_config.py" "${TARGET}/config/shairport-sync.conf"

rm -f /etc/pipewire/pipewire.conf.d/10-audio-endpoint-airplay.conf
sudo -u audioendpoint \
  XDG_RUNTIME_DIR="/run/user/${audio_uid}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${audio_uid}/bus" \
  systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service || true

unit_dir="/var/lib/audioendpoint/.config/systemd/user"
install -d -o audioendpoint -g audioendpoint "${unit_dir}"
install -m 644 -o audioendpoint -g audioendpoint \
  "${TARGET}/agent/audio-endpoint-airplay-link.service" \
  "${unit_dir}/audio-endpoint-airplay-link.service"
sudo -u audioendpoint \
  XDG_RUNTIME_DIR="/run/user/${audio_uid}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${audio_uid}/bus" \
  systemctl --user daemon-reload || true
sudo -u audioendpoint \
  XDG_RUNTIME_DIR="/run/user/${audio_uid}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${audio_uid}/bus" \
  systemctl --user enable --now audio-endpoint-airplay-link.service || true

docker compose --project-directory "${TARGET}" up -d --build

name="$(hostname -s)"
echo
echo "Instalação concluída."
echo "Interface: http://${name}.local"
echo "O DHCP continua automático. O primeiro pareamento Bluetooth é feito nessa interface."
