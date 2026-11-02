"""Navigateur de fichiers façon WinDirStat : scan du disque en mémoire, tailles cumulées, treemap.

Un nœud est une liste [nom, taille_disque, nb_fichiers, enfants] ; enfants vaut None pour un fichier.
La taille est l'espace réellement occupé (st_blocks * 512). Le scan traverse les partitions montées
sur /dev/* (pas de /proc, /sys, montages snap…) et ne suit pas les liens symboliques.
"""
import ctypes
import os
import stat
import threading
import time

import psutil

_state = {"root": None, "tree": None, "running": False, "files": 0, "bytes": 0,
          "started": None, "finished": None, "error": None}
_lock = threading.Lock()
_ext_cache = {}

# ---------- Date de création (statx, absent de os.stat sous Linux) ----------

try:
    _libc = ctypes.CDLL(None, use_errno=True)
    _statx = _libc.statx
    _statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
except (OSError, AttributeError):
    _statx = None


def birth_time(path):
    if _statx is None:
        return None
    buf = ctypes.create_string_buffer(256)
    # AT_FDCWD, AT_SYMLINK_NOFOLLOW, STATX_BTIME
    if _statx(-100, os.fsencode(path), 0x100, 0x800, buf) != 0:
        return None
    if not int.from_bytes(buf.raw[0:4], "little") & 0x800:
        return None
    return int.from_bytes(buf.raw[80:88], "little", signed=True)


# ---------- Scan ----------

def _mount_roots():
    """Points de montage à traverser : un par partition /dev/*, le plus court (ignore les montages bind en double)."""
    best = {}
    for p in psutil.disk_partitions(all=False):
        if p.device.startswith("/dev/") and not p.device.startswith("/dev/loop"):
            if p.device not in best or len(p.mountpoint) < len(best[p.device]):
                best[p.device] = p.mountpoint
    return set(best.values())


def _scan_dir(path, name, dev, mounts):
    size, count, children = 0, 0, []
    try:
        it = os.scandir(path)
    except OSError:
        return [name, 0, 0, children]
    with it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev != dev and e.path not in mounts:
                    continue
                child = _scan_dir(e.path, e.name, st.st_dev, mounts)
                child[1] += st.st_blocks * 512
            else:
                child = [e.name, st.st_blocks * 512, 1, None]
                _state["files"] += 1
                _state["bytes"] += child[1]
            children.append(child)
            size += child[1]
            count += child[2]
    children.sort(key=lambda c: c[1], reverse=True)
    return [name, size, count, children]


def _run(root):
    try:
        dev = os.lstat(root).st_dev
        tree = _scan_dir(root, root, dev, _mount_roots())
        with _lock:
            _state.update(tree=tree, root=root, finished=time.time(), error=None)
            _ext_cache.clear()
    except OSError as e:
        _state["error"] = str(e)
    finally:
        _state["running"] = False


def start_scan(root):
    root = os.path.normpath(root or "/")
    if not os.path.isdir(root):
        raise ValueError("dossier introuvable : " + root)
    with _lock:
        if _state["running"]:
            return status()
        _state.update(running=True, files=0, bytes=0, started=time.time(), error=None, scanning=root)
    threading.Thread(target=_run, args=(root,), daemon=True).start()
    return status()


def status():
    return {k: _state.get(k) for k in ("root", "running", "files", "bytes", "started", "finished", "error", "scanning")}


def _find(path):
    """Retourne le nœud correspondant à path dans le dernier scan, ou None."""
    root, node = _state["root"], _state["tree"]
    if node is None:
        return None
    path = os.path.normpath(path)
    if path == root:
        return node
    rel = os.path.relpath(path, root)
    if rel.startswith(".."):
        return None
    for part in rel.split(os.sep):
        node = next((c for c in node[3] or [] if c[0] == part), None)
        if node is None or node[3] is None:
            return node
    return node


def _ext(name):
    base, dot, ext = name.rpartition(".")
    return ext.lower() if dot and base and len(ext) <= 10 else ""


# ---------- API ----------

def listing(path):
    node = _find(path)
    if node is None:
        return None
    if node[3] is None:
        node, path = _find(os.path.dirname(path)), os.path.dirname(path)
    items = []
    for c in node[3][:2000]:
        full = os.path.join(path, c[0])
        try:
            mtime = os.lstat(full).st_mtime
        except OSError:
            mtime = None
        items.append({"name": c[0], "dir": c[3] is not None, "size": c[1], "files": c[2],
                      "created": birth_time(full), "modified": mtime, "ext": "" if c[3] is not None else _ext(c[0])})
    return {"path": path, "size": node[1], "files": node[2], "created": birth_time(path),
            "truncated": len(node[3]) > 2000, "items": items}


def treemap(path, max_nodes=6000):
    """Arbre élagué pour le treemap : on garde les plus gros éléments jusqu'à max_nodes."""
    node = _find(path)
    if node is None:
        return None
    min_size = node[1] / 20000 if node[1] else 0
    budget = [max_nodes]

    def prune(n, depth):
        if n[3] is None:
            return [n[0], n[1], _ext(n[0])]
        kids = []
        for c in n[3]:
            if c[1] < min_size or budget[0] <= 0:
                break
            budget[0] -= 1
            kids.append(prune(c, depth + 1))
        return [n[0], n[1], kids]

    return prune(node, 0)


def extensions(path):
    if path in _ext_cache:
        return _ext_cache[path]
    node = _find(path)
    if node is None:
        return None
    totals = {}
    stack = [node]
    while stack:
        n = stack.pop()
        if n[3] is None:
            e = _ext(n[0])
            t = totals.setdefault(e, [0, 0])
            t[0] += n[1]
            t[1] += 1
        else:
            stack.extend(n[3])
    result = sorted(([e, s, c] for e, (s, c) in totals.items()), key=lambda x: x[1], reverse=True)[:40]
    _ext_cache[path] = result
    return result
