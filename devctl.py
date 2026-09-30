#!/usr/bin/env python3
"""devctl: start/stop docker compose stacks and npm daemons from one terminal menu."""
import curses, locale, os, re, shlex, subprocess, sys, time, tomllib
from pathlib import Path

SESSION = os.environ.get("DEVCTL_SESSION", "devctl")
CONFIG = Path(os.environ.get("DEVCTL_CONFIG", Path(__file__).resolve().parent / "services.toml"))
KINDS = ("npm", "compose", "cmd")
TMUX_CMD = ("tmux", "-L", SESSION)  # dedicated server: Esc binding can't leak into the user's own tmux
SETUP = (";", "bind", "-n", "Escape", "detach-client", ";", "set", "-g", "escape-time", "10",
         ";", "set", "-g", "status-right", "Esc : retour à devctl ")


class ConfigError(Exception):
    pass


def check(ok, msg):
    if not ok:
        raise ConfigError(msg)


class Service:
    def __init__(self, name, spec):
        check(isinstance(spec, dict), f"{name}: doit être une table [services.{name}]")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ConfigError(f"{name}: nom invalide (lettres, chiffres, - et _ uniquement)")
        kinds = [k for k in KINDS if k in spec]
        if len(kinds) != 1:
            raise ConfigError(f"{name}: il faut exactement un de npm / compose / cmd")
        for key in ("dir", "npm", "cmd"):
            check(key not in spec or isinstance(spec[key], str), f"{name}: {key} doit être une chaîne")
        files = spec.get("compose_files", [])
        check(isinstance(files, list) and all(isinstance(f, str) for f in files),
              f"{name}: compose_files doit être une liste de chaînes")
        if "dir" not in spec:
            raise ConfigError(f"{name}: dir manquant")
        self.name, self.kind = name, kinds[0]
        self.dir = Path(spec["dir"]).expanduser().resolve()
        if not self.dir.is_dir():
            raise ConfigError(f"{name}: dir introuvable ({self.dir})")
        self.files = files
        self.command = {"npm": lambda: f"npm run {shlex.quote(spec['npm'])}",
                        "cmd": lambda: spec["cmd"], "compose": lambda: None}[self.kind]()
        self.engine = COMPOSE if self.kind == "compose" else TMUX


def load_config(path):
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise ConfigError("fichier non UTF-8")
    raw, groups = data.get("services", {}), data.get("groups", {})
    check(isinstance(raw, dict), "services doit être une table")
    check(isinstance(groups, dict), "groups doit être une table")
    services = {n: Service(n, s) for n, s in raw.items()}
    for g, members in groups.items():
        check(isinstance(members, list) and all(isinstance(m, str) for m in members),
              f"groupe {g}: doit être une liste de noms")
        if g in services:
            raise ConfigError(f"groupe {g}: même nom qu'un service")
        unknown = [m for m in members if m not in services]
        if unknown:
            raise ConfigError(f"groupe {g}: services inconnus {unknown}")
    return services, groups


def expand(name, services, groups):
    return [services[m] for m in groups[name]] if name in groups else [services[name]]


def plan(rows, services, groups, states):
    """Toggle each row: all up -> stop them, otherwise start what isn't up."""
    actions, seen = [], set()
    for row in rows:
        members = [s for s in expand(row, services, groups) if s.name not in seen]
        all_up = all(states.get(s.name) == "up" for s in members)
        for s in members:
            seen.add(s.name)
            if all_up:
                actions.append(("stop", s))
            elif states.get(s.name) != "up":
                actions.append(("start", s))
    return actions


def sh(*args, cwd=None):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: introuvable")


def attach(target):
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}  # allow nesting inside the user's tmux
    subprocess.call([*TMUX_CMD, "attach", "-t", target], env=env)


def error(r):
    if r.returncode == 0:
        return None
    lines = (r.stderr or r.stdout).strip().splitlines()
    return lines[-1] if lines else f"exit {r.returncode}"


