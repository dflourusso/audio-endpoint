#!/bin/sh
# Um NQPTP para a máquina. Um Shairport Sync por caixa, cada um com porta e saída próprias.
# Não inicia outro avahi-daemon: a placa já tem um, e um segundo disputa a porta 5353.

if [ -z "${PULSE_SERVER}" ]; then
  echo "PULSE_SERVER não foi definido." >&2
  exit 1
fi

echo "Starting NQPTP ($(date))"
/usr/local/bin/nqptp >/dev/null 2>&1 &
nqptp_pid=$!

conf_dir=/airplay-config/shairport
pid_dir=/run/audio-endpoint/airplay/pids
mkdir -p "$pid_dir"

while true; do
  if ! kill -0 "$nqptp_pid" 2>/dev/null; then
    echo "Starting NQPTP ($(date))"
    /usr/local/bin/nqptp >/dev/null 2>&1 &
    nqptp_pid=$!
  fi

  current=""
  if [ -d "$conf_dir" ]; then
    for conf in "$conf_dir"/*.conf; do
      if [ ! -f "$conf" ]; then
        continue
      fi
      base=$(basename "$conf" .conf)
      pidfile="$pid_dir/$base"
      pid=""
      if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
      fi
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        if [ "$conf" -nt "$pidfile" ]; then
          kill -TERM "$pid" 2>/dev/null || true
          pid=""
        fi
      else
        pid=""
      fi
      if [ -z "$pid" ]; then
        /usr/local/bin/shairport-sync -c "$conf" &
        echo $! > "$pidfile"
      fi
      current="$current $base"
    done
  fi

  for pidfile in "$pid_dir"/*; do
    if [ ! -f "$pidfile" ]; then
      continue
    fi
    base=$(basename "$pidfile")
    case " $current " in
      *" $base "*) ;;
      *)
        pid=$(cat "$pidfile")
        kill -TERM "$pid" 2>/dev/null || true
        rm -f "$pidfile"
        ;;
    esac
  done
  sleep 1
done
