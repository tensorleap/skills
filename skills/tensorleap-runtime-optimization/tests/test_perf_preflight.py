"""Tests for scripts/perf_preflight.sh, driven by a stub `leap` CLI (TL_CLI)."""
import os
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(os.path.dirname(HERE), "scripts", "perf_preflight.sh")


class PerfPreflightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "integration")
        os.makedirs(self.root)
        with open(os.path.join(self.root, "leap.yaml"), "w") as fh:
            fh.write("entryFile: leap_integration.py\n")

    def tearDown(self):
        self.tmp.cleanup()

    def stub_cli(self, whoami, server_info=""):
        path = os.path.join(self.tmp.name, "leap-stub")
        with open(path, "w") as fh:
            fh.write('#!/usr/bin/env bash\n'
                     'if [[ "$1 $2" == "auth whoami" ]]; then printf "%%s\\n" %s; exit 0; fi\n'
                     'if [[ "$1 $2" == "server info" ]]; then printf "%%s\\n" %s; exit 0; fi\n'
                     'exit 1\n' % (self._quote(whoami), self._quote(server_info)))
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    @staticmethod
    def _quote(lines):
        return " ".join("'%s'" % line for line in lines.splitlines()) or "''"

    def run_gate(self, cli, root=None):
        env = dict(os.environ, TL_CLI=cli, TL_SKILL_NO_UPDATE="1")
        return subprocess.run(["bash", GATE, root or self.root], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_remote_authenticated_is_ready(self):
        cli = self.stub_cli("API Url: https://tl.example.com/api/v2\nUser email: a@b.c")
        self.assertEqual(self.run_gate(cli).returncode, 0)

    def test_local_server_answering_is_ready(self):
        cli = self.stub_cli("API Url: http://localhost:4589/api/v2\nUser email: a@b.c",
                            "datasetvolumes:\n  - /data")
        self.assertEqual(self.run_gate(cli).returncode, 0)

    def test_not_authenticated_exits_3(self):
        cli = self.stub_cli("API Url: https://tl.example.com/api/v2")
        self.assertEqual(self.run_gate(cli).returncode, 3)

    def test_local_url_without_server_exits_6(self):
        cli = self.stub_cli("API Url: http://localhost:4589/api/v2\nUser email: a@b.c",
                            "Tensorleap is not running")
        self.assertEqual(self.run_gate(cli).returncode, 6)

    def test_missing_cli_exits_2(self):
        self.assertEqual(self.run_gate("leap-does-not-exist").returncode, 2)

    def test_missing_leap_yaml_exits_2(self):
        cli = self.stub_cli("API Url: https://tl.example.com/api/v2\nUser email: a@b.c")
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertEqual(self.run_gate(cli, root=empty).returncode, 2)


if __name__ == "__main__":
    unittest.main()