class Tmux:
    """npm/cmd daemons: one tmux window per service in session SESSION."""

    def target(self, svc):
        return f"={SESSION}:={svc.name}"

    def states(self, svcs):
        r = sh(*TMUX_CMD, "list-windows", "-t", f"={SESSION}", "-F", "#{window_name} #{pane_dead}")
        if r.returncode:
            return {}
        return {name: "dead" if dead == "1" else "up"
                for name, dead in (line.rsplit(" ", 1) for line in r.stdout.splitlines())}

    def start(self, svc):
        sh(*TMUX_CMD, "kill-window", "-t", self.target(svc))  # drop a dead window, no-op otherwise
        if sh(*TMUX_CMD, "has-session", "-t", f"={SESSION}").returncode == 0:
            new = ["new-window", "-d", "-t", f"={SESSION}:"]
        else:
            new = ["new-session", "-d", "-s", SESSION]
        return error(sh(*TMUX_CMD, *new, "-n", svc.name, "-c", str(svc.dir), svc.command,
                        ";", "set-option", "-w", "-t", self.target(svc), "remain-on-exit", "on", *SETUP))

    def stop(self, svc):
        sh(*TMUX_CMD, "send-keys", "-t", self.target(svc), "C-c")
        for _ in range(50):
            if self.states([svc]).get(svc.name) != "up":
                break
            time.sleep(0.1)
        return error(sh(*TMUX_CMD, "kill-window", "-t", self.target(svc)))

    def logs(self, svc):
        sh(*TMUX_CMD, *SETUP[1:])
        attach(self.target(svc))


class Compose:
    def cmd(self, svc, *args):
        return ["docker", "compose", *[a for f in svc.files for a in ("-f", f)], *args]

    def states(self, svcs):
        # ponytail: matches on the project's working_dir label; two services on the same dir share a state
        r = sh("docker", "ps", "--format", '{{.Label "com.docker.compose.project.working_dir"}}')
        running = set(r.stdout.splitlines())
        return {s.name: "up" for s in svcs if str(s.dir) in running}

    def start(self, svc):
        return error(sh(*self.cmd(svc, "up", "-d"), cwd=svc.dir))

    def stop(self, svc):
        return error(sh(*self.cmd(svc, "down"), cwd=svc.dir))

    def logs(self, svc):
        # run in a throwaway tmux session so Esc returns to the menu like for npm daemons
        name = f"logs-{svc.name}"
        sh(*TMUX_CMD, "new-session", "-d", "-s", name, "-c", str(svc.dir),
           shlex.join(self.cmd(svc, "logs", "-f", "--tail", "200")), *SETUP)
        attach(f"={name}")
        sh(*TMUX_CMD, "kill-session", "-t", f"={name}")


TMUX, COMPOSE = Tmux(), Compose()


def all_states(services):
    states = {}
    for engine in (TMUX, COMPOSE):
        svcs = [s for s in services.values() if s.engine is engine]
        if svcs:
            states.update(engine.states(svcs))
    return states


def apply(actions, show=print):
    errors = []
    for action, s in actions:
        show(f"{action} {s.name}…")
        err = getattr(s.engine, action)(s)
        if err:
            errors.append(f"{s.name}: {err}")
    return errors


def git(repo, *args):
    return sh("git", "-C", str(repo), *args)


def repo_of(path):
    r = git(path, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 else None


def row_repos(name, services, groups):
    """Distinct git repos behind a row (group or service), in member order."""
    repos = []
    for s in expand(name, services, groups):
        r = repo_of(s.dir)
        if r and r not in repos:
            repos.append(r)
    return repos


def branch_label(repo):
    branch = git(repo, "branch", "--show-current").stdout.strip() or "(detached)"
    dirty = git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    return branch + (" *" if dirty else "")


def git_label(repos):
    if len(repos) == 1:
        return "⎇ " + branch_label(repos[0])
    return "⎇ " + " · ".join(f"{r.name}:{branch_label(r)}" for r in repos) if repos else ""


def main_branch(repo):
    r = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0:
        return r.stdout.strip().removeprefix("origin/")
    for b in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}").returncode == 0:
            return b
    return "main"


