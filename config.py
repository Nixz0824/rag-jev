"""Single source of truth for ports, paths and model locations.

Everything (launch.py, engine.py, server.py, scripts/) imports from here so the
supervisor and its children cannot drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
DATA = ROOT / "data"
PATCH_DATA = DATA / "patch"
RAW = PATCH_DATA / "raw"
WEB = ROOT / "web"
DOCS = ROOT / "docs"
TESTS = ROOT / "tests"

# Models live outside the repo when RAGJEV_MODELS is set (a 1.7 GB download does
# not belong in a public repository working tree).
MODELS = Path(os.environ.get("RAGJEV_MODELS", ROOT / "models"))

# 1889x, so this project never collides with the older 1879x proof of concept.
APP_PORT = int(os.environ.get("RAGJEV_APP_PORT", 18890))
CHAT_PORT = int(os.environ.get("RAGJEV_CHAT_PORT", 18891))
EMBED_PORT = int(os.environ.get("RAGJEV_EMBED_PORT", 18892))
PORTS = (APP_PORT, CHAT_PORT, EMBED_PORT)

KEY_FILE = RUNTIME / "model-key.txt"
# llama-server opens --api-key-file with a narrow (non-UTF-8) file API, so this
# path must stay relative to the child's working directory (ROOT).
KEY_FILE_ARG = "runtime/model-key.txt"

DB_PATH = RUNTIME / "sessions.sqlite"
FEEDBACK_FILE = RUNTIME / "feedback.jsonl"

SERVICE = "rag-jev"
