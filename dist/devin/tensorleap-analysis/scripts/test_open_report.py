import os
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tl_api import _desktop_opener  # noqa: E402

LOCAL = "http://localhost:4589/project/p1/dashboard?report=r1"
REMOTE = "https://acme.tensorleap.ai/project/p1/dashboard?report=r1"


def fake_openers(script='echo "$1" >> "{log}"'):
    """A bin dir whose `open` and `xdg-open` record the link they were asked to open."""
    bindir = tempfile.mkdtemp()
    log = os.path.join(bindir, "opened.txt")
    for name in ("open", "xdg-open"):
        path = os.path.join(bindir, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + script.format(log=log) + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return bindir, log


def run(link, **env):
    base = {k: v for k, v in os.environ.items()
            if k not in ("SSH_CONNECTION", "SSH_TTY", "DISPLAY", "WAYLAND_DISPLAY")}
    base.update(env)
    return subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "open-report", link],
                          capture_output=True, text=True, env=base)


def check_desktop_opener():
    assert _desktop_opener({}, "darwin") == ("open", "")
    assert _desktop_opener({"DISPLAY": ":0"}, "linux") == ("xdg-open", "")
    assert _desktop_opener({"WAYLAND_DISPLAY": "wayland-0"}, "linux") == ("xdg-open", "")
    assert _desktop_opener({}, "linux") == (None, "no display")
    assert _desktop_opener({"SSH_CONNECTION": "1.2.3.4 5 10.0.0.5 22"}, "darwin") == (None, "SSH session")
    assert _desktop_opener({"SSH_TTY": "/dev/pts/0", "DISPLAY": ":0"}, "linux") == (None, "SSH session")
    assert _desktop_opener({}, "win32")[0] is None


def check_local_desktop_opens_the_published_report():
    bindir, log = fake_openers()
    r = run(LOCAL, DISPLAY=":0", PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert open(log).read().strip() == LOCAL
    assert f"opened: {LOCAL}" in r.stdout
    assert "tunnel" not in r.stdout


def check_ssh_session_says_how_to_reach_a_server_on_this_machine():
    bindir, log = fake_openers()
    r = run(LOCAL, SSH_CONNECTION="1.2.3.4 51000 10.0.0.5 22", DISPLAY=":0",
            PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(log)
    assert "not opened: SSH session" in r.stdout
    assert f"report: {LOCAL}" in r.stdout
    assert "tunnel: ssh -N -L 4589:localhost:4589 " in r.stdout and "@10.0.0.5" in r.stdout


def check_loopback_ssh_address_becomes_a_placeholder():
    # AWS SSM and other local port forwards give SSH_CONNECTION a loopback server address
    for addr in ("127.0.0.1", "127.0.1.1", "::1"):
        r = run("http://127.0.0.1:4589/project/p1/dashboard?report=r1",
                SSH_CONNECTION=f"127.0.0.1 51000 {addr} 22")
        assert r.returncode == 0, r.stderr
        assert "tunnel: ssh -N -L 4589:localhost:4589 " in r.stdout and r.stdout.count("@<server>") == 1, r.stdout
        assert f"@{addr}" not in r.stdout


def check_remote_server_needs_no_tunnel():
    r = run(REMOTE, SSH_TTY="/dev/pts/0")
    assert r.returncode == 0, r.stderr
    assert f"report: {REMOTE}" in r.stdout
    assert "tunnel" not in r.stdout


def check_failing_opener_gives_the_link():
    bindir, _ = fake_openers("exit 3")
    r = run(LOCAL, DISPLAY=":0", PATH=bindir + os.pathsep + os.environ["PATH"])
    assert r.returncode == 0, r.stderr
    assert "not opened: " in r.stdout and "exited 3" in r.stdout
    assert f"report: {LOCAL}" in r.stdout
    assert "tunnel" not in r.stdout


def check_unknown_user_gets_a_placeholder():
    import tl_api
    assert tl_api._or_placeholder(lambda: "tom", "<user>") == "tom"
    assert tl_api._or_placeholder(lambda: (_ for _ in ()).throw(KeyError("no uid")), "<user>") == "<user>"
    assert tl_api._or_placeholder(lambda: "", "<host>") == "<host>"


def check_only_a_link_is_opened():
    bindir, log = fake_openers()
    for not_a_link in ("/tmp/report.html", "file:///etc/passwd", "--help-me", "javascript:alert(1)"):
        r = run(not_a_link, DISPLAY=":0", PATH=bindir + os.pathsep + os.environ["PATH"])
        assert r.returncode == 2, (not_a_link, r.returncode, r.stdout)
    assert not os.path.exists(log)


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