def pull(repo):
    return error(git(repo, "pull", "--ff-only"))


def rebase_on_main(repo):
    """Update local main from origin, then rebase the current branch on it; abort on conflict."""
    main = main_branch(repo)
    if git(repo, "branch", "--show-current").stdout.strip() == main:
        return pull(repo)
    err = error(git(repo, "fetch", "origin", f"{main}:{main}"))
    if err:
        return err
    if git(repo, "rebase", "--autostash", main).returncode:
        git(repo, "rebase", "--abort")
        return f"conflits avec {main}, rebase annulé (à faire à la main)"
    return None


def branches(repo):
    """Local and origin branches, most recently committed first, without duplicates."""
    r = git(repo, "for-each-ref", "--sort=-committerdate", "--format=%(refname:short)",
            "refs/heads", "refs/remotes/origin")
    names = []
    for ref in r.stdout.splitlines():
        name = ref.removeprefix("origin/")
        if name not in ("HEAD", "origin") and name not in names:
            names.append(name)
    return names


def checkout(repo, branch):
    return error(git(repo, "checkout", branch))  # remote-only branch -> git creates the tracking branch


def pick(scr, title, items):
    """Filterable list: type to filter, arrows to move, Enter picks, Esc cancels (None)."""
    query, cur = "", 0
    scr.timeout(-1)
    try:
        while True:
            shown = [i for i in items if query.lower() in i.lower()]
            cur = min(cur, max(0, len(shown) - 1))
            h, w = scr.getmaxyx()
            top = max(0, cur - (h - 4))
            scr.erase()
            try:
                scr.addnstr(0, 0, f"{title} — tape pour filtrer : {query}", w - 1, curses.A_BOLD)
                for i, item in enumerate(shown[top: top + h - 3]):
                    scr.addnstr(i + 2, 2, item, w - 3, curses.A_REVERSE if top + i == cur else 0)
                scr.addnstr(h - 1, 0, "↑↓ choisir · entrée valider · esc annuler", w - 1, curses.A_DIM)
            except curses.error:
                pass
            scr.refresh()
            key = scr.get_wch()
            if key == "\x1b":
                return None
            if key in ("\n", "\r", curses.KEY_ENTER):
                return shown[cur] if shown else None
            if key == curses.KEY_UP:
                cur = max(0, cur - 1)
            elif key == curses.KEY_DOWN:
                cur += 1
            elif key in ("\x7f", "\b", curses.KEY_BACKSPACE):
                query, cur = query[:-1], 0
            elif isinstance(key, str) and key.isprintable():
                query, cur = query + key, 0
    finally:
        scr.timeout(2000)

ICONS = {"up": ("●", 1), "dead": ("✖", 2)}
HELP = "entrée actions · espace start/stop · x cocher · r refresh · q quitter"


def row_view(name, services, groups, states):
    if name in groups:
        ups = [states.get(m) == "up" for m in groups[name]]
        icon = ("●", 1) if all(ups) else ("◐", 3) if any(ups) else ("○", 0)
        return icon, "groupe"
    st = states.get(name)
    return ICONS.get(st, ("○", 0)), services[name].kind + ("  crashé, l pour voir" if st == "dead" else "")


def layout(services, groups):
    """(name, tree prefix) rows: each group followed by its members, then ungrouped services."""
    rows = []
    for g, members in groups.items():
        rows.append((g, ""))
        rows += [(m, "└── " if i == len(members) - 1 else "├── ") for i, m in enumerate(members)]
    grouped = {m for members in groups.values() for m in members}
    return rows + [(n, "") for n in services if n not in grouped]


