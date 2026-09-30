# devctl

Menu terminal pour démarrer/arrêter des stacks `docker compose` et des daemons npm (dans tmux).

- Installation : `cp services.example.toml services.toml` (à adapter, ou demande à Claude : il lit `CLAUDE.md`), `ln -sf ~/devctl/devctl.py ~/bin/devctl`, puis `devctl`. Prérequis : Python 3.11+, tmux.
- Touches : `↑↓` naviguer · `entrée`/`espace` start/stop (lignes cochées ou courante) · `x` cocher · `l` logs / terminal du service (`Esc` pour revenir) · `a`/`s` tout démarrer/arrêter · `b` changer de branche (filtre en tapant) · `p` pull (ff-only) · `m` met à jour main puis rebase la branche dessus (annulé proprement en cas de conflit) · `q` quitter (les daemons continuent)
- CLI : `devctl --check` · `devctl up monolith` · `devctl down monolith-api`
- Config : `services.toml`, personnel et gitignoré (ou `$DEVCTL_CONFIG`) — `[services.<nom>]` avec `dir` + `npm = "script"` | `compose = true` | `cmd = "..."`, et `[groups]`
- Git : la branche de chaque groupe s'affiche (`*` = modifs non commitées), rafraîchie au lancement, après une action git et avec `r`.
- Daemons : serveur tmux dédié (`tmux -L devctl attach`). `✖` = crashé, `l` pour voir l'erreur.
- Windows : double-clic sur `devctl.bat` (lance `devctl` dans WSL)
