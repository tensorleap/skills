"""run_integration.sh's RESULT line must match code-loader's verdict: exit 0 is "ready to push",
exit 3 is "not ready to push" with the next part to add, anything else is a failed run. Older
code-loader versions print the "not ready" table line but exit 0; that is still "not ready"."""
import os
import stat
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "run_integration.sh")

NEW_INCOMPLETE = ("This integration is not ready to push: `tensorleap_custom_loss` is not used in the "
                  "integration test yet. Add it next.")
NEW_READY = "Ready to push: all mandatory parts are in the integration test.\nOptional next: `tensorleap_custom_metric`."
OLD_INCOMPLETE = ("Successful!\nSome mandatory components have not yet been added to the Integration test. "
                  "Recommended next interface to add is: tensorleap_custom_loss")
OLD_READY = ("Successful!\nAll mandatory parts have been successfully set. If no errors accured, you can now push "
             "the project to the Tensorleap system or continue to the next optional reccomeded interface,adding: "
             "tensorleap_custom_metric (optional)")


def run(output, exit_code, entry="leap_integration.py"):
    with tempfile.TemporaryDirectory() as repo:
        open(os.path.join(repo, entry), "w").close()
        fake_py = os.path.join(repo, "fake_python")
        with open(fake_py, "w") as f:
            f.write("#!/usr/bin/env bash\ncat <<'EOF'\n%s\nEOF\nexit %d\n" % (output, exit_code))
        os.chmod(fake_py, os.stat(fake_py).st_mode | stat.S_IEXEC)
        env = dict(os.environ, TL_PY=fake_py, TL_DISABLE_ANALYTICS="1")
        proc = subprocess.run(["bash", SCRIPT, repo, entry], env=env, capture_output=True, text=True)
        result = [line for line in proc.stdout.splitlines() if line.startswith("RESULT:")]
        return proc.returncode, result[0] if result else "", proc.stdout


def test_exit_3_is_not_ready_and_names_the_next_part():
    code, result, out = run(NEW_INCOMPLETE, 3)
    assert code == 3, out
    assert result == "RESULT: integration incomplete, NOT ready to push. Next: tensorleap_custom_loss.", out


def test_exit_0_with_verdict_is_ready():
    code, result, out = run(NEW_READY, 0)
    assert code == 0, out
    assert result == "RESULT: ready to push. Every mandatory part ran in the integration test.", out


def test_crash_is_a_failed_run():
    code, result, out = run("Traceback (most recent call last):\nValueError: boom", 1)
    assert code == 1, out
    assert result.startswith("RESULT: run failed (exit 1)"), out


def test_older_code_loader_incomplete_is_not_ready_even_with_exit_0():
    code, result, out = run(OLD_INCOMPLETE, 0)
    assert code == 3, out
    assert result == "RESULT: integration incomplete, NOT ready to push. Next: tensorleap_custom_loss.", out
    assert "stage passed" not in out, out


def test_older_code_loader_mandatory_ready_is_ready():
    code, result, out = run(OLD_READY, 0)
    assert code == 0, out
    assert result.startswith("RESULT: ready to push."), out


def test_clean_exit_without_a_verdict_is_not_called_ready():
    code, result, out = run("hello", 0, entry="my_integration.py")
    assert code == 0, out
    assert result.startswith("RESULT: clean exit, but no readiness verdict was printed"), out
