import os, subprocess, tempfile, time, unittest
from pathlib import Path

os.environ["DEVCTL_SESSION"] = "devctl-test"
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

    def test_enter_toggles_like_space(self):
        import curses
        from unittest import mock
        services = {"a": devctl.Service("a", {"dir": "/tmp", "cmd": "true"})}
        for key in (10, 13, curses.KEY_ENTER, ord(" ")):
            scr = mock.Mock(getch=mock.Mock(side_effect=[key, ord("q")]), getmaxyx=mock.Mock(return_value=(24, 80)))
            with mock.patch.multiple(curses, curs_set=mock.DEFAULT, use_default_colors=mock.DEFAULT,
                                     init_pair=mock.DEFAULT, color_pair=mock.Mock(return_value=0)), \
                 mock.patch.object(devctl, "all_states", return_value={}), \
                 mock.patch.object(devctl, "apply", return_value=[]) as apply:
                devctl.tui(scr, services, {})
            self.assertEqual([(a, s.name) for a, s in apply.call_args[0][0]], [("start", "a")], key)


class MainTest(unittest.TestCase):
    def test_missing_config_points_to_example(self):
        from unittest import mock
        with mock.patch.object(devctl, "CONFIG", Path("/nope/services.toml")):
            with self.assertRaises(SystemExit) as e:
                devctl.main(["--check"])
        self.assertIn("services.example.toml", str(e.exception.code))


class GitTest(unittest.TestCase):
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
        self.git(self.other, "checkout", "-q", "-B", branch, "origin/main")
        self.commit(self.other, name, content)
        self.git(self.other, "push", "-q", "origin", f"{branch}:{branch}")

    def branch(self):
        return self.git(self.repo, "branch", "--show-current").strip()

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
