# server-monitor

Petite page web pour surveiller mon serveur Linux perso (CPU, RAM, VRAM, températures, batterie
et son historique, processus, erreurs matérielles), avec un onglet « Navigateur » façon WinDirStat
(tailles cumulées des dossiers, dates de création, treemap), accessible uniquement via Tailscale.

**Fait à la va-vite, pour un usage personnel.** Aucune garantie, pas de support, pas de sécurité
sérieuse au-delà de « n'écoute que sur Tailscale ». Si ça vous sert, tant mieux.

Libre de droit (domaine public, voir [LICENSE](LICENSE)) : faites-en ce que vous voulez.

## En bref

1. `cp config.example.ini config.ini` et remplir (cible SSH, port…).
2. `bash scripts/deploy.sh` (nécessite Python 3 + psutil sur le serveur, installe smartmontools).
3. Ouvrir `http://<ip-tailscale>:9110`.
