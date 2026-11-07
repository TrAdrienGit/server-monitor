"""Outil de transcription manuel : liste les audios et vidéos du cloud, ne transcrit que ce qui est choisi."""
import base64
import gc
import http.client
import json
import os
import queue
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from faster_whisper import WhisperModel

NC_HOST = "app"
NC_VHOST = "server-linux.tail192969.ts.net:8443"  # seul nom accepté par Nextcloud
NC_USER = "adrien"
PASSWORD = Path("/run/secrets/nextcloud").read_text().strip()
DAV_ROOT = f"/remote.php/dav/files/{NC_USER}/"
MODEL = os.environ.get("TRANSCRIBE_MODEL", "large-v3-turbo")
THREADS = int(os.environ.get("TRANSCRIBE_THREADS", "4"))
LANGUAGE = os.environ.get("TRANSCRIBE_LANGUAGE", "fr")
EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".oga", ".opus", ".flac", ".amr", ".wma", ".3ga",
              ".mp4", ".mkv", ".mov", ".avi", ".webm", ".3gp", ".m4v"}
TRANSCRIPT_FOLDER = "Transcript"
STATE = Path("/state")
WORK = Path("/work")

jobs = queue.Queue()
lock = threading.Lock()
queued = []  # fichiers choisis, pas encore commencés
status = {"phase": "attente", "current": None, "progress": None, "pending": 0, "done": 0, "failed": 0,
          "speed": None, "last_done": None, "last_error": None, "model": MODEL, "finished": []}


def publish(**changes):
    with lock:
        status.update(changes, updated_at=time.time(), pending=len(queued), queued=list(queued))
        tmp = STATE / "status.tmp"
        tmp.write_text(json.dumps(status))
        tmp.replace(STATE / "status.json")


def request(method, path, body=None, headers=None, sink=None):
    conn = http.client.HTTPConnection(NC_HOST, 80, timeout=300)
    token = base64.b64encode(f"{NC_USER}:{PASSWORD}".encode()).decode()
    try:
        conn.request(method, path, body=body, headers={"Host": NC_VHOST, "Authorization": "Basic " + token, **(headers or {})})
        response = conn.getresponse()
        if response.status >= 400:
            raise OSError(f"{method} HTTP {response.status}")
        if sink:
            while chunk := response.read(1 << 20):
                sink.write(chunk)
            return b""
        return response.read()
    finally:
        conn.close()


def list_cloud():
    """Tous les audios et vidéos du cloud, avec leur taille et l'existence d'un .txt du même nom."""
    body = request("PROPFIND", DAV_ROOT, headers={"Depth": "infinity"})
    ns = {"d": "DAV:"}
    files = {}
    for item in ET.fromstring(body).findall("d:response", ns):
        href = unquote(item.findtext("d:href", "", ns))
        if href.startswith(DAV_ROOT) and not href.endswith("/"):
            modified = item.findtext(".//d:getlastmodified", "", ns)
            files[href[len(DAV_ROOT):]] = (int(item.findtext(".//d:getcontentlength", "0", ns) or 0),
                                           parsedate_to_datetime(modified).timestamp() if modified else None)
    media = []
    for path, (size, modified) in sorted(files.items(), key=lambda kv: kv[0].lower()):
        ext = os.path.splitext(path)[1]
        if ext.lower() in EXTENSIONS:
            media.append({"path": path, "size": size, "modified": modified, "transcribed": transcript_path(path) in files})
    return media


def transcript_path(path):
    """Vocaux/a.m4a -> Transcript/Vocaux/a.txt : même arborescence que le fichier d'origine."""
    return TRANSCRIPT_FOLDER + "/" + os.path.splitext(path)[0] + ".txt"


def make_folders(path):
    parts = path.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        try:
            request("MKCOL", DAV_ROOT + quote("/".join(parts[:i])) + "/")
        except OSError as e:
            if "HTTP 405" not in str(e):  # 405 : le dossier existe déjà
                raise


