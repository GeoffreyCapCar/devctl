# devctl — spec

## But
Démarrer / arrêter depuis un seul menu terminal les stacks `docker compose` et les scripts npm "daemon" répartis dans plusieurs dossiers. WSL d'abord ; une copie Windows (npm uniquement) en étape 2.

## Hors scope (v1)
- Panneau de logs intégré au TUI (on passe par tmux ; à reconsidérer si insuffisant).
- Moteur Windows (étape 2 — mais le code est découpé pour l'accueillir).
- Redémarrage auto, healthchecks, dépendances entre services.

## Livrables
- `~/devctl/devctl` : script Python 3.11+ unique, stdlib uniquement (`curses`, `tomllib`, `subprocess`).
- `~/devctl/services.toml` : config de l'utilisateur.
- `~/bin/devctl` : symlink vers le script.
- `~/devctl/devctl.bat` : lanceur Windows → `wsl.exe -e bash -lc devctl`.

## Config (`services.toml`, à côté du script ou `$DEVCTL_CONFIG`)
```toml
[services.monolith-api]
dir = "~/code/monolith/api"
npm = "start:dev"                     # → npm run start:dev

[services.monolith-db]
dir = "~/code/monolith"
compose = true                         # → docker compose up -d / down
# compose_files = ["docker-compose.yml"]  optionnel → -f ...

[groups]
monolith = ["monolith-db", "monolith-api"]
```
Règles : un service a exactement un de `npm` / `compose` / `cmd` (commande shell libre). `dir` doit exister. Un groupe ne référence que des services existants. Config invalide → message clair + exit 1, pas de TUI.

## Moteurs
Interface commune : `status(svc) -> bool`, `start(svc)`, `stop(svc)`, `logs(svc)`.

**tmux (npm / cmd)** — session `devctl`, une fenêtre par service nommée comme lui.
- status : la fenêtre existe (`tmux list-windows -t devctl -F '#W'`).
- start : crée la session si absente, `new-window -d -n <nom> -c <dir> '<commande>'`. La fenêtre meurt avec le process → statut à jour automatiquement.
- stop : `send-keys C-c`, attente ≤ 5 s, puis `kill-window` si toujours là.
- logs : quitte curses, `tmux attach -t devctl:<nom>` (retour avec `Ctrl+b d`), relance le TUI.

**compose**
- status : `docker compose ps -q --status running` non vide.
- start / stop : `up -d` / `down`.
- logs : quitte curses, `docker compose logs -f --tail 200` (retour avec Ctrl+C).

## TUI
Liste : groupes en haut, puis services. Chaque ligne : `[x]` coché, `●` vert / `○` gris, nom, type.
Groupe : `●` si tous ses membres tournent, `◐` si une partie, `○` sinon.

| Touche | Action |
|---|---|
| ↑ ↓ / j k | naviguer |
| x | cocher / décocher la ligne |
| espace | start/stop : les lignes cochées si il y en a, sinon la ligne courante. Démarrée → stop, arrêtée (ou groupe partiel) → start |
| l | logs de la ligne courante (service seulement) |
| a / s | tout démarrer / tout arrêter (avec confirmation `y`) |
| r | rafraîchir |
| q | quitter (les daemons continuent de tourner) |

Statut rafraîchi toutes les 2 s. Une barre en bas affiche la dernière action ou erreur (ex. `monolith-api: dir introuvable`). Les actions lancées dans le TUI tournent en séquence ; une erreur sur un service n'arrête pas les suivants.

## CLI non interactif
- `devctl --check` : valide la config, affiche une ligne d'état par service, exit 0/1. Sert aussi de test.
- `devctl up <nom|groupe>...` / `devctl down <nom|groupe>...` : même logique que le TUI, pour les scripts.

## Étape 2 (Windows, pas maintenant)
Copie du script + son propre `services.toml`. `pip install windows-curses`. Moteur `winproc` choisi quand `os.name == "nt"` : start = `start "<nom>" cmd /k ...` avec PID stocké dans `%LOCALAPPDATA%\devctl\<nom>.pid`, stop = `taskkill /T /F /PID`, logs = focus sur la fenêtre (ou simplement lecture). `compose` sous Windows → `wsl docker compose`.

## Test
`devctl --check` sur la vraie config + une vérification manuelle : un service `cmd = "sleep 1000"` factice démarre, apparaît `●`, s'arrête, disparaît.
