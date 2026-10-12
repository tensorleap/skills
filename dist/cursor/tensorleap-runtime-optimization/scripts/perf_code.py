import difflib
import hashlib
import json
import os
import re
import shutil
import time

CODE_EXT = (".py", ".pyi", ".pyx", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".txt", ".json", ".sh")
SKIP_DIRS = {".git", ".hg", ".svn", ".bzr", "_darcs", ".jj", ".venv", "venv", "__pycache__",
             "node_modules", ".tox", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints",
             ".idea", ".vscode"}
SKIP_FILES = {"leap.yaml"}
TL_OUT = os.path.join("tensorleap", "runtime-optimization")
MAX_FILE_BYTES = 1 << 20
CAP_MARKER = re.compile(r"#\s*(smoke-validation|diagnostics) cap:")


def _store(out):
    return os.path.join(out, "code")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def code_files(root, out):
    skip_out = {os.path.realpath(out), os.path.realpath(os.path.join(root, TL_OUT))}
    found = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS
                         and os.path.realpath(os.path.join(d, x)) not in skip_out
                         and not os.path.exists(os.path.join(d, x, "pyvenv.cfg")))
        for f in sorted(files):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, root)
            if (f.endswith(CODE_EXT) and rel not in SKIP_FILES and not os.path.islink(p)
                    and os.path.getsize(p) <= MAX_FILE_BYTES):
                found.append(rel)
    return sorted(found)


def snapshot(root, out):
    objects = os.path.join(_store(out), "objects")
    os.makedirs(objects, exist_ok=True)
    files = {}
    for rel in code_files(root, out):
        src = os.path.join(root, rel)
        sha = _sha(src)
        blob = os.path.join(objects, sha)
        if not os.path.exists(blob):
            shutil.copyfile(src, blob + ".tmp")
            os.replace(blob + ".tmp", blob)
        files[rel] = sha
    return files


def save(run_dir, root, out):
    files = snapshot(root, out)
    with open(os.path.join(run_dir, "code.json"), "w", encoding="utf-8") as fh:
        json.dump({"saved": time.strftime("%Y-%m-%d %H:%M:%S"), "files": files}, fh, indent=1)
    return files


def _start(out):
    return os.path.join(_store(out), "start")


def save_start(root, out):
    if not os.path.exists(os.path.join(_start(out), "code.json")):
        os.makedirs(_start(out), exist_ok=True)
        save(_start(out), root, out)


def load(run_dir):
    path = os.path.join(run_dir or "", "code.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["files"]


def reference_run(out):
    path = os.path.join(out, "accepted.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            run = json.load(fh).get("run")
        if run and os.path.isdir(run):
            return run
    for d in (os.path.join(out, "baseline"), _start(out)):
        if os.path.isdir(d):
            return d
    return None


def _current(root, out):
    return {rel: _sha(os.path.join(root, rel)) for rel in code_files(root, out)}


def changed_since(run_dir, root, out):
    saved = load(run_dir)
    if saved is None:
        return []
    now = _current(root, out)
    return sorted(rel for rel in set(saved) | set(now) if saved.get(rel) != now.get(rel))


def cap_markers(root, out):
    hits = []
    for rel in code_files(root, out):
        if not rel.endswith((".py", ".yaml", ".yml", ".toml", ".cfg", ".ini")):
            continue
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if CAP_MARKER.search(line):
                    hits.append("%s:%d" % (rel, n))
    return hits


def _lines(out, sha):
    if sha is None:
        return []
    with open(os.path.join(_store(out), "objects", sha), encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines(keepends=True)


def diff(out, old, new):
    chunks = []
    for rel in sorted(set(old) | set(new)):
        if old.get(rel) == new.get(rel):
            continue
        name = rel.replace(os.sep, "/")
        a = "a/" + name if rel in old else "/dev/null"
        b = "b/" + name if rel in new else "/dev/null"
        body = list(difflib.unified_diff(_lines(out, old.get(rel)), _lines(out, new.get(rel)), a, b))
        chunks.append("".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n"
                              for l in body))
    return "".join(chunks)


def _write_patch(out, name, old_dir, new_dir):
    old, new = load(old_dir), load(new_dir)
    if old is None or new is None:
        return None
    text = diff(out, old, new)
    if not text:
        return None
    fixes = os.path.join(out, "fixes")
    os.makedirs(fixes, exist_ok=True)
    path = os.path.join(fixes, name(fixes))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def record_baseline(out, baseline_dir):
    return _write_patch(out, lambda fixes: "00-before-baseline.patch", _start(out), baseline_dir)


def promote(out, ref_dir, cur_dir):
    run = os.path.basename(os.path.normpath(cur_dir))
    return _write_patch(out, lambda fixes: "%02d-run%s.patch" % (
        len([f for f in os.listdir(fixes) if f.endswith(".patch") and not f.startswith("00-")]) + 1, run),
        ref_dir, cur_dir)


def restore(root, out):
    run = reference_run(out)
    target = load(run)
    if target is None:
        return None
    now = _current(root, out)
    aside = os.path.join(_store(out), "aside", time.strftime("%Y%m%d-%H%M%S"))
    moved = sorted(set(now) - set(target))
    for rel in moved:
        dst = os.path.join(aside, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(os.path.join(root, rel), dst)
    restored = sorted(rel for rel, sha in target.items() if now.get(rel) != sha)
    for rel in restored:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        tmp = dst + ".tl_perf_tmp"
        shutil.copyfile(os.path.join(_store(out), "objects", target[rel]), tmp)
        if os.path.exists(dst):
            shutil.copymode(dst, tmp)
        os.replace(tmp, dst)
    return {"run": run, "restored": restored, "moved_aside": moved, "aside_dir": aside if moved else None}
