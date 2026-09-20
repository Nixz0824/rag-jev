"""Retrieval core: BM25 + Qwen embeddings + RRF, with metadata filtering.

The retriever is deliberately corpus-agnostic: ``data/patch/knowledge.json`` is
the only input, so the same code can serve any versioned-changelog corpus. Facts
are never generated here; this module only ranks evidence.
"""

from __future__ import annotations

import collections
import hashlib
import json
import logging
import math
import re
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

from config import EMBED_PORT, KEY_FILE

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOG = logging.getLogger("ragjev")

# Scope gate. These are a policy, not calibrated confidence: they are chosen on
# tests/cases/gate_cases.json and are re-checked whenever the corpus changes.
# A corpus may override them with data/patch/thresholds.json.
GATE_SIMILARITY = 0.48
GATE_BM25 = 6.0

BLOCKED = re.compile(
    r"忽略.{0,6}(指令|提示|规则|要求)"
    r"|(输出|打印|给我|告诉我|显示).{0,8}(密钥|密码|凭据|token|api[_ -]?key)"
    r"|泄露.{0,8}(密钥|密码|凭据|token|api[_ -]?key)"
)
GREETING = re.compile(r"(?i)(hi|hello|hey|你好|您好|嗨|在吗)[!！。,.， ]*")


def post_local(port: int, path: str, data: dict, timeout: int = 120) -> dict:
    """POST to a loopback model server, with the local API key when present."""
    headers = {"Content-Type": "application/json"}
    if KEY_FILE.exists():
        headers["Authorization"] = "Bearer " + KEY_FILE.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(data, ensure_ascii=False).encode(),
        headers=headers,
    )
    with HTTP.open(request, timeout=timeout) as response:
        return json.load(response)


def redact(text: str) -> str:
    """Best-effort removal of credentials and contact details before storage."""
    text = re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)", "[凭据已隐藏]", text)
    text = re.sub(
        r"(?i)(password|token|api[_ -]?key|authorization|密码|验证码|令牌)\s*[:=：]\s*\S+", r"\1: [已隐藏]", text
    )
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[邮箱已隐藏]", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已隐藏]", text)
    return text


def tokens(text: str) -> list[str]:
    """Latin words plus Chinese bigrams; single-character segments stay whole."""
    latin = re.findall(r"[a-z0-9_.-]+", text.lower())
    out = list(latin)
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(segment) == 1:
            out.append(segment)
        else:
            out.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return out


