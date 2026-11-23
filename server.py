import json, os, re, socket, subprocess, tempfile, threading, time, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
HFILE = os.path.join(BASE, "history.json")
PAGE = 24
lock = threading.Lock()
cache, meta_cache, ccache, chats = {}, {}, {}, {}
UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
CH_RE = re.compile(r"(UC[A-Za-z0-9_-]{22}|@[^\s/?#&\"'<>]{2,80})")
KINDS = {"video", "live", "shorts", "chan_videos", "chan_shorts", "chan_streams"}


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

def num(n):
    n = int(n)
    if n >= 1_000_000_000: return f"{n/1e9:.1f} M"
    if n >= 1_000_000: return f"{n/1e6:.1f} jt"
    if n >= 1_000: return f"{n/1e3:.0f} rb"
    return str(n)

def fmt_views(n, live=False):
    if n is None: return ""
    return f"{num(n)} " + ("menonton" if live else "x ditonton")

def fmt_subs(n):
    return "" if n is None else f"{num(n)} subscriber"

def run_yt(args, timeout=120):
    p = subprocess.run(["yt-dlp", "--no-warnings", "--ignore-no-formats-error"] + args,
                       capture_output=True, text=True, timeout=timeout)
    if not p.stdout.strip():
        raise RuntimeError((p.stderr or "yt-dlp gagal").strip()[-300:])
    return json.loads(p.stdout)

def item(e, dname="", dcid="", live_default=False):
    vid = e.get("id") or ""
    if len(vid) != 11: return None
    dur = e.get("duration")
    live = e.get("live_status") == "is_live" or bool(e.get("is_live")) or (live_default and not dur)
    uid = e.get("uploader_id") or ""
    return {"id": vid, "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or dname,
            "cid": e.get("channel_id") or dcid or (uid if uid.startswith("@") else ""),
            "views": fmt_views(e.get("view_count"), live),
            "dur": "" if live else fmt_dur(dur), "live": live,
            "secs": int(dur) if dur else 0,
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}

def build(kind, ref, need):
    meta = {}
    if kind == "video":
        d = run_yt(["--flat-playlist", "-J", f"ytsearch{need}:{ref}"])
        items = [item(e) for e in d.get("entries") or [] if e]
    elif kind == "live":
        url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(ref) + "&sp=EgJAAQ%3D%3D"
        d = run_yt(["--flat-playlist", "-J", "--playlist-end", str(need), url])
        items = [item(e, live_default=True) for e in d.get("entries") or [] if e]
    elif kind == "shorts":
        d = run_yt(["--flat-playlist", "-J", f"ytsearch{min(need * 4, 150)}:{ref} #shorts"])
        items = [item(e) for e in d.get("entries") or [] if e]
        items = [i for i in items if i and 0 < i["secs"] <= 61]
    else:
        tab = {"chan_videos": "videos", "chan_shorts": "shorts", "chan_streams": "streams"}[kind]
        base = f"https://www.youtube.com/channel/{ref}" if ref.startswith("UC") else f"https://www.youtube.com/{ref}"
        d = run_yt(["--flat-playlist", "-J", "--playlist-end", str(need), f"{base}/{tab}"])
        name = d.get("channel") or d.get("uploader") or ""
        cid = d.get("channel_id") or (ref if ref.startswith("UC") else "")
        items = [item(e, name, cid) for e in d.get("entries") or [] if e]
        avatar = ""
        for t in d.get("thumbnails") or []:
            if "avatar" in str(t.get("id", "")): avatar = t.get("url", "")
        meta = {"name": name, "subs": fmt_subs(d.get("channel_follower_count")), "avatar": avatar, "cid": cid}
    return [i for i in items if i], meta

def listing(kind, ref, page):
    page = max(1, min(page, 12))
    need = page * PAGE
    key = (kind, ref)
    with lock:
        items = cache.get(key)
        meta = meta_cache.get(key, {})
    if items is None or len(items) < need:
        items, meta = build(kind, ref, need)
        with lock:
            if len(cache) > 80:
                cache.clear(); meta_cache.clear()
            cache[key] = items
            meta_cache[key] = meta
    chunk = items[(page - 1) * PAGE:need]
    return {"items": chunk, "more": len(chunk) == PAGE and page < 12, "meta": meta}

