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