class Retriever:
    """BM25 over one corpus plus a cached embedding matrix.

    Ranking happens after metadata filtering, so a high-similarity hit from the
    wrong version can never outrank a correct hit from the asked-about version.
    """

    FILTER_KEYS = ("patches", "subject", "type", "ability", "direction", "mode", "section")

    def __init__(self, corpus_path: Path, instruction: str, model_tag: str, embed_fn=None):
        self.path = Path(corpus_path)
        self.raw = self.path.read_bytes()
        self.kb = json.loads(self.raw)
        self.chunks = self.kb["chunks"]
        self.by_id = {row["id"]: row for row in self.chunks}
        self.texts = [f"{row['text']} {row.get('keywords', '')} {row.get('source_title', '')}" for row in self.chunks]
        self.terms = [collections.Counter(tokens(text)) for text in self.texts]
        self.instruction = instruction
        self.embed_fn = embed_fn or self._http_embed
        self.model_tag = model_tag
        self.matrix: np.ndarray | None = None
        self.query_cache: dict[str, np.ndarray] = {}
        self.lock = threading.Lock()
        self.fingerprint = hashlib.sha256(
            self.raw + model_tag.encode() + b"pooling-last" + instruction.encode()
        ).hexdigest()

    # ---------------------------------------------------------------- embeddings

    def _http_embed(self, text: str) -> np.ndarray:
        response = post_local(EMBED_PORT, "/v1/embeddings", {"input": text, "model": "local-embedding"})
        vector = np.asarray(response["data"][0]["embedding"], dtype=np.float32)
        return vector / max(float(np.linalg.norm(vector)), 1e-10)

    def embed(self, text: str) -> np.ndarray:
        return self.embed_fn(text)

    def build(self) -> None:
        """Load the cached matrix, or build it when the corpus changed."""
        meta, index = self.path.parent / "index-meta.json", self.path.parent / "index.npz"
        if meta.exists() and index.exists():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == self.fingerprint:
                self.matrix = np.load(index)["vectors"]
                return
        started = time.perf_counter()
        vectors = [self.embed(f"{row['text']}\n{row.get('keywords', '')}") for row in self.chunks]
        self.matrix = np.stack(vectors)
        np.savez_compressed(index, vectors=self.matrix)
        meta.write_text(
            json.dumps(
                {
                    "fingerprint": self.fingerprint,
                    "chunks": len(vectors),
                    "dimensions": len(vectors[0]),
                    "model": self.model_tag,
                    "filtering": "metadata before ranking",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        LOG.info("rebuilt vector index: %d chunks in %.1fs", len(vectors), time.perf_counter() - started)

    # ------------------------------------------------------------------ filtering

    def _candidate_indices(self, where: dict | None) -> list[int]:
        where = where or {}
        patches = set(where.get("patches") or [])
        result = []
        for index, row in enumerate(self.chunks):
            if patches and row.get("patch") not in patches:
                continue
            if any(where.get(key) and row.get(key) != where[key] for key in self.FILTER_KEYS if key != "patches"):
                continue
            result.append(index)
        return result

    def all(self, where: dict | None = None) -> list[dict]:
        return [self.chunks[index] for index in self._candidate_indices(where)]

    # ------------------------------------------------------------------ retrieval

    def search(self, query: str, mode: str = "hybrid", k: int = 12, where: dict | None = None) -> list[dict]:
        if mode not in ("bm25", "vector", "hybrid"):
            raise ValueError("检索模式无效")
        candidates = self._candidate_indices(where)
        if not candidates:
            return []
        query = redact(query)
        lengths = [sum(self.terms[index].values()) for index in candidates]
        average = sum(lengths) / len(lengths) or 1.0
        document_frequency = collections.Counter(term for index in candidates for term in self.terms[index])
        bm25 = np.zeros(len(candidates))
        for word in set(tokens(query)):
            df = document_frequency.get(word, 0)
            idf = math.log(1 + (len(candidates) - df + 0.5) / (df + 0.5))
            for local_index, document_index in enumerate(candidates):
                frequency = self.terms[document_index].get(word, 0)
                bm25[local_index] += (
                    idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * lengths[local_index] / average))
                )
        dense = np.zeros(len(candidates))
        if mode != "bm25":
            if self.matrix is None:
                raise RuntimeError("向量索引尚未就绪，请稍后重试。")
            with self.lock:
                if query not in self.query_cache:
                    self.query_cache[query] = self.embed(self.instruction + query)
                    if len(self.query_cache) > 128:
                        self.query_cache.pop(next(iter(self.query_cache)))
                vector = self.query_cache[query]
            dense = self.matrix[candidates] @ vector
        if mode == "bm25":
            score = bm25
        elif mode == "vector":
            score = dense
        else:
            score = np.zeros(len(candidates))
            for rank, index in enumerate(np.argsort(-bm25)):
                if bm25[index] > 0:
                    score[index] += 1 / (60 + rank + 1)
            for rank, index in enumerate(np.argsort(-dense)):
                score[index] += 1 / (60 + rank + 1)
        results = []
        for local_index in np.argsort(-score)[:k]:
            document_index = candidates[int(local_index)]
            results.append(
                {
                    **self.chunks[document_index],
                    "rank": len(results) + 1,
                    "score": round(float(score[local_index]), 5),
                    "bm25": round(float(bm25[local_index]), 3),
                    "similarity": round(float(dense[local_index]), 4),
                    "retrieval_mode": mode,
                }
            )
        return results

    def gate(self, hits: list[dict], thresholds: dict | None = None) -> dict:
        """Whether the retrieved evidence is strong enough to answer at all."""
        similarity = max((hit["similarity"] for hit in hits), default=0.0)
        bm25 = max((hit.get("bm25", 0.0) for hit in hits), default=0.0)
        limit_similarity = (thresholds or {}).get("similarity", GATE_SIMILARITY)
        limit_bm25 = (thresholds or {}).get("bm25", GATE_BM25)
        return {
            "supported": similarity >= limit_similarity or bm25 >= limit_bm25,
            "similarity": round(similarity, 4),
            "bm25": round(bm25, 3),
            "thresholds": {"similarity": limit_similarity, "bm25": limit_bm25},
        }