def yt_comments(vid, n):
    if not ID_RE.fullmatch(vid or ""): raise ValueError("id video tidak valid")
    n = max(10, min(int(n), 100))
    key = (vid, n)
    with lock: hit = ccache.get(key)
    if hit is not None: return hit
    p = subprocess.run(
        ["yt-dlp", "--skip-download", "--write-comments", "--ignore-no-formats-error",
         "--no-playlist", "--no-warnings", "--dump-single-json",
         "--extractor-args", f"youtube:max_comments={n},{n},0,0;comment_sort=top",
         f"https://www.youtube.com/watch?v={vid}"],
        capture_output=True, text=True, timeout=180)
    if not p.stdout.strip():
        raise RuntimeError((p.stderr or "yt-dlp gagal").strip()[-300:])
    d = json.loads(p.stdout)
    out = []
    for c in d.get("comments") or []:
        if c.get("parent") not in (None, "root"): continue
        out.append({"author": c.get("author") or "", "text": c.get("text") or "",
                    "likes": c.get("like_count") or 0, "time": c.get("_time_text") or "",
                    "pinned": bool(c.get("is_pinned")), "avatar": c.get("author_thumbnail") or ""})
    res = {"items": out}
    with lock:
        if len(ccache) > 60: ccache.clear()
        ccache[key] = res
    return res

def http(url, data=None, hdrs=None):
    h = {"User-Agent": UA, "Accept-Language": "id-ID,id;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
    if hdrs: h.update(hdrs)
    req = urllib.request.Request(url, data=data, headers=h)
    return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")

def find_key(o, name):
    if isinstance(o, dict):
        if name in o: return o[name]
        for v in o.values():
            r = find_key(v, name)
            if r is not None: return r
    elif isinstance(o, list):
        for v in o:
            r = find_key(v, name)
            if r is not None: return r
    return None

def yt_info(vid):
    if not ID_RE.fullmatch(vid or ""): raise ValueError("id video tidak valid")
    u = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(f"https://www.youtube.com/watch?v={vid}", safe="")
    d = json.loads(http(u))
    m = re.search(r"/(@[^/?#]+|channel/(UC[A-Za-z0-9_-]{22}))", d.get("author_url") or "")
    cid = (m.group(2) or m.group(1)) if m else ""
    return {"id": vid, "title": d.get("title") or "", "channel": d.get("author_name") or "", "cid": cid,
            "views": "", "dur": "", "live": False, "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"}

class Chat:
    def __init__(self, vid):
        self.vid = vid; self.lock = threading.Lock()
        self.buf = []; self.seq = 0; self.last = 0.0; self.wait = 2.0
        self.used = time.time(); self.seen = set()
        html = http(f"https://www.youtube.com/watch?v={vid}")
        k = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
        v = re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html)
        m = re.search(r"ytInitialData\s*=\s*", html)
        if not (k and m): raise RuntimeError("halaman video tidak terbaca")
        data, _ = json.JSONDecoder().raw_decode(html[m.end():])
        r = find_key(data, "liveChatRenderer")
        if not r: raise RuntimeError("live chat tidak tersedia")
        if r.get("isReplay"): raise RuntimeError("siaran ini bukan live lagi")
        c = r["continuations"][0]
        x = c.get("reloadContinuationData") or c.get("invalidationContinuationData") or c.get("timedContinuationData")
        self.cont = x["continuation"]
        self.key = k.group(1)
        self.ver = v.group(1) if v else "2.20240101.00.00"

    @staticmethod
    def _run(x):
        if "text" in x: return x["text"]
        e = x.get("emoji") or {}
        eid = e.get("emojiId") or ""
        if eid and "/" not in eid: return eid
        sc = e.get("shortcuts") or []
        return sc[0] if sc else ""

    def refresh(self):
        with self.lock:
            self.used = time.time()
            if time.time() - self.last < self.wait: return
            body = json.dumps({"context": {"client": {"clientName": "WEB", "clientVersion": self.ver, "hl": "id", "gl": "ID"}},
                               "continuation": self.cont}).encode()
            txt = http(f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={self.key}",
                       body, {"Content-Type": "application/json"})
            self.last = time.time()
            lc = (json.loads(txt).get("continuationContents") or {}).get("liveChatContinuation") or {}
            for a in lc.get("actions") or []:
                it = (a.get("addChatItemAction") or {}).get("item") or {}
                r = it.get("liveChatTextMessageRenderer") or it.get("liveChatPaidMessageRenderer")
                if not r or r.get("id") in self.seen: continue
                self.seen.add(r.get("id"))
                msg = "".join(self._run(x) for x in (r.get("message") or {}).get("runs", []))
                self.seq += 1
                self.buf.append({"seq": self.seq,
                                 "author": (r.get("authorName") or {}).get("simpleText", ""),
                                 "text": msg,
                                 "paid": (r.get("purchaseAmountText") or {}).get("simpleText", "")})
            self.buf = self.buf[-150:]
            if len(self.seen) > 3000: self.seen.clear()
            for c in lc.get("continuations") or []:
                x = c.get("timedContinuationData") or c.get("invalidationContinuationData") or c.get("reloadContinuationData")
                if x and x.get("continuation"):
                    self.cont = x["continuation"]
                    self.wait = max(1.5, min(5.0, (x.get("timeoutMs") or 2000) / 1000))
                    break

