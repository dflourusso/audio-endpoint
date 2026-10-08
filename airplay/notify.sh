#!/bin/sh
# Avisa a API local quando o AirPlay de uma caixa começa ou termina.
active="$1"
mac="$2"
if [ "${active}" != "true" ] && [ "${active}" != "false" ]; then
  exit 0
fi
case "$mac" in
  [0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]:[0-9A-Fa-f][0-9A-Fa-f]) ;;
  *) exit 0 ;;
esac
curl -fsS -m 2 -X POST "http://127.0.0.1/api/airplay/session" \
  -H "Content-Type: application/json" \
  -d "{\"active\":${active},\"mac\":\"${mac}\"}" || true
