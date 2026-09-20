"""HTTP layer: loopback-only JSON API plus the static workbench.

Deliberately small: one sqlite table, per-session locking, no external calls
except the two loopback model servers and (optionally) TypeSafe for Jev.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import APP_PORT, DB_PATH, PATCH_DATA, SERVICE, WEB
from engine import GREETING
from jev import available as jev_available
from patch_engine import PatchEngine, PatchRetriever, compare_versions

LOG = logging.getLogger("ragjev.server")
MAX_BODY = 200_000
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
RETAIN_DAYS = 30
RETAIN_ROWS = 500

ENGINE: PatchEngine | None = None
ENGINE_ERROR = ""
RETRIEVER: PatchRetriever | None = None
LOCK = threading.Lock()
SESSION_LOCKS: dict[str, threading.Lock] = {}


def connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL)")
    return db


def save_session(session: dict) -> None:
    with connection() as db:
        db.execute(
            "INSERT INTO sessions (id, updated, data) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, data=excluded.data",
            (session["id"], time.time(), json.dumps(session, ensure_ascii=False)),
        )


def load_session(session_id: str) -> dict | None:
    with connection() as db:
        row = db.execute("SELECT data FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def prune() -> None:
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE updated < ?", (time.time() - RETAIN_DAYS * 86400,))
        db.execute(
            "DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY updated DESC LIMIT ?)",
            (RETAIN_ROWS,),
        )


def session_lock(session_id: str) -> threading.Lock:
    with LOCK:
        return SESSION_LOCKS.setdefault(session_id, threading.Lock())


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%H:%M:%S"
    )


def engine() -> PatchEngine:
    if ENGINE_ERROR:
        raise RuntimeError(ENGINE_ERROR)
    if ENGINE is None:
        raise RuntimeError("索引尚未就绪")
    return ENGINE


def bootstrap() -> None:
    """Build the retriever and the vector index; record failures for /api/health."""
    global ENGINE, ENGINE_ERROR, RETRIEVER
    try:
        RETRIEVER = PatchRetriever()
        RETRIEVER.build()
        # Answer self-check costs one extra Jev call per answer; RAGJEV_SELF_CHECK=0 disables it.
        ENGINE = PatchEngine(RETRIEVER, self_check=os.environ.get("RAGJEV_SELF_CHECK", "1") != "0")
    except Exception as error:  # noqa: BLE001 - surfaced through /api/health
        ENGINE_ERROR = str(error)
        LOG.exception("bootstrap failed")


class Handler(BaseHTTPRequestHandler):
    server_version = "RagJev/0.1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ plumbing

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        LOG.info("%s %s", self.address_string(), fmt % args)

    def allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
            return False
        return True

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求体过大或为空")
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ValueError("需要 application/json")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # --------------------------------------------------------------------- routes

    def do_GET(self):  # noqa: N802 - stdlib signature
        if not self.allowed():
            return self.send_json({"error": "只接受本机访问"}, 403)
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self.send_json(health())
        if path == "/api/patch/meta":
            return self.send_json(patch_meta())
        if path == "/api/patch/compare":
            return self.send_json(patch_compare(self.path))
        if path == "/api/session":
            session_id = (self.path.split("session_id=") + [""])[1].split("&")[0]
            if not SESSION_RE.fullmatch(session_id or ""):
                return self.send_json({"error": "会话 ID 无效"}, 400)
            session = load_session(session_id)
            return self.send_json(session or {"error": "会话不存在"}, 200 if session else 404)
        return self.serve_static(path)

    def do_POST(self):  # noqa: N802 - stdlib signature
        if not self.allowed():
            return self.send_json({"error": "只接受本机访问"}, 403)
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError) as error:
            return self.send_json({"error": f"请求体无效：{error}"}, 400)
        path = self.path.split("?")[0]
        session_id = str(payload.get("session_id") or "")
        if not SESSION_RE.fullmatch(session_id):
            return self.send_json({"error": "会话 ID 无效"}, 400)
        if path not in ("/api/chat", "/api/feedback"):
            return self.send_json({"error": "未知接口"}, 404)
        if ENGINE_ERROR:
            return self.send_json({"error": f"服务未就绪：{ENGINE_ERROR}"}, 503)
        patch = payload.get("patch") or None
        try:
            with session_lock(session_id):
                session = load_session(session_id) or engine().new(session_id)
                if path == "/api/chat":
                    text = str(payload.get("text") or "").strip()
                    session = engine().chat(session, text, selected_patch=patch)
                else:
                    session = engine().feedback(session, str(payload.get("result") or ""), str(payload.get("note") or ""))
                save_session(session)
                prune()
            return self.send_json(session)
        except ValueError as error:
            return self.send_json({"error": str(error)}, 400)
        except RuntimeError as error:
            return self.send_json({"error": str(error)}, 503)
        except Exception as error:  # noqa: BLE001 - keep the UI alive
            LOG.exception("chat failed")
            return self.send_json({"error": f"服务内部错误：{error}"}, 500)

    def serve_static(self, path: str):
        relative = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / relative).resolve()
        if not str(target).startswith(str(WEB.resolve())) or not target.is_file():
            return self.send_json({"error": "未找到"}, 404)
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def health() -> dict:
    ready = ENGINE is not None
    payload = {
        "service": SERVICE,
        "ready": ready,
        "error": ENGINE_ERROR,
        "jev": {
            "enabled": ready and ENGINE.use_jev,
            "key": jev_available(),
            "self_check": ready and ENGINE.self_check,
        },
    }
    if ready:
        payload["chunks"] = len(RETRIEVER.chunks)
        payload["patches"] = {"supported": RETRIEVER.supported, "latest": RETRIEVER.latest}
    return payload


def patch_meta() -> dict:
    if ENGINE is None:
        return {"error": ENGINE_ERROR or "服务未就绪"}
    return {
        "title": RETRIEVER.kb.get("title", ""),
        "source_note": RETRIEVER.kb.get("source_note", ""),
        "generated_at": RETRIEVER.kb.get("generated_at", ""),
        "stats": RETRIEVER.kb.get("stats", {}),
        "patches": RETRIEVER.supported,
        "latest": RETRIEVER.latest,
        "patch_map": RETRIEVER.patch_map,
        "examples": EXAMPLE_QUESTIONS,
    }


EXAMPLE_QUESTIONS = [
    "26.17 亚索改了什么",
    "26.17 有哪些英雄被削弱",
    "26.17 岚切怎么调整的",
]


def patch_compare(path: str) -> dict:
    """GET /api/patch/compare?a=26.16&b=26.17&subject=亚索 — structured diff of two patches."""
    if ENGINE is None:
        return {"error": ENGINE_ERROR or "服务未就绪"}
    query = urllib.parse.parse_qs(path.split("?", 1)[1] if "?" in path else "")
    patch_a = (query.get("a") or [""])[0]
    patch_b = (query.get("b") or [""])[0]
    subject = (query.get("subject") or [""])[0].strip()
    if not patch_a or not patch_b:
        return {"error": "需要 a 与 b 两个版本", "supported": RETRIEVER.supported}
    for value in (patch_a, patch_b):
        if value not in RETRIEVER.supported:
            return {"error": f"没有收录版本 {value}", "supported": RETRIEVER.supported}
    if subject:
        resolved, _ = RETRIEVER.resolve_subject(subject)
        subject = resolved or subject

    def rows_of(patch: str) -> list[dict]:
        where = {"patches": [patch], "mode": "rift"}
        if subject:
            where["subject"] = subject
        return [row for row in RETRIEVER.all(where) if row.get("field_key") != "narrative"]

    return {
        "a": patch_a,
        "b": patch_b,
        "subject": subject,
        "subjects": compare_versions(rows_of(patch_a), rows_of(patch_b)),
    }


def main() -> int:
    configure_logging()
    LOG.info("bootstrapping patch corpus and vector index")
    bootstrap()
    server = ThreadingHTTPServer(("127.0.0.1", APP_PORT), Handler)
    LOG.info("listening on http://127.0.0.1:%s", APP_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
