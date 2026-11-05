#!/bin/bash
# Service de partage public : administration sur Tailscale (9443), liens sur Funnel (10000).
# À lancer en root dans un terminal interactif. Peut être relancé.
set -euo pipefail
cd "$(dirname "$0")"

DIR=/data/public-share
NC="docker compose -f /data/nextcloud/compose.yaml"

# Dossier géré depuis Nextcloud.
$NC exec -T -u www-data app mkdir -p data/adrien/files/Public
$NC exec -T -u www-data app php occ files:scan --path=adrien/files/Public >/dev/null

install -d -m 755 "$DIR"
install -m 644 compose.yaml "$DIR/compose.yaml"
install -d -o 33 -g 33 -m 700 "$DIR/db"
cd "$DIR"

fb() { docker compose run --rm -T --no-deps share --database /db/filebrowser.db "$@"; }

docker compose pull -q
if [ ! -s db/filebrowser.db ]; then
    while :; do
        read -r -s -p "Mot de passe d'administration du partage (12 caractères au moins, unique) : " p1; echo
        read -r -s -p "Confirmation : " p2; echo
        if [ "${#p1}" -ge 12 ] && [ "$p1" = "$p2" ]; then break; fi
        echo "Trop court ou différents, recommence."
    done
    fb config init --address 0.0.0.0 --port 8080 --root /srv --signup=false --branding.disableExternal >/dev/null
    fb users add adrien "$p1" --perm.admin --perm.create=false --perm.delete=false --perm.modify=false --perm.rename=false --perm.execute=false >/dev/null
    unset p1 p2
fi

docker compose up -d
sleep 3

# Seules les adresses nécessaires aux liens de partage sont publiques.
tailscale funnel --bg --https=10000 --set-path=/share http://127.0.0.1:8091/share >/dev/null
tailscale funnel --bg --https=10000 --set-path=/static http://127.0.0.1:8091/static >/dev/null
tailscale funnel --bg --https=10000 --set-path=/api/public http://127.0.0.1:8091/api/public >/dev/null
tailscale serve --bg --https=9443 http://127.0.0.1:8091 >/dev/null

echo
tailscale funnel status
echo
echo "--- conteneur : $(docker compose ps --format '{{.State}}' share)"
echo "--- local : HTTP $(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8091/)"
echo "Administration : https://server-linux.tail192969.ts.net:9443"
