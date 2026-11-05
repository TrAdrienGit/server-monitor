#!/bin/bash
# Installe le rallumage automatique et le monitor à jour. À lancer en root sur le serveur,
# depuis un dossier contenant les fichiers de autowake/, monitor.py et index.html.
set -euo pipefail
cd "$(dirname "$0")"

install -m 755 server-autowake /usr/local/sbin/server-autowake
install -m 644 server-autowake-boot.service server-autowake.service server-autowake.timer /etc/systemd/system/
install -m 644 90-server-autorestart.conf /etc/sysctl.d/90-server-autorestart.conf
install -d /etc/systemd/system.conf.d
install -m 644 90-server-watchdog.conf /etc/systemd/system.conf.d/90-server-watchdog.conf
install -m 755 monitor.py /opt/server-monitor/monitor.py
install -m 644 index.html /opt/server-monitor/index.html

sysctl -q -p /etc/sysctl.d/90-server-autorestart.conf
systemctl daemon-reload
systemctl enable --now server-autowake-boot.service
systemctl enable --now server-autowake.timer
systemctl restart server-monitor

sleep 2
echo "--- server-monitor : $(systemctl is-active server-monitor)"
echo "--- timer : $(systemctl is-active server-autowake.timer)"
server-autowake status
