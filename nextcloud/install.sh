#!/bin/bash
# Installe Nextcloud dans /data/nextcloud, accessible uniquement par Tailscale (port 8443).
# À lancer en root dans un terminal interactif. Peut être relancé sans risque.
set -euo pipefail
cd "$(dirname "$0")"

DIR=/data/nextcloud
URL=https://server-linux.tail192969.ts.net:8443

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    echo "--- Installation de Docker"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io docker-compose-v2
    systemctl enable --now docker
fi

install -d -m 755 "$DIR"
install -d -m 700 "$DIR/secrets"
install -m 644 compose.yaml "$DIR/compose.yaml"

if [ ! -e "$DIR/secrets/admin_password" ]; then
    while :; do
        read -r -s -p "Mot de passe du compte Nextcloud « adrien » : " p1; echo
        read -r -s -p "Confirmation : " p2; echo
        if [ -n "$p1" ] && [ "$p1" = "$p2" ]; then break; fi
        echo "Vide ou différents, recommence."
    done
    printf '%s' "$p1" > "$DIR/secrets/admin_password"
    unset p1 p2
fi
if [ ! -s "$DIR/secrets/db_password" ]; then
    (umask 077; openssl rand -hex 32 | tr -d '\n' > "$DIR/secrets/db_password")
fi
# Lisibles dans les conteneurs (root ne franchit que le dossier secrets, en 700 sur l'hôte).
chmod 644 "$DIR/secrets/admin_password" "$DIR/secrets/db_password"

cd "$DIR"
echo "--- Démarrage des conteneurs (téléchargement au premier lancement)"
docker compose up -d

echo "--- Attente de l'installation de Nextcloud (5 minutes au plus)"
ok=""
for _ in $(seq 1 60); do
    if curl -fsS -m 5 http://127.0.0.1:8090/status.php 2>/dev/null | grep -q '"installed":true'; then ok=1; break; fi
    sleep 5
done
if [ -z "$ok" ]; then
    echo "Nextcloud ne répond pas. Journal :" >&2
    docker compose logs --tail=30 app >&2
    exit 1
fi

occ() { docker compose exec -T -u www-data app php occ "$@"; }
occ background:cron
occ config:system:set default_phone_region --value=FR
occ config:system:set maintenance_window_start --type=integer --value=2

echo "--- Exposition sur Tailscale uniquement"
tailscale serve --bg --https=8443 http://127.0.0.1:8090

# Le mot de passe initial n'est utile qu'à la création du compte : on ne le garde pas en clair.
: > "$DIR/secrets/admin_password"

echo
tailscale funnel status
echo
echo "Nextcloud : $URL"
docker compose ps --format 'table {{.Service}}\t{{.State}}'
