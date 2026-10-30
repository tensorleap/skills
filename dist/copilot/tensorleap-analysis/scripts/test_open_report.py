import json
import os
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tl_api import _desktop_opener  # noqa: E402


def make_report_dir(api_base="http://localhost:4589/api/v2"):
    root = tempfile.mkdtemp()
    open(os.path.join(root, "report.html"), "w").write("<html></html>")
    with open(os.path.join(root, "insights.json"), "w") as f:
        json.dump({"api_base": api_base, "links": {"insights_panel": "http://localhost:4589/p"}}, f)
    return root


def fake_openers():
    """A bin dir whose `open` and `xdg-open` record the file they were asked to open."""
    bindir = tempfile.mkdtemp()
    log = os.path.join(bindir, "opened.txt")
    for name in ("open", "xdg-open"):
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write(f'#!/bin/sh\necho "$1" >> "{log}"\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return bindir, log


def run(root, **env):
    base = {k: v for k, v in os.environ.items()
            if k not in ("SSH_CONNECTION", "SSH_TTY", "DISPLAY", "WAYLAND_DISPLAY")}
    base.update(env)
    return subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "open-report",
                           os.path.join(root, "report.html")], capture_output=True, text=True, env=base)


def check_desktop_opener():
    assert _desktop_opener({}, "darwin") == ("open", "")
    assert _desktop_opener({"DISPLAY": ":0"}, "linux") == ("xdg-open", "")
    assert _desktop_opener({"WAYLAND_DISPLAY": "wayland-0"}, "linux") == ("xdg-open", "")
    assert _desktop_opener({}, "linux") == (None, "no display")
    assert _desktop_opener({"SSH_CONNECTION": "1.2.3.4 5 10.0.0.5 22"}, "darwin") == (None, "SSH session")
    assert _desktop_opener({"SSH_TTY": "/dev/pts/0", "DISPLAY": ":0"}, "linux") == (None, "SSH session")
    assert _desktop_opener({}, "win32")[0] is None


def check_local_desktop_opens_the_report():
    root = make_report_dir()
    bindir, log = fake_openers()
    r = run(root, DISPLAY=":0", PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    report = os.path.join(root, "report.html")
    assert open(log).read().strip() == report
    assert f"opened: {report}" in r.stdout
    assert "insights panel: http://localhost:4589/p" in r.stdout
    assert "scp" not in r.stdout and "tunnel" not in r.stdout


def check_ssh_session_says_how_to_copy_and_tunnel():
    root = make_report_dir()
    bindir, log = fake_openers()
    r = run(root, SSH_CONNECTION="1.2.3.4 51000 10.0.0.5 22", DISPLAY=":0",
            PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(log)
    report = os.path.join(root, "report.html")
    assert "not opened: SSH session" in r.stdout
    assert f"@10.0.0.5:{report} ." in r.stdout
    assert "tunnel: ssh -N -L 4589:localhost:4589 " in r.stdout
    assert "insights panel: http://localhost:4589/p" in r.stdout


def check_remote_server_needs_no_tunnel():
    root = make_report_dir(api_base="https://acme.tensorleap.ai/api/v2")
    r = run(root, SSH_TTY="/dev/pts/0")
    assert r.returncode == 0, r.stderr
    assert "copy: scp " in r.stdout
    assert "tunnel" not in r.stdout


def check_failing_opener_gives_the_path():
    root = make_report_dir()
    bindir = tempfile.mkdtemp()
    for name in ("open", "xdg-open"):
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\nexit 3\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    r = run(root, DISPLAY=":0", PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert "not opened: " in r.stdout and "exited 3" in r.stdout
    assert f"report: {os.path.join(root, 'report.html')}" in r.stdout
    assert "scp" not in r.stdout


def check_missing_report_is_bad_args():
    r = run(tempfile.mkdtemp())
    assert r.returncode == 2


def main():
    failed = []
    for name, check in list(globals().items()):
        if not name.startswith("check_"):
            continue
        try:
            check()
            print(f"PASS {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
