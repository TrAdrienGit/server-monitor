#!/usr/bin/env python3
"""Supervision légère du serveur (lecture seule), accessible uniquement via Tailscale.

Dépendance unique : psutil. Tout le reste est la bibliothèque standard.
"""
import configparser
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

HERE = Path(__file__).resolve().parent
# Réglages : config.ini à côté du script (voir config.example.ini), surchargeables par variables d'environnement.
_cfg = configparser.ConfigParser()
_cfg.read(HERE / "config.ini", encoding="utf-8")


def setting(key, default):
    return os.environ.get("MONITOR_" + key.upper()) or _cfg.get("monitor", key, fallback="") or default


HOST = setting("host", "")
PORT = int(setting("port", "9110"))
DB_PATH = Path(setting("db", "/var/lib/server-monitor/history.db"))
SAMPLE_SECONDS = int(setting("sample_seconds", "60"))
RETENTION_DAYS = int(setting("retention_days", "30"))
ALLOWED_NETS = [ipaddress.ip_network(n) for n in
                ("100.64.0.0/10", "fd7a:115c:a1e0::/48", "127.0.0.0/8", "::1/128")]


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def tailscale_ip():
    if shutil.which("tailscale"):
        ip = run(["tailscale", "ip", "-4"]).strip().splitlines()
        if ip:
            return ip[0]
    return None


# ---------- Mesures instantanées ----------

def read_sysfs(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def battery():
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        def val(name):
            v = read_sysfs(bat / name)
            return int(v) if v and v.lstrip("-").isdigit() else None
        watts = None
        if val("power_now") is not None:
            watts = val("power_now") / 1e6
        elif val("current_now") is not None and val("voltage_now") is not None:
            watts = val("current_now") * val("voltage_now") / 1e12
        full, design = val("charge_full") or val("energy_full"), val("charge_full_design") or val("energy_full_design")
        ac = read_sysfs("/sys/class/power_supply/ADP1/online") or read_sysfs("/sys/class/power_supply/AC/online")
        return {
            "percent": val("capacity"),
            "status": read_sysfs(bat / "status"),
            "watts": round(abs(watts), 2) if watts is not None else None,
            "health_percent": round(100 * full / design, 1) if full and design else None,
            "ac_online": ac == "1" if ac is not None else None,
        }
    b = psutil.sensors_battery()
    if b:
        return {"percent": round(b.percent), "status": "Charging" if b.power_plugged else "Discharging",
                "watts": None, "health_percent": None, "ac_online": b.power_plugged}
    return None


def gpus():
    if not shutil.which("nvidia-smi"):
        return []
    out = run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,clocks_throttle_reasons.active",
               "--format=csv,noheader,nounits"])
    result = []
    for line in out.strip().splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 7:
            continue
        num = lambda s: float(s) if re.fullmatch(r"[\d.]+", s) else None
        result.append({"name": p[0], "mem_used_mb": num(p[1]), "mem_total_mb": num(p[2]),
                       "util_percent": num(p[3]), "temp_c": num(p[4]), "power_w": num(p[5]),
                       "throttle": p[6]})
    return result


def temperatures():
    temps = []
    for chip, entries in psutil.sensors_temperatures().items():
        for e in entries:
            if e.current:
                temps.append({"chip": chip, "label": e.label or chip, "c": e.current,
                              "high": e.high, "critical": e.critical})
    return temps


def processes(limit=60):
    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info", "memory_percent", "cmdline"]):
        i = p.info
        procs.append({"pid": i["pid"], "name": i["name"], "user": i["username"],
                      "cpu": round(i["cpu_percent"] or 0, 1),
                      "rss_mb": round((i["memory_info"].rss if i["memory_info"] else 0) / 2**20, 1),
                      "mem": round(i["memory_percent"] or 0, 1),
                      "cmd": " ".join(i["cmdline"] or [])[:200]})
    procs.sort(key=lambda x: (x["cpu"], x["rss_mb"]), reverse=True)
    return procs[:limit]


def stats():
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    disk = psutil.disk_usage("/")
    return {
        "time": time.time(),
        "hostname": os.uname().nodename,
        "uptime_s": time.time() - psutil.boot_time(),
        "load": os.getloadavg(),
        "cpu": {"percent": psutil.cpu_percent(), "per_core": psutil.cpu_percent(percpu=True),
                "freq_mhz": round(psutil.cpu_freq().current) if psutil.cpu_freq() else None},
        "ram": {"used": vm.total - vm.available, "total": vm.total, "percent": vm.percent},
        "swap": {"used": sw.used, "total": sw.total},
        "disk": {"used": disk.used, "total": disk.total},
        "gpus": gpus(),
        "temps": temperatures(),
        "battery": battery(),
        "processes": processes(),
    }