def draw(scr, rows, prefixes, cur, marked, services, groups, states, labels, msg):
    scr.erase()
    h, w = scr.getmaxyx()
    try:
        scr.addnstr(0, 0, f"devctl — session tmux '{SESSION}'", w - 1, curses.A_BOLD)
        for i, name in enumerate(rows[: max(0, h - 3)]):
            (icon, color), info = row_view(name, services, groups, states)
            rev = curses.A_REVERSE if i == cur else 0
            scr.addnstr(i + 2, 0, "[x] " if name in marked else "[ ] ", w - 1, rev)
            scr.addstr(prefixes[i], rev)
            scr.addstr(icon, curses.color_pair(color) | rev)
            scr.addnstr(f" {name:<{28 - len(prefixes[i])}} {info:<8} {labels.get(name, '')}", max(0, w - 10), rev)
        scr.addnstr(h - 1, 0, msg, w - 1, curses.A_DIM)
    except curses.error:
        pass  # terminal too small
    scr.refresh()


def menu_items(name, services, groups, states, repos, marked):
    """(label, shortcut key) entries for the row's action menu; keys are the ones tui() handles."""
    if marked:
        items = [(f"Démarrer/arrêter la sélection ({len(marked)})", " ")]
    elif name in groups:
        all_up = all(states.get(m) == "up" for m in groups[name])
        items = [("Arrêter le groupe" if all_up else "Démarrer le groupe", " ")]
    else:
        st = states.get(name)
        items = [({"up": "Arrêter", "dead": "Relancer"}.get(st, "Démarrer"), " "),
                 ("Voir les logs" if services[name].kind == "compose" else "Voir le terminal", "l")]
    if len(repos) == 1:
        items.append(("Changer de branche…", "b"))
    if repos:
        items += [("Pull", "p"), ("Rebase sur main…", "m")]
    return items + [("Tout démarrer…", "a"), ("Tout arrêter…", "s")]


def popup(scr, y, x, labels):
    """Boxed menu near (y, x): arrows + Enter return the chosen index, Esc/q return None."""
    h, w = scr.getmaxyx()
    width, height = min(max(map(len, labels)) + 6, w), min(len(labels) + 2, h)
    win = curses.newwin(height, width, max(0, min(y, h - height)), max(0, min(x, w - width)))
    win.keypad(True)
    cur = 0
    try:
        while True:
            win.erase()
            win.box()
            for i, label in enumerate(labels[: height - 2]):
                win.addnstr(i + 1, 1, ("▸ " if i == cur else "  ") + label, width - 2,
                            curses.A_REVERSE if i == cur else 0)
            win.refresh()
            key = win.getch()
            if key in (27, ord("q")):
                return None
            if key in (10, 13, curses.KEY_ENTER):
                return cur
            if key in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(labels)
            elif key in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(labels)
    finally:
        scr.touchwin()  # the main screen doesn't know the popup painted over it


def confirm(scr, show, question):
    show(f"{question} (y/n)")
    scr.timeout(-1)
    ok = scr.getch() == ord("y")
    scr.timeout(2000)
    return ok


def git_each(repos, action, show, verb):
    errors = []
    for r in repos:
        show(f"{verb} {r.name}…")
        err = action(r)
        if err:
            errors.append(f"{r.name}: {err}")
    return errors


