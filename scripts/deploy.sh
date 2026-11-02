#!/usr/bin/env bash
# Déploie server-monitor sur le serveur via SSH (Tailscale). Cible lue dans config.ini ([deploy] ssh_target).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f config.ini ] || { echo "config.ini manquant : copier config.example.ini" >&2; exit 1; }
HOST="$(sed -n 's/^ssh_target *= *//p' config.ini | tr -d '\r')"
[ -n "$HOST" ] || { echo "ssh_target vide dans config.ini" >&2; exit 1; }

ssh "$HOST" 'mkdir -p /opt/server-monitor /var/lib/server-monitor'
scp monitor.py index.html favicon.svg config.ini "$HOST:/opt/server-monitor/"
scp server-monitor.service "$HOST:/etc/systemd/system/server-monitor.service"
ssh "$HOST" 'set -e
  python3 -c "import psutil" 2>/dev/null || apt-get install -y python3-psutil
  command -v smartctl >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends smartmontools
  systemctl daemon-reload
  systemctl enable server-monitor
  systemctl restart server-monitor
  sleep 2
  systemctl --no-pager --lines=5 status server-monitor'