def chat_poll(vid, after):
    if not ID_RE.fullmatch(vid or ""): raise ValueError("id video tidak valid")
    now = time.time()
    with lock:
        for k in [k for k, c in chats.items() if now - c.used > 180]: chats.pop(k, None)
        c = chats.get(vid)
    if c is None:
        c = Chat(vid)
        with lock: chats[vid] = c
    c.refresh()
    with c.lock:
        items = c.buf[-4:] if after < 0 else [m for m in c.buf if m["seq"] > after][-40:]
        return {"items": items, "seq": c.seq}


DLDIR = os.path.join(tempfile.gettempdir(), "ytlite-dl")
os.makedirs(DLDIR, exist_ok=True)
for _f in os.listdir(DLDIR):
    try: os.remove(os.path.join(DLDIR, _f))
    except OSError: pass
dl_jobs = {}
dl_sem = threading.Semaphore(2)
HEIGHTS = {"360": 360, "480": 480, "720": 720}

def dl_key(vid, q):
    if not ID_RE.fullmatch(vid or ""): raise ValueError("id video tidak valid")
    return (vid, q if q in HEIGHTS else "720")

def dl_get(vid, q):
    key = dl_key(vid, q)
    with lock: return dl_jobs.get(key)

def dl_clean(j):
    pre = f"{j['vid']}_{j['q']}."
    for f in os.listdir(DLDIR):
        if f.startswith(pre):
            try: os.remove(os.path.join(DLDIR, f))
            except OSError: pass

def dl_gc():
    now = time.time()
    with lock:
        dead = [k for k, j in dl_jobs.items() if j["state"] not in ("queued", "working") and now - j["ts"] > 1800]
        gone = [dl_jobs.pop(k) for k in dead]
    for j in gone: dl_clean(j)

def dl_run(j):
    with dl_sem:
        if j["cancel"]:
            j["state"] = "error"; j["err"] = "dibatalkan"; return
        j["state"] = "working"
        h = HEIGHTS[j["q"]]
        two = h > 360
        if two:
            fmt = f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/b[height<={h}][ext=mp4]/b[height<={h}]"
        else:
            fmt = f"b[height<={h}][ext=mp4]/bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/b[height<={h}]"
        out = os.path.join(DLDIR, f"{j['vid']}_{j['q']}.%(ext)s")
        plain = ["yt-dlp", "--no-playlist", "--newline", "--no-warnings", "-f", fmt,
                 "--merge-output-format", "mp4", "--remux-video", "mp4", "-o", out,
                 f"https://www.youtube.com/watch?v={j['vid']}"]
        withjs = plain[:1] + ["--js-runtimes", "node", "--remote-components", "ejs:github"] + plain[1:]
        tail = ""; rc = 1
        for args in (withjs, plain):
            stage = 0; tail = ""
            p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            j["proc"] = p
            for line in p.stdout:
                tail = (tail + line)[-800:]
                if "[download] Destination:" in line: stage += 1
                m = re.search(r"\[download\]\s+([\d.]+)%", line)
                if m:
                    pc = float(m.group(1))
                    j["pct"] = int(min(99, ((max(stage, 1) - 1) * 100 + pc) / 2)) if two else int(min(99, pc))
            p.wait(); rc = p.returncode
            if j["cancel"] or rc == 0: break
            if not re.search(r"no such option|unrecognized arguments|invalid choice|unknown option", tail, re.I): break
        if j["cancel"]:
            j["state"] = "error"; j["err"] = "dibatalkan"; dl_clean(j); return
        path = os.path.join(DLDIR, f"{j['vid']}_{j['q']}.mp4")
        if rc != 0 or not os.path.exists(path):
            lines = [x for x in tail.strip().splitlines() if x.strip()]
            j["state"] = "error"; j["err"] = (lines[-1] if lines else "download gagal")[-220:]
            dl_clean(j); return
        j["path"] = path; j["size"] = os.path.getsize(path); j["pct"] = 100; j["state"] = "ready"

