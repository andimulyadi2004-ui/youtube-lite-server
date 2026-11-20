import json, os, socket, subprocess, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
HFILE = os.path.join(BASE, "history.json")
PAGE = 24
cache = {}
lock = threading.Lock()

def load_h():
    try:
        with open(HFILE, encoding="utf-8") as f: return json.load(f)
    except Exception:
        return {"watch": [], "search": []}

def save_h(h):
    with open(HFILE, "w", encoding="utf-8") as f: json.dump(h, f, ensure_ascii=False)

def fmt_dur(s):
    if not s: return ""
    s = int(s); h, r = divmod(s, 3600); m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def fmt_views(n):
    if n is None: return ""
    n = int(n)
    if n >= 1_000_000_000: return f"{n/1e9:.1f} M x ditonton"
    if n >= 1_000_000: return f"{n/1e6:.1f} jt x ditonton"
    if n >= 1_000: return f"{n/1e3:.0f} rb x ditonton"
    return f"{n} x ditonton"

def fetch(q, n):
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "-J", "--no-warnings", f"ytsearch{n}:{q}"],
        capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError((p.stderr or "yt-dlp gagal").strip()[-300:])
    out = []
    for e in json.loads(p.stdout).get("entries", []):
        if not e or not e.get("id"): continue
        out.append({
            "id": e["id"], "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or "",
            "views": fmt_views(e.get("view_count")), "time": "",
            "dur": fmt_dur(e.get("duration")),
            "thumb": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"})
    return out

def yt_search(q, page):
    page = max(1, min(page, 12))
    need = page * PAGE
    with lock: items = cache.get(q, [])
    if len(items) < need:
        items = fetch(q, need)
        with lock: cache[q] = items
    chunk = items[(page - 1) * PAGE:need]
    return {"items": chunk, "more": len(chunk) == PAGE and page < 12}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def js(self, obj):
        self.send(200, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode())
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path == "/api/search":
            try:
                self.js(yt_search(qs.get("q", ["populer indonesia"])[0], int(qs.get("page", ["1"])[0])))
            except Exception as e:
                print("ERROR:", e); self.js({"error": str(e)})
        elif u.path == "/api/history":
            self.js(load_h())
        else:
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                self.send(200, "text/html; charset=utf-8", f.read())
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            h = load_h()
            t = d.get("type")
            if t == "watch" and d.get("video", {}).get("id"):
                v = d["video"]
                h["watch"] = [v] + [x for x in h["watch"] if x.get("id") != v["id"]]
                h["watch"] = h["watch"][:200]
            elif t == "search" and d.get("q"):
                q = d["q"].strip()
                h["search"] = [q] + [x for x in h["search"] if x.lower() != q.lower()]
                h["search"] = h["search"][:50]
            elif t == "clear":
                h = {"watch": [], "search": []}
            save_h(h)
            self.js({"ok": True})
        except Exception as e:
            self.js({"error": str(e)})

def ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); r = s.getsockname()[0]; s.close(); return r
    except Exception:
        return "127.0.0.1"

PORT = 8080
print(f"\nYouTube Lite jalan!\n  HP ini : http://localhost:{PORT}\n  WiFi sama: http://{ip()}:{PORT}\n  History: {HFILE}\n  Stop: Ctrl+C\n")
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
