"""Tests for scripts/assistant.sh and the Codex-only hint in the install preflight.

Run: python3 skills/_shared/tests/test_assistant.py
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
ASSISTANT = os.path.join(SKILLS, "_shared", "scripts", "assistant.sh")
PREFLIGHT = os.path.join(SKILLS, "tensorleap-install", "scripts", "install_preflight.sh")
ENV_VARS = ("CODEX_THREAD_ID", "CLAUDECODE", "CURSOR_TRACE_ID")
HINT = "Optional: leap mcp config codex lets Codex run leap commands"


def clean_env(home, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS}
    env["HOME"] = home
    env.update(extra)
    return env


class AssistantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def install_at(self, skill_dir, *scripts):
        """Copy assistant.sh (and any other scripts) into <skill_dir>/scripts/."""
        scripts_dir = os.path.join(skill_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        for src in (ASSISTANT,) + scripts:
            shutil.copy(src, scripts_dir)
        return scripts_dir

    def detect(self, scripts_dir, **env):
        out = subprocess.run(["bash", os.path.join(scripts_dir, "assistant.sh")],
                             env=clean_env(self.home, **env), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_env_vars_decide_in_order(self):
        scripts = self.install_at(os.path.join(self.tmp.name, "elsewhere", "tensorleap-install"))
        self.assertEqual(self.detect(scripts, CODEX_THREAD_ID="t1"), "codex")
        self.assertEqual(self.detect(scripts, CLAUDECODE="1"), "claude-code")
        self.assertEqual(self.detect(scripts, CURSOR_TRACE_ID="x"), "cursor")
        # Codex first, then Claude Code, then Cursor (e.g. Codex run inside Cursor's terminal)
        self.assertEqual(self.detect(scripts, CODEX_THREAD_ID="t1", CLAUDECODE="1",
                                     CURSOR_TRACE_ID="x"), "codex")
        self.assertEqual(self.detect(scripts, CLAUDECODE="1", CURSOR_TRACE_ID="x"), "claude-code")
        # an empty variable is not set
        self.assertEqual(self.detect(scripts, CODEX_THREAD_ID="", CURSOR_TRACE_ID="x"), "cursor")

    def test_env_var_beats_install_folder(self):
        scripts = self.install_at(os.path.join(self.home, ".agents", "skills", "tensorleap-install"))
        self.assertEqual(self.detect(scripts, CURSOR_TRACE_ID="x"), "cursor")

    def test_install_folder_fallback(self):
        cases = {
            os.path.join(self.home, ".agents", "skills"): "codex",
            os.path.join(self.tmp.name, "proj", ".agents", "skills"): "codex",
            os.path.join(self.home, ".cursor", "skills"): "cursor",
            os.path.join(self.tmp.name, "proj", ".cursor", "skills"): "cursor",
            os.path.join(self.home, ".copilot", "skills"): "copilot",
            os.path.join(self.tmp.name, "proj", ".github", "skills"): "copilot",
            os.path.join(self.home, ".codeium", "windsurf", "skills"): "windsurf",
            os.path.join(self.tmp.name, "proj", ".windsurf", "skills"): "windsurf",
            os.path.join(self.home, ".claude", "plugins", "cache", "tensorleap", "installer",
                         "0.1.5", "skills"): "claude-code",
            os.path.join(self.tmp.name, "somewhere", "skills"): "unknown",
        }
        for parent, want in cases.items():
            with self.subTest(parent=parent):
                scripts = self.install_at(os.path.join(parent, "tensorleap-install"))
                self.assertEqual(self.detect(scripts), want)

    def test_source_defines_function_and_prints_nothing(self):
        scripts = self.install_at(os.path.join(self.home, ".agents", "skills", "tensorleap-install"))
        script = 'source "%s"; echo "[$(tl_assistant)]"' % os.path.join(scripts, "assistant.sh")
        out = subprocess.run(["bash", "-c", script], env=clean_env(self.home),
                             capture_output=True, text=True)
        self.assertEqual(out.stdout, "[codex]\n")


class PreflightCodexHintTest(unittest.TestCase):
    """install_preflight.sh prints the leap-rule hint under Codex only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        # stubs so the preflight touches nothing real: no docker, sudo or lsof on this machine
        self.bin = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin)
        for name in ("docker", "sudo", "lsof", "leap"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\nexit 1\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)

    def tearDown(self):
        self.tmp.cleanup()

    def preflight(self, skills_parent, **env):
        scripts = os.path.join(skills_parent, "tensorleap-install", "scripts")
        os.makedirs(scripts, exist_ok=True)
        shutil.copy(ASSISTANT, scripts)
        shutil.copy(PREFLIGHT, scripts)
        run_env = clean_env(self.home, TL_SKILL_NO_UPDATE="1", TL_DISABLE_ANALYTICS="1",
                            PATH=self.bin + os.pathsep + os.environ["PATH"], **env)
        out = subprocess.run(["bash", os.path.join(scripts, "install_preflight.sh")],
                             env=run_env, capture_output=True, text=True, timeout=60)
        return out.stdout

    def test_hint_under_codex(self):
        out = self.preflight(os.path.join(self.tmp.name, "other"), CODEX_THREAD_ID="t1")
        self.assertIn(HINT, out)
        self.assertEqual(out.count(HINT), 1)

    def test_hint_from_codex_install_folder(self):
        out = self.preflight(os.path.join(self.home, ".agents", "skills"))
        self.assertIn(HINT, out)

    def test_no_hint_for_other_assistants(self):
        for env, parent in (({"CURSOR_TRACE_ID": "x"}, os.path.join(self.home, ".cursor", "skills")),
                            ({"CLAUDECODE": "1"}, os.path.join(self.tmp.name, "other")),
                            ({}, os.path.join(self.home, ".copilot", "skills")),
                            ({}, os.path.join(self.tmp.name, "unknown"))):
            with self.subTest(env=env, parent=parent):
                out = self.preflight(parent, **env)
                self.assertNotIn(HINT, out)
                self.assertNotIn("leap mcp config codex", out)
                self.assertIn("== Shell ==", out)  # the report still ran to the end

    def test_cursor_in_codex_folder_gets_no_hint(self):
        # an assistant's own variable wins over the folder it found the skill in
        out = self.preflight(os.path.join(self.home, ".agents", "skills"), CURSOR_TRACE_ID="x")
        self.assertNotIn(HINT, out)


if __name__ == "__main__":
    unittest.main()
