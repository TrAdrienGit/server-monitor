#!/bin/bash
# Installe le service de transcription. La supervision se déploie à part (monitor.py, index.html).
# À lancer en root dans un terminal interactif. Peut être relancé.
set -euo pipefail
cd "$(dirname "$0")"

DIR=/data/transcribe
install -d -m 755 "$DIR" "$DIR/build"
install -m 644 compose.yaml "$DIR/compose.yaml"
install -m 644 Dockerfile worker.py "$DIR/build/"
install -d -o 1000 -g 1000 -m 755 "$DIR/models" "$DIR/state" "$DIR/work"
install -d -m 700 "$DIR/secrets"

if [ ! -s "$DIR/secrets/nextcloud" ]; then
    echo "Dans Nextcloud : Paramètres personnels > Sécurité > mot de passe d'application nommé Transcription."
    read -r -s -p "Colle-le ici (rien ne s'affiche) : " secret; echo
    [ -n "$secret" ] || { echo "Mot de passe vide." >&2; exit 1; }
    printf '%s' "$secret" > "$DIR/secrets/nextcloud"
    unset secret
fi
chown 1000:1000 "$DIR/secrets/nextcloud"
chmod 400 "$DIR/secrets/nextcloud"

cd "$DIR"
docker compose build
docker compose down
# Créé mais pas démarré : c'est toi qui l'allumes depuis la supervision.
docker compose up --no-start
tailscale serve --bg --https=9444 http://127.0.0.1:8093 >/dev/null

echo "--- transcription : $(docker compose ps --format '{{.State}}' worker) (éteinte par défaut)"
tailscale funnel status
