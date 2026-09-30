import os, subprocess, tempfile, time, unittest
from pathlib import Path

os.environ["DEVCTL_SESSION"] = "devctl-test"
os.environ["DEVCTL_STATE"] = str(Path(tempfile.mkdtemp()) / "state.json")
import devctl


def write_config(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
    f.write(text); f.close()
    return Path(f.name)


class ConfigTest(unittest.TestCase):
    def bad(self, text):
        with self.assertRaises(devctl.ConfigError):
            devctl.load_config(write_config(text))

    def test_valid(self):
        services, groups = devctl.load_config(write_config(
            '[services.a]\ndir = "/tmp"\nnpm = "dev"\n[services.b]\ndir = "/tmp"\ncompose = true\n[groups]\ng = ["a", "b"]\n'))
        self.assertEqual(services["a"].command, "npm run dev")
        self.assertEqual(groups, {"g": ["a", "b"]})

    def test_two_kinds(self): self.bad('[services.a]\ndir = "/tmp"\nnpm = "x"\ncmd = "y"\n')
    def test_missing_dir(self): self.bad('[services.a]\ndir = "/nope/nope"\nnpm = "x"\n')
    def test_bad_name(self): self.bad('[services."a:b"]\ndir = "/tmp"\nnpm = "x"\n')
    def test_wrong_types(self):
        for text in ('services = 1\n', '[services]\na = 1\n', '[services.a]\ndir = 1\nnpm = "x"\n',
                     '[services.a]\ndir = "/tmp"\nnpm = 1\n', '[services.a]\ndir = "/tmp"\ncmd = 1\n',
                     '[services.a]\ndir = "/tmp"\ncompose = true\ncompose_files = "a.yml"\n',
                     '[services.a]\ndir = "/tmp"\nnpm = "x"\n[groups]\ng = 3\n'):
            with self.subTest(text=text):
                self.bad(text)

    def test_not_utf8(self):
        f = tempfile.NamedTemporaryFile("wb", suffix=".toml", delete=False)
        f.write(b"\xff\xfe"); f.close()
        with self.assertRaises(devctl.ConfigError):
            devctl.load_config(Path(f.name))

    def test_missing_binary_is_an_error_not_a_crash(self):
        self.assertNotEqual(devctl.sh("devctl-no-such-binary").returncode, 0)

    def test_unknown_member(self): self.bad('[services.a]\ndir = "/tmp"\nnpm = "x"\n[groups]\ng = ["zz"]\n')


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.services = {n: devctl.Service(n, {"dir": "/tmp", "cmd": "true"}) for n in "ab"}
        self.groups = {"g": ["a", "b"]}

    def names(self, rows, states):
        return [(a, s.name) for a, s in devctl.plan(rows, self.services, self.groups, states)]

    def test_partial_group_starts_missing_only(self):
        self.assertEqual(self.names(["g"], {"a": "up"}), [("start", "b")])

    def test_full_group_stops_all(self):
        self.assertEqual(self.names(["g"], {"a": "up", "b": "up"}), [("stop", "a"), ("stop", "b")])

    def test_dead_service_restarts(self):
        self.assertEqual(self.names(["a"], {"a": "dead"}), [("start", "a")])

    def test_no_duplicates(self):
        self.assertEqual(self.names(["g", "a"], {}), [("start", "a"), ("start", "b")])


class TuiGitTest(unittest.TestCase):
    def test_git_keys_act_on_row_repo_and_refresh_labels_only_after_git(self):
        import curses
        from unittest import mock
        repo = Path("/r")
        keys = [ord("p"), curses.KEY_DOWN, curses.KEY_UP, ord("m"), ord("y"), ord("b"), ord("q")]
        scr = mock.Mock(getch=mock.Mock(side_effect=keys), getmaxyx=mock.Mock(return_value=(24, 80)))
        services = {"a": devctl.Service("a", {"dir": "/tmp", "cmd": "true"})}
        m = dict(all_states=mock.Mock(return_value={}), row_repos=mock.Mock(return_value=[repo]),
                 git_label=mock.Mock(return_value="⎇ main"), pull=mock.Mock(return_value=None),
                 rebase_on_main=mock.Mock(return_value=None), branches=mock.Mock(return_value=["main", "feat"]),
                 pick=mock.Mock(return_value="feat"), checkout=mock.Mock(return_value=None))
        with mock.patch.multiple(curses, curs_set=mock.DEFAULT, use_default_colors=mock.DEFAULT,
                                 init_pair=mock.DEFAULT, color_pair=mock.Mock(return_value=0)), \
             mock.patch.multiple(devctl, **m):
            devctl.tui(scr, services, {})
        m["pull"].assert_called_once_with(repo)
        m["rebase_on_main"].assert_called_once_with(repo)
        m["checkout"].assert_called_once_with(repo, "feat")
        self.assertEqual(m["git_label"].call_count, 4)  # startup + after p, m, b; not on arrows


class LayoutTest(unittest.TestCase):
    def test_members_nested_under_group_then_ungrouped(self):
        services = {n: devctl.Service(n, {"dir": "/tmp", "cmd": "true"}) for n in "abc"}
        self.assertEqual(devctl.layout(services, {"g": ["a", "b"]}),
                         [("g", ""), ("a", "├── "), ("b", "└── "), ("c", "")])


class TmuxTest(unittest.TestCase):
    def tearDown(self):
        subprocess.run(["tmux", "-L", "devctl-test", "kill-server"], capture_output=True)

    def svc(self, name, cmd):
        return devctl.Service(name, {"dir": "/tmp", "cmd": cmd})

    def state(self, s):
        time.sleep(0.3)
        return devctl.TMUX.states([s]).get(s.name)

    def test_no_session_means_all_stopped(self):
        self.assertEqual(devctl.TMUX.states([self.svc("a", "true")]), {})

    def test_start_stop_restart(self):
        s = self.svc("sleeper", "sleep 1000")
        self.assertIsNone(devctl.TMUX.start(s))
        self.assertEqual(self.state(s), "up")
        self.assertIsNone(devctl.TMUX.stop(s))   # last window -> session gone
        self.assertIsNone(self.state(s))
        self.assertIsNone(devctl.TMUX.start(s))  # session recreated
        self.assertEqual(self.state(s), "up")

    def test_runs_on_dedicated_server_with_escape_to_detach(self):
        devctl.TMUX.start(self.svc("sleeper", "sleep 1000"))
        default = subprocess.run(["tmux", "has-session", "-t", "=devctl-test"], capture_output=True)
        self.assertNotEqual(default.returncode, 0)
        keys = subprocess.run(["tmux", "-L", "devctl-test", "list-keys", "-T", "root", "Escape"],
                              capture_output=True, text=True).stdout
        self.assertIn("detach-client", keys)

    def test_crash_is_visible_then_restartable(self):
        keep, crash = self.svc("keep", "sleep 1000"), self.svc("crash", "false")
        devctl.TMUX.start(keep); devctl.TMUX.start(crash)
        self.assertEqual(self.state(crash), "dead")
        devctl.TMUX.start(crash)
        self.assertEqual(self.state(crash), "dead")  # still crashes, but no duplicate window
        out = subprocess.run(["tmux", "-L", "devctl-test", "list-windows", "-t", "=devctl-test", "-F", "#W"],
                             capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(out), ["crash", "keep"])



class TuiTest(unittest.TestCase):
    def test_navigation_does_not_refresh_states(self):
        import curses
        from unittest import mock
        keys = [curses.KEY_DOWN] * 20 + [ord("q")]
        scr = mock.Mock(getch=mock.Mock(side_effect=keys), getmaxyx=mock.Mock(return_value=(24, 80)))
        services = {"a": devctl.Service("a", {"dir": "/tmp", "cmd": "true"})}
        with mock.patch.multiple(curses, curs_set=mock.DEFAULT, use_default_colors=mock.DEFAULT,
                                 init_pair=mock.DEFAULT, color_pair=mock.Mock(return_value=0)), \
             mock.patch.object(devctl, "all_states", return_value={}) as states:
            devctl.tui(scr, services, {})
        self.assertEqual(states.call_count, 1)

    def test_space_toggles_and_enter_menu_first_entry_toggles(self):
        import curses
        from unittest import mock
        services = {"a": devctl.Service("a", {"dir": "/tmp", "cmd": "true"})}
        for key in (10, 13, curses.KEY_ENTER, ord(" ")):
            scr = mock.Mock(getch=mock.Mock(side_effect=[key, ord("q")]), getmaxyx=mock.Mock(return_value=(24, 80)))
            with mock.patch.multiple(curses, curs_set=mock.DEFAULT, use_default_colors=mock.DEFAULT,
                                     init_pair=mock.DEFAULT, color_pair=mock.Mock(return_value=0)), \
                 mock.patch.object(devctl, "all_states", return_value={}), \
                 mock.patch.object(devctl, "apply", return_value=[]) as apply, \
                 mock.patch.object(devctl, "popup", return_value=0) as popup:
                devctl.tui(scr, services, {})
            self.assertEqual([(a, s.name) for a, s in apply.call_args[0][0]], [("start", "a")], key)
            self.assertEqual(popup.called, key != ord(" "))


class MainTest(unittest.TestCase):
    def test_missing_config_points_to_example(self):
        from unittest import mock
        with mock.patch.object(devctl, "CONFIG", Path("/nope/services.toml")):
            with self.assertRaises(SystemExit) as e:
                devctl.main(["--check"])
        self.assertIn("services.example.toml", str(e.exception.code))


class GitFixture(unittest.TestCase):
    def setUp(self):
        from unittest import mock
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start(); self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp())
        origin = self.tmp / "origin.git"
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        self.repo = self.tmp / "repo"
        self.git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        self.commit(self.repo, "a.txt", "1")
        self.git(self.repo, "remote", "add", "origin", str(origin))
        self.git(self.repo, "push", "-q", "-u", "origin", "main")
        self.other = self.tmp / "other"  # a colleague's clone
        self.git(self.tmp, "clone", "-q", str(origin), str(self.other))

    def git(self, cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout

    def commit(self, repo, name, content):
        (repo / name).write_text(content)
        self.git(repo, "add", name)
        self.git(repo, "commit", "-q", "-m", f"{name}={content}")

    def push_from_other(self, name, content, branch="main"):
        self.git(self.other, "fetch", "-q")
        self.git(self.other, "checkout", "-q", "-B", branch, "origin/main")
        self.commit(self.other, name, content)
        self.git(self.other, "push", "-q", "origin", f"{branch}:{branch}")

    def branch(self):
        return self.git(self.repo, "branch", "--show-current").strip()

    def add_worktree(self, branch):
        wt = self.repo / ".claude" / "worktrees" / branch.replace("/", "+")
        self.git(self.repo, "worktree", "add", "-q", "-b", branch, str(wt))
        return wt.resolve()


class GitTest(GitFixture):
    def test_repo_of_subdir_and_non_repo(self):
        (self.repo / "api").mkdir()
        self.assertEqual(devctl.repo_of(self.repo / "api"), self.repo.resolve())
        self.assertIsNone(devctl.repo_of(self.tmp))

    def test_row_repos_dedupes_members(self):
        (self.repo / "api").mkdir()
        services = {"a": devctl.Service("a", {"dir": str(self.repo), "compose": True}),
                    "b": devctl.Service("b", {"dir": str(self.repo / "api"), "npm": "dev"}),
                    "c": devctl.Service("c", {"dir": str(self.tmp), "cmd": "true"})}
        self.assertEqual(devctl.row_repos("g", services, {"g": ["a", "b", "c"]}), [self.repo.resolve()])

    def test_label_shows_branch_and_dirty_marker(self):
        self.assertEqual(devctl.git_label([self.repo]), "⎇ main")
        (self.repo / "a.txt").write_text("changed")
        self.assertEqual(devctl.git_label([self.repo]), "⎇ main *")

    def test_label_multi_repo_names_each(self):
        self.assertEqual(devctl.git_label([self.repo, self.other]), "⎇ repo:main · other:main")

    def test_pull_fast_forwards(self):
        self.push_from_other("b.txt", "2")
        self.assertIsNone(devctl.pull(self.repo))
        self.assertEqual((self.repo / "b.txt").read_text(), "2")

    def test_pull_refuses_diverged_branch_without_merge_commit(self):
        self.commit(self.repo, "local.txt", "x")
        self.push_from_other("b.txt", "2")
        self.assertIsNotNone(devctl.pull(self.repo))
        self.assertEqual(self.git(self.repo, "rev-list", "--count", "HEAD").strip(), "2")

    def test_rebase_on_main_updates_main_and_keeps_uncommitted_work(self):
        self.git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit(self.repo, "f.txt", "feat")
        (self.repo / "f.txt").write_text("wip")  # uncommitted -> autostash
        self.push_from_other("c.txt", "main2")
        self.assertIsNone(devctl.rebase_on_main(self.repo))
        self.assertEqual(self.branch(), "feat")
        self.assertEqual((self.repo / "c.txt").read_text(), "main2")
        self.assertEqual((self.repo / "f.txt").read_text(), "wip")
        self.assertEqual(self.git(self.repo, "rev-parse", "main"), self.git(self.repo, "rev-parse", "origin/main"))

    def test_rebase_conflict_is_aborted(self):
        self.git(self.repo, "checkout", "-q", "-b", "feat")
        self.commit(self.repo, "a.txt", "feat")
        self.push_from_other("a.txt", "main2")
        err = devctl.rebase_on_main(self.repo)
        self.assertIn("annulé", err)
        self.assertEqual(self.branch(), "feat")
        self.assertEqual((self.repo / "a.txt").read_text(), "feat")
        self.assertFalse((self.repo / ".git" / "rebase-merge").exists())

    def test_rebase_on_main_while_on_main_just_pulls(self):
        self.push_from_other("b.txt", "2")
        self.assertIsNone(devctl.rebase_on_main(self.repo))
        self.assertEqual((self.repo / "b.txt").read_text(), "2")

    def test_branches_include_remote_ones_and_checkout_tracks_them(self):
        os.environ["GIT_COMMITTER_DATE"] = "2030-01-01T00:00:00"  # setUp commits share one second
        self.push_from_other("x.txt", "1", branch="colleague")
        self.git(self.repo, "fetch", "-q")
        names = devctl.branches(self.repo)
        self.assertEqual(names[0], "colleague")  # most recent first
        self.assertIn("main", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse({"HEAD", "origin", "origin/HEAD"} & set(names))
        self.assertIsNone(devctl.checkout(self.repo, "colleague"))
        self.assertEqual(self.branch(), "colleague")


class PickTest(unittest.TestCase):
    def pick(self, keys, items):
        from unittest import mock
        scr = mock.Mock(get_wch=mock.Mock(side_effect=keys), getmaxyx=mock.Mock(return_value=(24, 80)))
        return devctl.pick(scr, "branche", items)

    def test_filter_then_enter(self):
        self.assertEqual(self.pick(["f", "e", "\n"], ["main", "feat/x", "fix"]), "feat/x")

    def test_arrows_and_backspace(self):
        import curses
        self.assertEqual(self.pick(["z", "\x7f", curses.KEY_DOWN, "\n"], ["main", "feat/x"]), "feat/x")

    def test_escape_cancels(self):
        self.assertIsNone(self.pick(["\x1b"], ["main"]))

    def test_enter_on_empty_result_cancels(self):
        self.assertIsNone(self.pick(["z", "z", "\n"], ["main"]))


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.services = {"api": devctl.Service("api", {"dir": "/tmp", "npm": "dev"}),
                         "db": devctl.Service("db", {"dir": "/tmp", "compose": True})}
        self.groups = {"g": ["db", "api"]}

    def labels(self, name, states, repos, marked=()):
        return [label for label, _ in devctl.menu_items(name, self.services, self.groups, states, repos, marked)]

    def test_running_service_in_repo(self):
        self.assertEqual(self.labels("api", {"api": "up"}, [Path("/r")]),
                         ["Arrêter", "Voir le terminal", "Changer de branche…", "Pull", "Rebase sur main…",
                          "Tout démarrer…", "Tout arrêter…"])

    def test_stopped_compose_outside_git(self):
        self.assertEqual(self.labels("db", {}, []), ["Démarrer", "Voir les logs", "Tout démarrer…", "Tout arrêter…"])

    def test_crashed_service_offers_restart(self):
        self.assertEqual(self.labels("api", {"api": "dead"}, [])[0], "Relancer")

    def test_partial_group_with_two_repos_has_no_branch_switch(self):
        labels = self.labels("g", {"db": "up"}, [Path("/r1"), Path("/r2")])
        self.assertEqual(labels[:3], ["Démarrer le groupe", "Pull", "Rebase sur main…"])

    def test_selection_replaces_row_toggle(self):
        self.assertEqual(self.labels("api", {}, [], marked={"api", "db"})[0], "Démarrer/arrêter la sélection (2)")

    def test_every_entry_maps_to_a_handled_key(self):
        keys = {k for _, k in devctl.menu_items("api", self.services, self.groups, {"api": "up"}, [Path("/r")], ())}
        self.assertLessEqual(keys, set(" lbpmas"))


class PopupTest(unittest.TestCase):
    def popup(self, keys):
        import curses
        from unittest import mock
        win = mock.Mock(getch=mock.Mock(side_effect=keys))
        scr = mock.Mock(getmaxyx=mock.Mock(return_value=(24, 80)))
        with mock.patch.object(curses, "newwin", return_value=win):
            return devctl.popup(scr, 3, 10, ["Démarrer", "Voir le terminal", "Pull"])

    def test_arrows_then_enter(self):
        import curses
        self.assertEqual(self.popup([curses.KEY_DOWN, curses.KEY_DOWN, 10]), 2)

    def test_wraps_up(self):
        import curses
        self.assertEqual(self.popup([curses.KEY_UP, 10]), 2)

    def test_escape_cancels(self):
        self.assertIsNone(self.popup([27]))


class WorktreeTest(GitFixture):
    def setUp(self):
        super().setUp()
        devctl.STATE.unlink(missing_ok=True)
        (self.repo / "api").mkdir()
        (self.repo / "api" / "x").write_text("x")
        self.git(self.repo, "add", "api"); self.git(self.repo, "commit", "-q", "-m", "api")
        self.git(self.repo, "push", "-q")
        self.services = {"db": devctl.Service("db", {"dir": str(self.repo), "compose": True}),
                         "api": devctl.Service("api", {"dir": str(self.repo / "api"), "npm": "dev"}),
                         "front": devctl.Service("front", {"dir": str(self.repo), "cmd": "true"})}
        self.groups = {"g": ["db", "api", "front"]}
        self.homes = devctl.group_homes(self.services, self.groups)
        self.home = self.repo.resolve()

    def switch(self, branch, states, locs=None):
        from unittest import mock
        locs = locs or {"g": self.home}
        with mock.patch.object(devctl, "apply", return_value=[]) as apply:
            errors = devctl.switch_group("g", branch, self.services, self.groups, self.homes, locs, states, lambda m: None)
        actions = [[(a, s.name) for a, s in c.args[0]] for c in apply.call_args_list]
        return errors, locs, [x for batch in actions for x in batch]

    def test_group_homes_single_repo_only(self):
        self.assertEqual(self.homes, {"g": self.home})
        other = {"x": devctl.Service("x", {"dir": str(self.tmp), "cmd": "true"})}
        self.assertEqual(devctl.group_homes({**self.services, **other}, {"g": ["db", "x"], "h": ["x"]}), {"g": self.home})

    def test_worktrees_maps_branches_to_dirs(self):
        wt = self.add_worktree("feat/x")
        self.assertEqual(devctl.worktrees(self.repo), {"main": self.home, "feat/x": wt})

    def test_switch_to_worktree_moves_npm_restarts_running_only_keeps_docker(self):
        wt = self.add_worktree("feat/x")
        errors, locs, actions = self.switch("feat/x", {"api": "up", "db": "up"})
        self.assertEqual(errors, [])
        self.assertEqual(locs["g"], wt)
        self.assertEqual(self.services["api"].dir, wt / "api")
        self.assertEqual(self.services["front"].dir, wt)
        self.assertEqual(self.services["db"].dir, self.home)  # docker stays in the main folder
        self.assertEqual(actions, [("stop", "api"), ("start", "api")])
        self.assertEqual(self.git(self.repo, "branch", "--show-current").strip(), "main")  # no checkout

    def test_switch_back_to_main_returns_home_and_checks_out_main(self):
        wt = self.add_worktree("feat/x")
        self.git(self.repo, "checkout", "-q", "-b", "other")
        _, locs, _ = self.switch("feat/x", {})
        errors, locs, _ = self.switch("main", {}, locs)
        self.assertEqual(errors, [])
        self.assertEqual(locs["g"], self.home)
        self.assertEqual(self.services["api"].dir, self.home / "api")
        self.assertEqual(self.git(self.repo, "branch", "--show-current").strip(), "main")

    def test_branch_without_worktree_is_checked_out_in_main_folder(self):
        self.git(self.repo, "branch", "fix")
        errors, locs, actions = self.switch("fix", {"api": "up"})
        self.assertEqual(errors, [])
        self.assertEqual(locs["g"], self.home)
        self.assertEqual(actions, [])  # same folder: dev servers reload by themselves
        self.assertEqual(self.git(self.repo, "branch", "--show-current").strip(), "fix")

    def test_location_persists_and_missing_worktree_falls_back(self):
        wt = self.add_worktree("feat/x")
        self.switch("feat/x", {})
        fresh = {n: devctl.Service(n, {"dir": str(s.base_dir), **{s.kind: s.command or True}})
                 for n, s in self.services.items() if s.kind != "npm"}
        fresh["api"] = devctl.Service("api", {"dir": str(self.repo / "api"), "npm": "dev"})
        locs, notes = devctl.restore_locations(fresh, self.groups, self.homes)
        self.assertEqual((locs["g"], fresh["api"].dir, notes), (wt, wt / "api", []))
        self.git(self.repo, "worktree", "remove", "--force", str(wt))
        fresh["api"] = devctl.Service("api", {"dir": str(self.repo / "api"), "npm": "dev"})
        locs, notes = devctl.restore_locations(fresh, self.groups, self.homes)
        self.assertEqual(locs["g"], self.home)
        self.assertEqual(fresh["api"].dir, self.home / "api")
        self.assertEqual(len(notes), 1)

    def test_rebase_on_main_from_worktree_updates_main_in_main_folder(self):
        wt = self.add_worktree("feat/x")
        self.commit(wt, "f.txt", "feat")
        self.push_from_other("c.txt", "main2")
        self.assertIsNone(devctl.rebase_on_main(wt))
        self.assertEqual((self.repo / "c.txt").read_text(), "main2")  # main folder pulled
        self.assertEqual((wt / "c.txt").read_text(), "main2")


class TmuxMissingDirTest(unittest.TestCase):
    def tearDown(self):
        subprocess.run(["tmux", "-L", "devctl-test", "kill-server"], capture_output=True)

    def test_start_in_missing_dir_is_an_error(self):
        s = devctl.Service("a", {"dir": "/tmp", "cmd": "sleep 1000"})
        s.dir = Path("/nope/nope")
        self.assertIn("introuvable", devctl.TMUX.start(s))


class TuiWorktreeTest(unittest.TestCase):
    def run_tui(self, keys, rows_down=0):
        import curses
        from unittest import mock
        home, wt = Path("/r"), Path("/r/.claude/worktrees/feat")
        scr = mock.Mock(getch=mock.Mock(side_effect=[curses.KEY_DOWN] * rows_down + keys + [ord("q")]),
                        getmaxyx=mock.Mock(return_value=(24, 80)))
        services = {"api": devctl.Service("api", {"dir": "/tmp", "npm": "dev"})}
        self.seen = []
        def pick(scr, title, items):
            self.seen = items
            return next(i for i in items if i.startswith("feat"))
        m = dict(all_states=mock.Mock(return_value={}), row_repos=mock.Mock(return_value=[home]),
                 git_label=mock.Mock(return_value="⎇ main"), group_homes=mock.Mock(return_value={"g": home}),
                 restore_locations=mock.Mock(return_value=({"g": home}, [])),
                 worktrees=mock.Mock(return_value={"main": home, "feat": wt}),
                 branches=mock.Mock(return_value=["feat", "main", "fix"]), pick=pick,
                 switch_group=mock.Mock(return_value=[]), checkout=mock.Mock(return_value=None))
        with mock.patch.multiple(curses, curs_set=mock.DEFAULT, use_default_colors=mock.DEFAULT,
                                 init_pair=mock.DEFAULT, color_pair=mock.Mock(return_value=0)), \
             mock.patch.multiple(devctl, **m):
            devctl.tui(scr, services, {"g": ["api"]})
        return m

    def test_branch_on_group_switches_worktree_with_tagged_list(self):
        m = self.run_tui([ord("b")])
        self.assertEqual(m["switch_group"].call_args.args[:2], ("g", "feat"))
        m["checkout"].assert_not_called()
        tags = {i.split()[0]: " ".join(i.split()[1:]) for i in self.seen}
        self.assertEqual(tags, {"feat": "worktree", "main": "dossier principal", "fix": "checkout dossier principal"})

    def test_branch_on_member_row_acts_for_its_group(self):
        m = self.run_tui([ord("b")], rows_down=1)
        self.assertEqual(m["switch_group"].call_args.args[:2], ("g", "feat"))
