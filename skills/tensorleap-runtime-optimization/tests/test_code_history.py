import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_tl_perf import SYNTH, TL_PERF, SyntheticEnv, tl_perf  # noqa: E402
from test_profile import FAST  # noqa: E402

import perf_code  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def read(path):
    with open(path) as fh:
        return fh.read()


class CodeFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.out = os.path.join(self.root, "tensorleap", "runtime-optimization")
        write(os.path.join(self.root, "leap_integration.py"), "x = 1\n")
        write(os.path.join(self.root, "pkg", "utils.py"), "y = 2\n")
        write(os.path.join(self.root, "requirements.txt"), "numpy\n")
        write(os.path.join(self.root, "leap.yaml"), "entryFile: leap_integration.py\n")
        write(os.path.join(self.root, "data", "image.png"), "binary")
        write(os.path.join(self.root, "data", "big.json"), "0" * (perf_code.MAX_FILE_BYTES + 1))
        for vcs in (".git", ".hg", ".svn"):
            write(os.path.join(self.root, vcs, "config.txt"), "vcs state")
        write(os.path.join(self.root, "myenv", "pyvenv.cfg"), "home = /usr\n")
        write(os.path.join(self.root, "myenv", "lib", "site.py"), "z = 3\n")
        write(os.path.join(self.out, "runs", "001", "profile.json"), "{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_source_files_outside_vcs_venv_and_output(self):
        self.assertEqual(perf_code.code_files(self.root, self.out),
                         ["leap_integration.py", "pkg/utils.py", "requirements.txt"])

    def test_snapshot_dedups_blobs(self):
        write(os.path.join(self.root, "copy.py"), "x = 1\n")
        files = perf_code.snapshot(self.root, self.out)
        self.assertEqual(files["copy.py"], files["leap_integration.py"])
        self.assertEqual(len(os.listdir(os.path.join(self.out, "code", "objects"))), 3)

    def test_cap_markers_are_found_in_code_and_config_not_notes(self):
        write(os.path.join(self.root, "leap_integration.py"),
              "ids = ids[:50]  # smoke-validation cap: not for merge\n")
        write(os.path.join(self.root, "project_config.yaml"),
              "sample_limit_per_split: 40  # diagnostics cap: not for merge\n")
        write(os.path.join(self.root, "notes.txt"), "# diagnostics cap: in a note\n")
        self.assertEqual(perf_code.cap_markers(self.root, self.out),
                         ["leap_integration.py:1", "project_config.yaml:1"])

    def test_diff_covers_added_changed_and_removed_files(self):
        objects = perf_code.snapshot(self.root, self.out)
        write(os.path.join(self.root, "leap_integration.py"), "x = 2\n")
        write(os.path.join(self.root, "new.py"), "n = 0")
        os.remove(os.path.join(self.root, "pkg", "utils.py"))
        text = perf_code.diff(self.out, objects, perf_code.snapshot(self.root, self.out))
        self.assertIn("--- a/leap_integration.py\n+++ b/leap_integration.py\n", text)
        self.assertIn("-x = 1\n+x = 2\n", text)
        self.assertIn("--- /dev/null\n+++ b/new.py\n", text)
        self.assertIn("+n = 0\n\\ No newline at end of file\n", text)
        self.assertIn("--- a/pkg/utils.py\n+++ /dev/null\n", text)


class RestoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.out = os.path.join(self.root, "tensorleap", "runtime-optimization")
        self.base = os.path.join(self.out, "baseline")
        os.makedirs(self.base)
        write(os.path.join(self.root, "leap_integration.py"), "x = 1\n")
        write(os.path.join(self.root, "pkg", "utils.py"), "y = 2\n")
        write(os.path.join(self.root, "leap.yaml"), "projectId: ''\n")
        perf_code.save(self.base, self.root, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_back_to_the_baseline_without_touching_leap_yaml(self):
        write(os.path.join(self.root, "leap_integration.py"), "x = 99  # smoke-validation cap: not for merge\n")
        os.remove(os.path.join(self.root, "pkg", "utils.py"))
        write(os.path.join(self.root, "helper.py"), "h = 1\n")
        write(os.path.join(self.root, "leap.yaml"), "projectId: 'abc'\n")
        res = perf_code.restore(self.root, self.out)
        self.assertEqual(res["restored"], ["leap_integration.py", "pkg/utils.py"])
        self.assertEqual(res["moved_aside"], ["helper.py"])
        self.assertEqual(read(os.path.join(self.root, "leap_integration.py")), "x = 1\n")
        self.assertEqual(read(os.path.join(self.root, "pkg", "utils.py")), "y = 2\n")
        self.assertFalse(os.path.exists(os.path.join(self.root, "helper.py")))
        self.assertEqual(read(os.path.join(res["aside_dir"], "helper.py")), "h = 1\n")
        self.assertEqual(read(os.path.join(self.root, "leap.yaml")), "projectId: 'abc'\n")
        self.assertEqual(perf_code.cap_markers(self.root, self.out), [])

    def test_idempotent(self):
        write(os.path.join(self.root, "leap_integration.py"), "x = 5\n")
        perf_code.restore(self.root, self.out)
        res = perf_code.restore(self.root, self.out)
        self.assertEqual((res["restored"], res["moved_aside"]), ([], []))

    def test_goes_to_the_accepted_run_when_there_is_one(self):
        run = os.path.join(self.out, "runs", "002")
        os.makedirs(run)
        write(os.path.join(self.root, "leap_integration.py"), "x = 2\n")
        perf_code.save(run, self.root, self.out)
        tl_perf.write_json(os.path.join(self.out, "accepted.json"), {"run": run})
        write(os.path.join(self.root, "leap_integration.py"), "x = 3\n")
        perf_code.restore(self.root, self.out)
        self.assertEqual(read(os.path.join(self.root, "leap_integration.py")), "x = 2\n")

    def test_keeps_the_executable_bit(self):
        script = os.path.join(self.root, "run.sh")
        write(script, "echo 1\n")
        os.chmod(script, 0o755)
        perf_code.save(self.base, self.root, self.out)
        write(script, "echo 2\n")
        perf_code.restore(self.root, self.out)
        self.assertEqual(read(script), "echo 1\n")
        self.assertTrue(os.access(script, os.X_OK))

    def test_changes_after_the_profile_are_reported(self):
        self.assertEqual(perf_code.changed_since(self.base, self.root, self.out), [])
        write(os.path.join(self.root, "pkg", "utils.py"), "y = 3\n")
        write(os.path.join(self.root, "extra.py"), "e = 1\n")
        self.assertEqual(perf_code.changed_since(self.base, self.root, self.out), ["extra.py", "pkg/utils.py"])

    def test_nothing_saved_is_a_blocker(self):
        shutil.rmtree(self.base)
        self.assertIsNone(perf_code.restore(self.root, self.out))
        self.assertEqual(tl_perf.main(["restore", "--root", self.root]), tl_perf.EXIT_BLOCKER)


class BeforeBaselineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.out = os.path.join(self.root, "tensorleap", "runtime-optimization")
        write(os.path.join(self.root, "leap_integration.py"), "loss = 1\n")
        perf_code.save_start(self.root, self.out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_is_kept_from_the_first_preflight(self):
        write(os.path.join(self.root, "leap_integration.py"), "loss = 2\n")
        perf_code.save_start(self.root, self.out)
        start = perf_code.load(perf_code._start(self.out))
        self.assertEqual(start, {"leap_integration.py": hashlib.sha256(b"loss = 1\n").hexdigest()})

    def test_fix_before_the_baseline_is_patch_00_and_numbering_starts_at_01(self):
        write(os.path.join(self.root, "leap_integration.py"), "loss = 2\n")
        base = os.path.join(self.out, "baseline")
        os.makedirs(base)
        perf_code.save(base, self.root, self.out)
        path = perf_code.record_baseline(self.out, base)
        self.assertEqual(os.path.basename(path), "00-before-baseline.patch")
        self.assertIn("-loss = 1\n+loss = 2\n", read(path))
        run = os.path.join(self.out, "runs", "002")
        os.makedirs(run)
        write(os.path.join(self.root, "leap_integration.py"), "loss = 3\n")
        perf_code.save(run, self.root, self.out)
        self.assertEqual(os.path.basename(perf_code.promote(self.out, base, run)), "01-run002.patch")

    def test_no_patch_when_nothing_changed_before_the_baseline(self):
        base = os.path.join(self.out, "baseline")
        os.makedirs(base)
        perf_code.save(base, self.root, self.out)
        self.assertIsNone(perf_code.record_baseline(self.out, base))

    def test_restore_before_any_baseline_returns_to_the_code_as_found(self):
        write(os.path.join(self.root, "leap_integration.py"), "loss = 9\n")
        perf_code.restore(self.root, self.out)
        self.assertEqual(read(os.path.join(self.root, "leap_integration.py")), "loss = 1\n")

    def test_the_default_output_dir_is_skipped_for_any_out(self):
        write(os.path.join(self.out, "runs", "001", "profile.json"), "{}")
        smoke = os.path.join(self.out, "smoke")
        self.assertEqual(perf_code.code_files(self.root, smoke), ["leap_integration.py"])


class CapGuardCliTest(unittest.TestCase):
    def test_compare_and_report_refuse_while_a_cap_is_in_the_code(self):
        with tempfile.TemporaryDirectory() as root:
            write(os.path.join(root, "leap_integration.py"),
                  "ids = ids[:40]  # diagnostics cap: not for merge\n")
            self.assertEqual(tl_perf.main(["compare", "--root", root]), tl_perf.EXIT_BLOCKER)
            self.assertEqual(tl_perf.main(["report", "--root", root]), tl_perf.EXIT_BLOCKER)


class ProfileCompareRestoreFlow(unittest.TestCase):
    def setUp(self):
        self.synth = SyntheticEnv()
        self.root = os.path.join(self.synth.tmp.name, "integration")
        shutil.copytree(SYNTH, self.root, ignore=shutil.ignore_patterns("__pycache__"))
        self.entry = os.path.join(self.root, "leap_integration.py")
        self.original = read(self.entry)

    def tearDown(self):
        self.synth.close()

    def tl(self, *args, **env):
        cmd = [sys.executable, TL_PERF] + list(args) + ["--root", self.root, "--out", self.synth.out]
        return subprocess.run(cmd, env=dict(self.synth.env, **env), capture_output=True, text=True,
                              timeout=300)

    def test_accept_writes_a_patch_and_restore_undoes_a_rejected_change(self):
        slow = dict(SYNTH_REDUNDANT="1", SYNTH_DECODE_MS="4")
        p = self.tl("profile", *FAST, **slow)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        write(self.entry, self.original + "# fix: decode once\n")
        p = self.tl("profile", *FAST, SYNTH_CACHE="1", **slow)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        c = self.tl("compare")
        self.assertEqual(c.returncode, tl_perf.EXIT_OK, c.stdout + c.stderr)
        fixes = os.path.join(self.synth.out, "fixes")
        patches = sorted(os.listdir(fixes))
        self.assertEqual(len(patches), 1)
        self.assertIn("+# fix: decode once\n", read(os.path.join(fixes, patches[0])))
        self.assertEqual(self.tl("compare").returncode, tl_perf.EXIT_OK)
        self.assertEqual(len(os.listdir(fixes)), 1)

        accepted = read(self.entry)
        write(self.entry, accepted + "# attempt: no gain\n")
        write(os.path.join(self.root, "helper.py"), "h = 1\n")
        p = self.tl("profile", *FAST, SYNTH_CACHE="1", **slow)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        c = self.tl("compare")
        self.assertNotEqual(c.returncode, tl_perf.EXIT_OK)
        self.assertIn("tl_perf restore", c.stdout)
        r = self.tl("restore")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(read(self.entry), accepted)
        self.assertFalse(os.path.exists(os.path.join(self.root, "helper.py")))
        self.assertEqual(len(os.listdir(fixes)), 1)


if __name__ == "__main__":
    unittest.main()
