# devctl

Menu terminal pour démarrer/arrêter des stacks `docker compose` et des daemons npm (dans tmux).

- Installation : `cp services.example.toml services.toml` (à adapter, ou demande à Claude : il lit `CLAUDE.md`), `ln -sf ~/devctl/devctl.py ~/bin/devctl`, puis `devctl`. Prérequis : Python 3.11+, tmux.
- Touches : `↑↓` naviguer · `entrée` menu d'actions de la ligne (start/stop, terminal/logs, branche, pull, rebase sur main, tout démarrer/arrêter) · `espace` start/stop direct (lignes cochées ou courante) · `x` cocher · `r` rafraîchir · `q` quitter (les daemons continuent). Dans le terminal d'un service : `Esc` pour revenir.
- Raccourcis cachés (même effet que le menu) : `l` terminal/logs · `b` branche · `p` pull · `m` rebase main · `a`/`s` tout démarrer/arrêter
- CLI : `devctl --check` · `devctl up monolith` · `devctl down monolith-api`
- Config : `services.toml`, personnel et gitignoré (ou `$DEVCTL_CONFIG`) — `[services.<nom>]` avec `dir` + `npm = "script"` | `compose = true` | `cmd = "..."`, et `[groups]`
- Git : la branche de chaque groupe s'affiche (`*` = modifs non commitées), rafraîchie au lancement, après une action git et avec `r`.
- Worktrees : dans « Changer de branche… » d'un groupe mono-repo, une branche qui a un worktree (ex. `.claude/worktrees/…`) fait tourner les services npm/cmd du groupe depuis ce worktree (ceux qui tournaient sont relancés) ; `main` et les autres branches restent dans le dossier principal (checkout). Docker ne bouge jamais. Deux groupes du même repo peuvent être dans des worktrees différents. Le choix est mémorisé dans `~/.local/state/devctl/worktrees.json`.
- Daemons : serveur tmux dédié (`tmux -L devctl attach`). `✖` = crashé, `l` pour voir l'erreur.
- Windows : double-clic sur `devctl.bat` (lance `devctl` dans WSL)