def stamp(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(model, path):
    local = WORK / "entree"
    with open(local, "wb") as f:
        request("GET", DAV_ROOT + quote(path), sink=f)
    start = time.time()
    segments, info = model.transcribe(str(local), language=LANGUAGE or None, vad_filter=True,
                                      condition_on_previous_text=False)
    lines = []
    for seg in segments:
        lines.append(f"[{stamp(seg.start)}] {seg.text.strip()}")
        if info.duration:
            publish(progress=min(1.0, seg.end / info.duration))
    local.unlink()
    target = transcript_path(path)
    make_folders(target)
    request("PUT", DAV_ROOT + quote(target), body=("\n".join(lines) + "\n").encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"})
    elapsed = time.time() - start
    return round(info.duration / elapsed, 2) if elapsed > 0 else None


def worker():
    model = None
    while True:
        path = jobs.get()
        with lock:
            queued.remove(path)
        try:
            if model is None:
                publish(phase="chargement", current=path, progress=0)
                model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS, download_root="/models")
            publish(phase="transcription", current=path, progress=0)
            speed = transcribe(model, path)
            publish(done=status["done"] + 1, speed=speed, last_done=path,
                    finished=(status["finished"] + [{"path": path, "ok": True}])[-20:])
        except Exception as e:  # un fichier illisible ne doit pas bloquer les suivants
            traceback.print_exc()
            publish(failed=status["failed"] + 1, last_error=f"{os.path.basename(path)} : {e}",
                    finished=(status["finished"] + [{"path": path, "ok": False}])[-20:])
        if jobs.empty():
            model = None  # rien à faire : la mémoire est rendue
            gc.collect()
            publish(phase="attente", current=None, progress=None)


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Transcription</title><style>
:root{color-scheme:light dark;--bg:#14181f;--card:#1c222c;--text:#e6e9ef;--mut:#9aa4b2;--acc:#4f9dff;--line:#2c3440}
body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}main{max-width:820px;margin:auto;padding:16px}
h1{font-size:20px;margin:0 0 12px}.card{background:var(--card);border-radius:10px;padding:14px;margin-bottom:12px}
.mut{color:var(--mut);font-size:13px}.bar{height:8px;background:var(--line);border-radius:4px;margin:8px 0}.bar div{height:100%;background:var(--acc);border-radius:4px;width:0}
input[type=search]{width:100%;box-sizing:border-box;padding:8px;background:var(--bg);color:var(--text);border:1px solid var(--line);border-radius:6px}
label{display:flex;gap:8px;padding:8px 2px;border-bottom:1px solid var(--line);align-items:flex-start}label span{overflow-wrap:anywhere;flex:1}
button{padding:8px 14px;background:var(--acc);color:#fff;border:0;border-radius:6px;font-size:15px}button:disabled{opacity:.5}
.bar-top{display:flex;gap:10px;align-items:center;margin:10px 0}.badge{color:#5fd38d;font-size:12px;white-space:nowrap}
</style></head><body><main><h1>Transcription</h1>
<div class="card"><div id="st">Chargement…</div><div class="bar"><div id="pb"></div></div><div class="mut" id="meta"></div></div>
<div class="card"><input type="search" id="q" placeholder="Filtrer par nom ou dossier"><div class="bar-top"><button id="go" disabled>Transcrire la sélection</button><span class="mut" id="sel"></span></div><div id="list" class="mut">Lecture du cloud…</div></div>
<script>
const $=i=>document.getElementById(i);let files=[],chosen=new Set(),busy=new Set();
const mo=b=>(b/1048576).toFixed(1)+" Mo";
function draw(){const f=$("q").value.toLowerCase(),l=$("list");l.textContent="";l.className="";
 const shown=files.filter(x=>x.path.toLowerCase().includes(f));
 if(!shown.length){l.textContent="Aucun fichier audio ou vidéo.";l.className="mut"}
 for(const x of shown){const r=document.createElement("label"),c=document.createElement("input"),t=document.createElement("span"),m=document.createElement("div");
  c.type="checkbox";c.checked=chosen.has(x.path);c.disabled=busy.has(x.path);
  c.onchange=()=>{c.checked?chosen.add(x.path):chosen.delete(x.path);count()};t.textContent=x.path;m.className="mut";m.textContent=mo(x.size)+(x.modified?" · "+new Date(x.modified*1000).toLocaleString("fr-FR",{dateStyle:"short",timeStyle:"short"}):"");t.append(m);r.append(c,t);
  if(busy.has(x.path)||x.transcribed){const b=document.createElement("span");b.className="badge";b.style.flex="none";b.textContent=busy.has(x.path)?"en file":"déjà transcrit";r.append(b)}
  l.append(r)}count()}
function count(){$("sel").textContent=chosen.size?chosen.size+" choisi(s)":"";$("go").disabled=!chosen.size}
async function load(){try{const r=await fetch("api/files");if(!r.ok)throw 0;files=await r.json();draw()}catch{$("list").textContent="Cloud injoignable : vérifie qu'il est allumé, puis recharge la page."}}
const PH={attente:"En attente de ton choix",chargement:"Chargement du modèle…",transcription:"Transcription en cours"};
async function poll(){const s=await (await fetch("api/status")).json();busy=new Set([...(s.queued||[]),...(s.current?[s.current]:[])]);
 $("st").textContent=(PH[s.phase]||s.phase)+(s.current?" : "+s.current.split("/").pop():"");$("pb").style.width=(s.progress||0)*100+"%";
 $("meta").textContent=[s.current&&s.progress!=null?((s.progress*100).toFixed(0)+" %"):"",s.pending+" en file",s.done+" terminé(s)",s.speed?"vitesse ×"+s.speed:"",s.failed?s.failed+" en échec : "+s.last_error:""].filter(Boolean).join(" · ");
 const was=poll.done;poll.done=s.done+s.failed;if(was!==undefined&&was!==poll.done)load();}
$("q").oninput=draw;
$("go").onclick=async()=>{const paths=[...chosen],done=paths.filter(p=>files.find(x=>x.path===p)?.transcribed);
 if(done.length&&!confirm(done.length+" fichier(s) ont déjà un transcript, qui sera remplacé. Continuer ?"))return;
 const r=await fetch("api/enqueue",{method:"POST",headers:{"X-Transcribe":"1","Content-Type":"application/json"},body:JSON.stringify({paths})});
 if(r.ok){chosen.clear();await poll();draw()}else alert("Refusé.")};
load();poll();setInterval(()=>poll().catch(()=>{}),3000);
</script></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/status":
            with lock:
                return self.send(200, status)
        if self.path == "/api/files":
            try:
                return self.send(200, list_cloud())
            except (OSError, ET.ParseError, http.client.HTTPException) as e:
                return self.send(502, {"error": str(e)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        # En-tête personnalisé : une page tierce ne peut pas l'envoyer sans préalable CORS.
        if self.path != "/api/enqueue" or self.headers.get("X-Transcribe") != "1":
            return self.send(400, {"error": "requete refusee"})
        try:
            paths = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["paths"]
            known = {f["path"] for f in list_cloud()}
        except (ValueError, KeyError, OSError, ET.ParseError, http.client.HTTPException) as e:
            return self.send(400, {"error": str(e)})
        with lock:
            active = set(queued) | {status["current"]}
            added = [p for p in paths if p in known and p not in active]
            queued.extend(added)
        for p in added:
            jobs.put(p)
        publish()
        self.send(200, {"added": len(added)})


if __name__ == "__main__":
    publish()
    threading.Thread(target=worker, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