def tui(scr, services, groups):
    curses.curs_set(0)
    curses.use_default_colors()
    for i, c in enumerate((curses.COLOR_GREEN, curses.COLOR_RED, curses.COLOR_YELLOW), 1):
        curses.init_pair(i, c, -1)
    scr.timeout(2000)
    rows, prefixes = map(list, zip(*layout(services, groups)))
    repos = {name: row_repos(name, services, groups) for name in set(rows)}
    cur, marked, msg, stale, git_stale = 0, set(), HELP, True, True
    while True:
        if stale:  # only on timer/actions: refreshing per keypress makes held arrows lag
            states, stale = all_states(services), False
        if git_stale:  # git status is slow on big repos: only at startup, after git actions and on r
            labels = {n: git_label(repos[n]) for n, p in zip(rows, prefixes) if not p}
            git_stale = False
        show = lambda m: draw(scr, rows, prefixes, cur, marked, services, groups, states, labels, m)
        show(msg)
        key = scr.getch()
        if key in (10, 13, curses.KEY_ENTER):
            items = menu_items(rows[cur], services, groups, states, repos[rows[cur]],
                               [r for r in rows if r in marked])
            choice = popup(scr, min(cur, len(rows) - 1) + 3, 8 + len(prefixes[cur]), [l for l, _ in items])
            key = -1 if choice is None else ord(items[choice][1])
        if key == ord("q"):
            return
        stale = key not in (curses.KEY_UP, ord("k"), curses.KEY_DOWN, ord("j"), ord("x"), curses.KEY_RESIZE)
        if key in (curses.KEY_UP, ord("k")):
            cur = (cur - 1) % len(rows)
        elif key in (curses.KEY_DOWN, ord("j")):
            cur = (cur + 1) % len(rows)
        elif key == ord("x"):
            marked ^= {rows[cur]}
        elif key == ord(" "):
            targets = [r for r in rows if r in marked] or [rows[cur]]
            errors = apply(plan(targets, services, groups, states), show)
            marked.clear()
            msg = " · ".join(errors) or HELP
        elif key == ord("l"):
            if rows[cur] in groups:
                msg = "logs : choisis un service, pas un groupe"
            else:
                s = services[rows[cur]]
                curses.endwin()
                s.engine.logs(s)
                scr.refresh()
        elif key == ord("r"):
            git_stale = True
        elif key in (ord("b"), ord("p"), ord("m")):
            row_repo = repos[rows[cur]]
            errors = []
            if not row_repo:
                errors = [f"{rows[cur]} : pas dans un repo git"]
            elif key == ord("p"):
                errors = git_each(row_repo, pull, show, "pull")
            elif key == ord("m"):
                if confirm(scr, show, f"Mettre à jour main et rebaser {rows[cur]} dessus ?"):
                    errors = git_each(row_repo, rebase_on_main, show, "rebase")
            elif len(row_repo) > 1:
                errors = [f"{rows[cur]} : plusieurs repos, choisis la ligne d'un service"]
            else:
                branch = pick(scr, f"branche de {row_repo[0].name}", branches(row_repo[0]))
                if branch:
                    errors = git_each(row_repo, lambda r: checkout(r, branch), show, f"checkout {branch}")
            git_stale = True
            msg = " · ".join(errors) or HELP
        elif key in (ord("a"), ord("s")):
            start = key == ord("a")
            if confirm(scr, show, f"Tout {'démarrer' if start else 'arrêter'} ?"):
                actions = [("start" if start else "stop", s) for s in services.values()
                           if (states.get(s.name) != "up" if start else s.name in states)]
                msg = " · ".join(apply(actions, show)) or HELP


def main(argv):
    if not CONFIG.exists():
        sys.exit(f"devctl: pas de config ({CONFIG}).\n"
                 f"  cp {Path(__file__).resolve().parent / 'services.example.toml'} {CONFIG}\n"
                 "  puis adapte-la (ou demande à Claude, cf. CLAUDE.md).")
    try:
        services, groups = load_config(CONFIG)
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"devctl: {CONFIG}: {e}")
    if not services:
        sys.exit(f"devctl: aucun service dans {CONFIG}")
    if argv == ["--check"]:
        states = all_states(services)
        for s in services.values():
            print(f"{ICONS.get(states.get(s.name), ('○',))[0]} {s.name:<24} {s.kind:<8} {s.dir}")
        return
    if argv[:1] in (["up"], ["down"]) and len(argv) > 1:
        unknown = [n for n in argv[1:] if n not in services and n not in groups]
        if unknown:
            sys.exit(f"devctl: inconnu : {', '.join(unknown)}")
        states = all_states(services)
        svcs = list({s.name: s for n in argv[1:] for s in expand(n, services, groups)}.values())
        if argv[0] == "up":
            actions = [("start", s) for s in svcs if states.get(s.name) != "up"]
        else:
            actions = [("stop", s) for s in svcs if s.name in states]
        errors = apply(actions)
        for e in errors:
            print("erreur", e, file=sys.stderr)
        sys.exit(1 if errors else 0)
    if argv:
        sys.exit("usage: devctl [--check | up NOM... | down NOM...]")
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")  # Esc in the branch picker without curses' 1 s delay
    curses.wrapper(tui, services, groups)


if __name__ == "__main__":
    main(sys.argv[1:])
