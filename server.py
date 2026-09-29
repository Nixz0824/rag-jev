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

from config import APP_PORT, DB_PATH, DOCS, PATCH_DATA, SERVICE, WEB
from engine import GREETING
from jev import available as jev_available
from patch_engine import PatchEngine, PatchRetriever, compare_versions
import routing

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
        # Routing mode is read from RAGJEV_ROUTING_MODE (off / shadow / active) so the old
        # pipeline stays reproducible without touching code.
        ENGINE = PatchEngine(
            RETRIEVER,
            self_check=os.environ.get("RAGJEV_SELF_CHECK", "1") != "0",
            routing_mode=routing.routing_mode_from_env(),
        )
        LOG.info("routing mode: %s", ENGINE.routing_mode)
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
        if path == "/api/evaluation":
            return self.send_json(evaluation())
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
            # Routing is independent of the key: off/shadow need no credentials, and the mode
            # must be visible before a reader wonders why a stage did nothing.
            "routing_mode": ENGINE.routing_mode if ready else routing.routing_mode_from_env(),
            "routing": (ENGINE.routing_config if ready else routing.CONFIG).as_json(),
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


def evaluation() -> dict:
    """Serve the newest evaluation runs so the UI never hard-codes numbers."""
    runs = []
    for path in sorted(DOCS.glob("*.json")):
        if path.name.startswith("_") or path.parent.name == "patch":
            continue
        if not (path.name.startswith(("评测报告", "盲测报告"))):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        arms = {}
        for name, arm in (payload.get("arms") or {}).items():
            single = arm.get("single") or {}
            jev_stats = arm.get("jev") or {}
            calls = jev_stats.get("latency_calls") or 0
            arms[name] = {
                "single": single,
                "pinned": arm.get("pinned") or {},
                "overview": arm.get("overview") or {},
                "absent": arm.get("absent") or {},
                "out_of_scope": arm.get("out_of_scope") or {},
                "trap": arm.get("trap") or {},
                "selfcheck": arm.get("selfcheck") or {},
                "jev": {
                    "calls": jev_stats.get("calls", 0),
                    "cost_usd": round(jev_stats.get("cost_usd", 0.0), 6),
                    "mean_latency_ms": round(jev_stats.get("latency_total", 0) / calls) if calls else 0,
                },
            }
        runs.append(
            {
                "name": path.stem,
                "generated_at": payload.get("generated_at", ""),
                "case_file": payload.get("case_file", []),
                "use_case_version": payload.get("use_case_version", False),
                "cases": payload.get("cases", 0),
                "corpus": payload.get("corpus", {}),
                "arms": arms,
            }
        )
    parse_file = DOCS / "查询理解.json"
    if parse_file.exists():
        try:
            payload = json.loads(parse_file.read_text(encoding="utf-8"))
            runs.append(
                {
                    "name": "查询理解",
                    "generated_at": payload.get("generated_at", ""),
                    "cases": payload.get("cases", 0),
                    "cases_passed": payload.get("cases_passed", 0),
                    "slots": payload.get("slots", {}),
                    "guard": payload.get("guard", {}),
                    "arms": {},
                }
            )
        except (OSError, json.JSONDecodeError):
            pass

    extras = {}
    corpus_file = DOCS / "语料统计.json"
    if corpus_file.exists():
        try:
            payload = json.loads(corpus_file.read_text(encoding="utf-8"))
            extras["corpus_stats"] = {
                "totals": payload.get("totals", {}),
                "recent": payload.get("recent", {}),
                "per_patch": payload.get("per_patch", []),
            }
        except (OSError, json.JSONDecodeError):
            pass
    for key, filename in (
        ("jev_effect", "Jev效果.json"),
        ("jev_ranker", "Jev效果-排序对照.json"),
        ("jev_selfcheck", "Jev自检.json"),
        ("jev_routing", "Jev分层路由.json"),
        # Preferred first: production runs hybrid, and the hybrid effect report is the one that
        # matches production. The BM25 report stays as the fallback so the page still works when
        # only the reproducible scope has been run.
        ("jev_routing_effect", "Jev分层路由-效果集-hybrid.json"),
        ("_jev_routing_effect_fallback", "Jev分层路由-效果集.json"),
    ):
        path = DOCS / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if key == "jev_selfcheck":
            extras[key] = {
                "cases": payload.get("cases", 0),
                "detected": payload.get("detected", 0),
                "false_alarms": payload.get("false_alarms", 0),
                "threshold": payload.get("threshold", 0.5),
                "control_mean": (payload.get("control") or {}).get("mean", 0),
                "mutation_mean": (payload.get("mutation") or {}).get("mean", 0),
                "control_scores": (payload.get("control") or {}).get("scores", []),
                "mutation_scores": (payload.get("mutation") or {}).get("scores", []),
                "cost_usd": payload.get("cost_usd", 0),
            }
        elif key == "jev_routing":
            # The routing report is served whole (minus the bulky per-case rows) so the page
            # can read its numbers instead of hard-coding them; `source` matters because a
            # fixture run is not a measurement of the model.
            extras[key] = {
                "source": payload.get("source", ""),
                "modes": payload.get("modes", []),
                "cases": payload.get("cases", 0),
                "arms": payload.get("arms", {}),
                "behaviour": payload.get("behaviour", {}),
                "improved": payload.get("improved", 0),
                "hurt": payload.get("hurt", 0),
                "recall_gained": payload.get("recall_gained", 0),
                "recall_lost": payload.get("recall_lost", 0),
                "changed": payload.get("changed", []),
                "config": payload.get("config", {}),
                "generated_at": payload.get("generated_at", ""),
            }
        elif key == "jev_routing_effect":
            # Same shape, plus the outcome matrix the page quotes: who was solved by which
            # scope. `only_off` is the number that would argue against routing, so it travels
            # with the report rather than being derived in the browser.
            rows = payload.get("rows", {})
            off_rows, active_rows = rows.get("off", []), rows.get("active", [])

            def solved(rowset):
                grouped: dict[str, list[bool]] = {}
                for row in rowset:
                    grouped.setdefault(row["id"], []).append(bool(row["hit@1"]))
                return grouped

            off_solved, active_solved = solved(off_rows), solved(active_rows)
            matrix = {"both_right": 0, "only_active": 0, "only_off": 0, "both_wrong": 0}
            for case_id, hits in off_solved.items():
                off_any, active_any = any(hits), any(active_solved.get(case_id, []))
                key_name = ("both_right" if off_any and active_any else
                            "only_active" if active_any else
                            "only_off" if off_any else "both_wrong")
                matrix[key_name] += 1
            extras[key] = {
                "source": payload.get("source", ""),
                "retrieval": "hybrid" if "hybrid" in filename else "bm25",
                "report": filename,
                "cases": payload.get("cases", 0),
                "arms": payload.get("arms", {}),
                "behaviour": payload.get("behaviour", {}),
                "improved": payload.get("improved", 0),
                "hurt": payload.get("hurt", 0),
                "matrix": matrix,
                "generated_at": payload.get("generated_at", ""),
            }
        else:
            extras[key] = payload.get("stats", {})
    # The hybrid report is served as the primary key; when only the reproducible BM25 run
    # exists, promote it so the page still shows a routing result instead of "—".
    if "jev_routing_effect" not in extras and "_jev_routing_effect_fallback" in extras:
        extras["jev_routing_effect"] = extras.pop("_jev_routing_effect_fallback")
    extras.pop("_jev_routing_effect_fallback", None)
    return {"runs": runs, **extras}


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
