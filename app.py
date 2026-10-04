"""Narrator: a desktop audiobook player that reads ebooks aloud with Kokoro TTS.

Run with Narrator.bat (or: .venv\\Scripts\\python app.py). Use --browser to open in a
normal browser tab instead of an app window.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import types
from collections import OrderedDict
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file

import narrate

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
COVERS = ROOT / ".cache" / "covers"
OUTPUT = ROOT / "output"
STATE_FILE = ROOT / "state.json"
EXTS = {".epub", ".pdf", ".docx", ".txt", ".md"}

for d in (BOOKS, COVERS, OUTPUT):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- persistent state

DEFAULT_SETTINGS = {"voice": "af_heart", "speed": 1.0, "volume": 1.0,
                    "theme": "dark", "fontSize": 20}

_state_lock = threading.Lock()


def load_state() -> dict:
    try:
        s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("books", {})
    s["settings"] = {**DEFAULT_SETTINGS, **{k: v for k, v in s.get("settings", {}).items() if k in DEFAULT_SETTINGS}}
    s.setdefault("last", None)
    return s


STATE = load_state()


def save_state():
    with _state_lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(STATE, indent=1), encoding="utf-8")
        os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- books

_books: dict[str, dict] = {}     # id -> parsed book (chapters split into paragraphs)


def book_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).lower().encode()).hexdigest()[:12]


def split_long(para: str, limit: int = 1100) -> list[str]:
    """Break very long paragraphs (common in PDFs) at sentence ends so navigation stays fine-grained."""
    if len(para) <= limit:
        return [para]
    sentences = [s.strip() for s in re.findall(r"[^.!?…]+(?:[.!?…]+[\"'”’)]*|$)", para) if s.strip()]
    out, cur = [], ""
    for s in sentences:
        if cur and len(cur) + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def metadata(path: Path) -> tuple[str, str]:
    title, author = narrate.book_title(path), ""
    try:
        if path.suffix.lower() == ".epub":
            from ebooklib import epub
            b = epub.read_epub(str(path), options={"ignore_ncx": True})
            creators = b.get_metadata("DC", "creator")
            author = creators[0][0] if creators else ""
        elif path.suffix.lower() == ".pdf":
            import pymupdf
            m = pymupdf.open(str(path)).metadata or {}
            title = m.get("title") or title
            author = m.get("author") or ""
    except Exception:
        pass
    return title.strip() or path.stem, author.strip()


def get_book(bid: str) -> dict:
    if bid in _books:
        return _books[bid]
    entry = STATE["books"].get(bid)
    if not entry:
        abort(404)
    path = Path(entry["path"])
    if not path.exists():
        abort(410, f"File not found: {path}")
    chapters = []
    for ch in narrate.extract(path):
        paras = [] if ch.text.startswith(ch.title) else [ch.title]
        for p in ch.text.split("\n\n"):
            if p.strip():
                paras += split_long(" ".join(p.split()))
        chapters.append({"title": ch.title, "paras": paras})
    book = {"id": bid, "title": entry["title"], "author": entry["author"], "chapters": chapters}
    _books[bid] = book
    if entry.get("chapterCount") != len(chapters):
        entry["chapterCount"] = len(chapters)
        entry["paraCounts"] = [len(c["paras"]) for c in chapters]
        save_state()
    return book


def add_book(path: Path) -> str:
    path = path.resolve()
    if path.suffix.lower() not in EXTS:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    bid = book_id(path)
    if bid not in STATE["books"]:
        title, author = metadata(path)
        STATE["books"][bid] = {"path": str(path), "title": title, "author": author,
                               "added": time.time(), "opened": 0, "ch": 0, "para": 0, "t": 0}
        save_state()
        get_book(bid)        # parse now so chapter counts are known
    return bid


def cover_bytes(bid: str) -> bytes | None:
    cached = COVERS / f"{bid}.img"
    if cached.exists():
        return cached.read_bytes() or None
    data = None
    path = Path(STATE["books"][bid]["path"])
    try:
        if path.suffix.lower() == ".epub":
            import ebooklib
            from ebooklib import epub
            b = epub.read_epub(str(path), options={"ignore_ncx": True})
            cover_id = None
            for _, attrs in b.get_metadata("OPF", "cover"):
                cover_id = attrs.get("content")
            items = list(b.get_items_of_type(ebooklib.ITEM_COVER))
            if cover_id and b.get_item_with_id(cover_id):
                items.insert(0, b.get_item_with_id(cover_id))
            items += [i for i in b.get_items_of_type(ebooklib.ITEM_IMAGE) if "cover" in i.get_name().lower()]
            for i in items:
                if i.media_type.startswith("image/"):
                    data = i.get_content()
                    break
        elif path.suffix.lower() == ".pdf":
            import pymupdf
            page = pymupdf.open(str(path))[0]
            data = page.get_pixmap(matrix=pymupdf.Matrix(0.6, 0.6)).tobytes("png")
    except Exception:
        data = None
    cached.write_bytes(data or b"")
    return data


# ---------------------------------------------------------------- speech

_narrator = None
_narrator_ready = threading.Event()
_narrator_error = None


def load_narrator():
    global _narrator, _narrator_error
    try:
        _narrator = narrate.Narrator(STATE["settings"]["voice"])
        _narrator.speak("Ready.", voice=STATE["settings"]["voice"])   # warm up
    except Exception as e:     # surfaced to the UI
        _narrator_error = str(e)
    _narrator_ready.set()


def narrator():
    _narrator_ready.wait()
    if _narrator is None:
        abort(503, _narrator_error or "Voice engine failed to load")
    return _narrator


class AudioCache:
    def __init__(self, size=80):
        self.items, self.size, self.lock = OrderedDict(), size, threading.Lock()
        self.pending: dict = {}

    def get(self, key, make):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
            ev = self.pending.get(key)
            owner = ev is None
            if owner:
                ev = self.pending[key] = threading.Event()
        if not owner:                     # someone else is already generating it
            ev.wait()
            with self.lock:
                if key in self.items:
                    return self.items[key]
            return make()
        try:
            data = make()
            with self.lock:
                self.items[key] = data
                while len(self.items) > self.size:
                    self.items.popitem(last=False)
            return data
        finally:
            with self.lock:
                self.pending.pop(key, None)
            ev.set()


CACHE = AudioCache()


def wav_bytes(audio) -> bytes:
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, audio, narrate.SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


# ---------------------------------------------------------------- export jobs

EXPORT = {"running": False, "book": None, "done": 0, "total": 0, "chapter": "", "error": "",
          "result": "", "started": 0}


def run_export(bid, fmt, voice, speed, chapters):
    path = Path(STATE["books"][bid]["path"])
    try:
        chs = narrate.extract(path)
        idx = narrate.parse_range(chapters or None, len(chs))
        args = types.SimpleNamespace(out=str(OUTPUT), voice=voice, speed=speed, format=fmt, keep_work=False)

        def progress(done, total, title):
            EXPORT.update(done=done, total=total, chapter=title)

        dest = narrate.make_book(path, chs, idx, args, narrator=narrator(), progress=progress)
        EXPORT["result"] = str(dest)
    except Exception as e:
        EXPORT["error"] = str(e)
    EXPORT["running"] = False


# ---------------------------------------------------------------- routes

@app.get("/")
def index():
    return send_file(ROOT / "ui" / "index.html")


@app.get("/api/state")
def api_state():
    lib = []
    for bid, b in STATE["books"].items():
        lib.append({"id": bid, **{k: b.get(k) for k in
                    ("title", "author", "opened", "added", "ch", "para", "chapterCount", "paraCounts")},
                    "format": Path(b["path"]).suffix[1:].upper(), "missing": not Path(b["path"]).exists()})
    lib.sort(key=lambda b: (-(b["opened"] or 0), -(b["added"] or 0)))
    return jsonify(settings=STATE["settings"], library=lib, last=STATE["last"],
                   voices=narrate.VOICES, engine="ready" if _narrator else
                   ("error" if _narrator_error else "loading"), engineError=_narrator_error)


@app.get("/api/book/<bid>")
def api_book(bid):
    book = get_book(bid)
    entry = STATE["books"][bid]
    entry["opened"] = time.time()
    STATE["last"] = bid
    save_state()
    return jsonify(id=bid, title=book["title"], author=book["author"], chapters=book["chapters"],
                   pos={"ch": entry.get("ch", 0), "para": entry.get("para", 0), "t": entry.get("t", 0)},
                   bookmarks=entry.get("bookmarks", []))


@app.delete("/api/book/<bid>")
def api_remove(bid):
    STATE["books"].pop(bid, None)
    _books.pop(bid, None)
    if STATE["last"] == bid:
        STATE["last"] = None
    save_state()
    return jsonify(ok=True)


@app.get("/api/cover/<bid>")
def api_cover(bid):
    if bid not in STATE["books"]:
        abort(404)
    data = cover_bytes(bid)
    if not data:
        abort(404)
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    return Response(data, mimetype=mime, headers={"Cache-Control": "max-age=86400"})


@app.post("/api/progress")
def api_progress():
    d = request.get_json(force=True)
    entry = STATE["books"].get(d.get("book"))
    if entry:
        for k in ("ch", "para", "t"):
            if k in d:
                entry[k] = d[k]
        if "bookmarks" in d:
            entry["bookmarks"] = d["bookmarks"]
        save_state()
    return jsonify(ok=True)


@app.post("/api/settings")
def api_settings():
    d = request.get_json(force=True)
    STATE["settings"].update({k: v for k, v in d.items() if k in DEFAULT_SETTINGS})
    save_state()
    return jsonify(settings=STATE["settings"])


@app.post("/api/upload")
def api_upload():
    added = []
    for f in request.files.getlist("files"):
        name = Path(f.filename).name
        if Path(name).suffix.lower() not in EXTS:
            continue
        dest = BOOKS / name
        if not dest.exists():
            f.save(dest)
        added.append(add_book(dest))
    return jsonify(added=added)


@app.post("/api/add-path")
def api_add_path():
    added = []
    for p in request.get_json(force=True).get("paths", []):
        try:
            added.append(add_book(Path(p)))
        except Exception:
            pass
    return jsonify(added=added)


@app.get("/api/tts")
def api_tts():
    bid, ch, p = request.args["book"], int(request.args["ch"]), int(request.args["p"])
    voice = request.args.get("voice") or STATE["settings"]["voice"]
    book = get_book(bid)
    try:
        text = book["chapters"][ch]["paras"][p]
    except IndexError:
        abort(404)
    data = CACHE.get((bid, ch, p, voice), lambda: wav_bytes(narrator().speak(text, voice=voice, speed=1.0)))
    return Response(data, mimetype="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/api/say")
def api_say():
    text = request.args.get("text", "")[:400]
    voice = request.args.get("voice") or STATE["settings"]["voice"]
    data = CACHE.get(("say", text, voice), lambda: wav_bytes(narrator().speak(text, voice=voice, speed=1.0)))
    return Response(data, mimetype="audio/wav")


@app.post("/api/export")
def api_export():
    if EXPORT["running"]:
        return jsonify(error="An export is already running"), 409
    d = request.get_json(force=True)
    s = STATE["settings"]
    EXPORT.update(running=True, book=d["book"], done=0, total=0, chapter="Starting…", error="",
                  result="", started=time.time())
    threading.Thread(target=run_export, daemon=True,
                     args=(d["book"], d.get("format", "m4b"), d.get("voice", s["voice"]),
                           float(d.get("speed", 1.0)), d.get("chapters", ""))).start()
    return jsonify(ok=True)


@app.get("/api/export")
def api_export_status():
    return jsonify(EXPORT)


@app.post("/api/reveal")
def api_reveal():
    target = request.get_json(force=True).get("path") or str(OUTPUT)
    p = Path(target)
    if sys.platform == "win32":
        if p.is_file():
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p if p.exists() else OUTPUT))
    return jsonify(ok=True)


# ---------------------------------------------------------------- desktop window

def add_paths(paths) -> list[str]:
    added = []
    for p in paths or []:
        try:
            added.append(add_book(Path(p)))
        except Exception:
            pass
    return added


class JsApi:
    """Exposed to the page as window.pywebview.api when running in the app window.

    pywebview walks every public attribute of this object to build the JS bridge, so the
    window reference must stay private (a public one makes it crawl the whole window and hang).
    """

    def __init__(self):
        self._window = None

    def pick_files(self):
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        paths = self._window.create_file_dialog(
            kind, allow_multiple=True,
            file_types=("Ebooks (*.epub;*.pdf;*.docx;*.txt;*.md)", "All files (*.*)"))
        return add_paths(paths)


# A second launch (e.g. a book dropped on the desktop icon) hands its files to the running
# window over this local socket instead of opening another copy of the app.
INSTANCE_PORT = 47821


def send_to_running(paths) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=1) as s:
            s.sendall(json.dumps(paths).encode() + b"\n")
        return True
    except OSError:
        return False


def serve_instance(window):
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", INSTANCE_PORT))
    srv.listen()
    while True:
        conn, _ = srv.accept()
        with conn:
            data = conn.makefile().readline()
        try:
            added = add_paths(json.loads(data))
        except Exception:
            added = []
        try:
            window.restore()
            window.show()
        except Exception:
            pass
        window.evaluate_js(f"window.onBooksAdded && onBooksAdded({json.dumps(added)})")


def on_window_ready(window, initial):
    from webview.dom import DOMEventHandler

    def on_drop(e):
        files = (e.get("dataTransfer") or {}).get("files") or []
        added = add_paths([f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")])
        window.evaluate_js(f"window.onBooksAdded && onBooksAdded({json.dumps(added)}, true)")

    window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=False)
    threading.Thread(target=serve_instance, args=(window,), daemon=True).start()
    if initial:
        window.evaluate_js(f"window.onBooksAdded && onBooksAdded({json.dumps(add_paths(initial))})")


def main():
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if files and send_to_running([str(Path(f).resolve()) for f in files]):
        return

    threading.Thread(target=load_narrator, daemon=True).start()

    if "--browser" in sys.argv:
        import webbrowser
        port = 8765
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        app.run(host="127.0.0.1", port=port, threaded=True)
        return

    import webview
    api = JsApi()
    window = webview.create_window("Narrator", app, js_api=api, width=1320, height=860,
                                   min_size=(900, 600), background_color="#14120f")
    api._window = window
    webview.start(on_window_ready, (window, files), private_mode=False,
                  storage_path=str(ROOT / ".cache" / "webview"))


if __name__ == "__main__":
    main()