def dl_start(vid, q):
    key = dl_key(vid, q)
    dl_gc()
    with lock:
        j = dl_jobs.get(key)
        if j and j["state"] in ("queued", "working", "ready"):
            j["ts"] = time.time()
            return {"ok": True}
        j = {"vid": key[0], "q": key[1], "state": "queued", "pct": 0, "size": 0, "path": "",
             "err": "", "proc": None, "cancel": False, "ts": time.time()}
        dl_jobs[key] = j
    threading.Thread(target=dl_run, args=(j,), daemon=True).start()
    return {"ok": True}

def dl_status(vid, q):
    j = dl_get(vid, q)
    if not j: return {"state": "none"}
    j["ts"] = time.time()
    return {"state": j["state"], "pct": j["pct"], "size": j["size"], "error": j["err"]}

def dl_cancel(vid, q):
    key = dl_key(vid, q)
    with lock: j = dl_jobs.pop(key, None)
    if j:
        j["cancel"] = True
        p = j.get("proc")
        if p and p.poll() is None:
            try: p.kill()
            except Exception: pass
        dl_clean(j)
    return {"ok": True}

def dl_done(vid, q):
    key = dl_key(vid, q)
    with lock: j = dl_jobs.pop(key, None)
    if j: dl_clean(j)
    return {"ok": True}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def js(self, obj):
        self.send(200, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode())
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": qs.get(k, [d])[0]
        try:
            if u.path == "/api/list":
                kind, ref = g("kind", "video"), g("ref").strip()[:200]
                if kind not in KINDS: raise ValueError("kind salah")
                if kind.startswith("chan_"):
                    if not CH_RE.fullmatch(ref): raise ValueError("channel tidak valid")
                elif not ref:
                    ref = "live indonesia" if kind == "live" else "populer indonesia"
                self.js(listing(kind, ref, int(g("page", "1"))))
            elif u.path == "/api/comments":
                self.js(yt_comments(g("id"), g("n", "20")))
            elif u.path == "/api/chat":
                self.js(chat_poll(g("id"), int(g("after", "-1"))))
            elif u.path == "/api/info":
                self.js(yt_info(g("id")))
            elif u.path == "/api/dl/start":
                self.js(dl_start(g("id"), g("q", "720")))
            elif u.path == "/api/dl/status":
                self.js(dl_status(g("id"), g("q", "720")))
            elif u.path == "/api/dl/file":
                self.dl_file(g("id"), g("q", "720"))
            elif u.path == "/api/dl/cancel":
                self.js(dl_cancel(g("id"), g("q", "720")))
            elif u.path == "/api/dl/done":
                self.js(dl_done(g("id"), g("q", "720")))
            elif u.path == "/api/history":
                self.js(load_h())
            else:
                with open(os.path.join(BASE, "index.html"), "rb") as f:
                    self.send(200, "text/html; charset=utf-8", f.read())
        except Exception as e:
            print("ERROR:", e)
            self.js({"error": str(e)[-300:]})
    def dl_file(self, vid, q):
        j = dl_get(vid, q)
        if not j or j["state"] != "ready" or not os.path.exists(j["path"]):
            self.js({"error": "file belum siap"}); return
        size = os.path.getsize(j["path"])
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            with open(j["path"], "rb") as f:
                while True:
                    b = f.read(65536)
                    if not b: break
                    self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            d = json.loads(self.rfile.read(n) or b"{}")
            h = load_h(); t = d.get("type")
            if t == "watch" and d.get("video", {}).get("id"):
                v = d["video"]
                h["watch"] = ([v] + [x for x in h["watch"] if x.get("id") != v["id"]])[:200]
            elif t == "search" and d.get("q"):
                q = d["q"].strip()
                h["search"] = ([q] + [x for x in h["search"] if x.lower() != q.lower()])[:50]
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
print(f"\nYouTube Lite jalan!\n  HP ini : http://localhost:{PORT}\n  WiFi sama: http://{ip()}:{PORT}\n  Stop: Ctrl+C\n")
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