# ---------- Erreurs matérielles / stabilité (lentes : mises en cache) ----------

_errors_cache = {"at": 0, "data": None}


def smart():
    disks = []
    if not shutil.which("smartctl"):
        return {"available": False, "disks": []}
    scan = run(["smartctl", "--scan", "-j"])
    try:
        devices = [d["name"] for d in json.loads(scan).get("devices", [])]
    except ValueError:
        devices = []
    for dev in devices:
        try:
            j = json.loads(run(["smartctl", "-H", "-A", "-l", "error", "-j", dev]))
        except ValueError:
            continue
        nv = j.get("nvme_smart_health_information_log", {})
        disks.append({"device": dev, "model": j.get("model_name"),
                      "passed": j.get("smart_status", {}).get("passed"),
                      "temp_c": j.get("temperature", {}).get("current"),
                      "media_errors": nv.get("media_errors"),
                      "error_log_entries": nv.get("num_err_log_entries"),
                      "percent_used": nv.get("percentage_used"),
                      "critical_warning": nv.get("critical_warning")})
    return {"available": True, "disks": disks}


def journal(args, limit):
    out = run(["journalctl", "--no-pager", "-o", "short-iso", "-n", str(limit)] + args, timeout=20)
    return [l for l in out.splitlines() if l and not l.startswith("-- ")]


def errors():
    if time.time() - _errors_cache["at"] < 60 and _errors_cache["data"]:
        return _errors_cache["data"]
    kernel_err = journal(["-k", "-p", "err", "--since", "-7d"], 80)
    edac = {}
    for mc in Path("/sys/devices/system/edac/mc").glob("mc*"):
        edac[mc.name] = {"ce": read_sysfs(mc / "ce_count"), "ue": read_sysfs(mc / "ue_count")}
    data = {
        "kernel_errors": kernel_err,
        "system_errors": journal(["-p", "err", "--since", "-24h"], 80),
        "mce": [l for l in journal(["-k", "--since", "-30d", "--grep", "mce|Machine check|Hardware Error"], 40)],
        "edac": edac,
        "oom": journal(["-k", "--since", "-30d", "--grep", "Out of memory|oom-kill|Killed process"], 40),
        "throttling": journal(["-k", "--since", "-7d", "--grep", "throttl|temperature above threshold"], 40),
        "smart": smart(),
        "boots": run(["journalctl", "--list-boots", "--no-pager"]).splitlines()[-10:],
    }
    _errors_cache.update(at=time.time(), data=data)
    return data


# ---------- Historique batterie (SQLite) ----------

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS battery (ts INTEGER PRIMARY KEY, percent REAL, watts REAL, status TEXT)")
    return conn


def sampler():
    psutil.cpu_percent()  # amorce la mesure CPU
    while True:
        b = battery()
        if b and b["percent"] is not None:
            try:
                with db() as conn:
                    conn.execute("INSERT OR REPLACE INTO battery VALUES (?,?,?,?)",
                                 (int(time.time()), b["percent"], b["watts"], b["status"]))
                    conn.execute("DELETE FROM battery WHERE ts < ?", (int(time.time()) - RETENTION_DAYS * 86400,))
            except sqlite3.Error as e:
                print("battery history:", e, flush=True)
        time.sleep(SAMPLE_SECONDS)


def battery_history(hours):
    with db() as conn:
        rows = conn.execute("SELECT ts, percent, watts, status FROM battery WHERE ts >= ? ORDER BY ts",
                            (int(time.time() - hours * 3600),)).fetchall()
    return [{"ts": r[0], "percent": r[1], "watts": r[2], "status": r[3]} for r in rows]


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        ip = ipaddress.ip_address(self.client_address[0].removeprefix("::ffff:"))
        if not any(ip in n for n in ALLOWED_NETS):
            return self.send(403, {"error": "forbidden"})
        path, _, query = self.path.partition("?")
        if path == "/":
            return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/favicon.svg", "/favicon.ico"):
            return self.send(200, (HERE / "favicon.svg").read_bytes(), "image/svg+xml")
        if path == "/api/stats":
            return self.send(200, stats())
        if path == "/api/errors":
            return self.send(200, errors())
        if path == "/api/battery-history":
            m = re.search(r"hours=(\d+)", query)
            return self.send(200, battery_history(min(int(m.group(1)) if m else 24, 24 * RETENTION_DAYS)))
        self.send(404, {"error": "not found"})


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    host = HOST or tailscale_ip()
    if not host:
        raise SystemExit("IP Tailscale introuvable (renseigner host dans config.ini) ; systemd réessaiera")
    threading.Thread(target=sampler, daemon=True).start()
    print(f"server-monitor sur http://{host}:{PORT}", flush=True)
    ThreadingHTTPServer((host, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
