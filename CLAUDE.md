# devctl

Menu terminal (Python stdlib, un seul fichier `devctl.py`) pour démarrer/arrêter des stacks docker compose et des daemons npm. Les daemons tournent dans un serveur tmux dédié (`tmux -L devctl`).

## Aider l'utilisateur à écrire `services.toml`

`services.toml` est personnel et gitignoré ; partir de `services.example.toml`. Il se trouve à côté de `devctl.py` (ou `$DEVCTL_CONFIG`).

```toml
[services.<nom>]              # nom : lettres, chiffres, - et _ uniquement
dir = "~/chemin/du/projet"     # obligatoire, doit exister
# exactement UN des trois :
npm = "start:dev"              # → npm run start:dev (tmux)
compose = true                 # → docker compose up -d / down, dans dir
cmd = "npx nps worker"         # → commande shell libre (tmux)
# compose_files = ["docker-compose.yml", "docker-compose.override.yml"]  # optionnel → -f ...

[groups]
<groupe> = ["<nom>", "<nom>"]  # nom ≠ nom de service ; affiché en arbre dans le menu
```

Pour proposer des services, inspecter les projets de l'utilisateur :
- `docker-compose*.yml` / `compose.yml` → un service `compose = true` par dossier (suffixe `-docker` par convention).
- `package.json` → scripts longue durée (`start:dev`, `dev`, `*:start`…), pas `build`/`test`/`lint`.
- `package-scripts.js` (nps) → `cmd = "npx nps <raccourci>"`.
- Grouper l'env docker et les daemons d'un même projet.

Valider avec `devctl --check` (exit 1 + message si la config est invalide).

## Dev

Tests : `python3 -m unittest` (utilise le serveur tmux `devctl-test`). Aucune dépendance pip.
