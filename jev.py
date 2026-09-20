"""TypeSafe System One (Jev) client: typed decisions over retrieved candidates.

Jev returns calibrated choices and probabilities instead of generated text, so it
is used here for exactly three jobs:

* ``rerank``   — which retrieved change answers the question (choice + noul per hit)
* ``classify`` — which slot the question is about (field / subject / intent)
* ``judge``    — is this statement supported by this evidence (noul per claim)

Every function degrades to ``None`` on a missing key or a network error so the
caller can fall back to deterministic retrieval. Degradation is never silent: the
caller records ``jev.mode`` in the session trace.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from config import ROOT, RUNTIME

LOG = logging.getLogger("ragjev.jev")

ENDPOINT = os.environ.get("TYPESAFE_ENDPOINT", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
PRICE_PER_INPUT_TOKEN_USD = 0.042 / 1e6
MAX_CANDIDATES = 20
MAX_CANDIDATE_CHARS = 320


def load_api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if key:
        return key
    for path in (RUNTIME / "jev-key.txt", ROOT / ".env.local"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                match = line.strip().removeprefix("export ").split("=", 1)
                if len(match) == 2 and match[0].strip() == "TYPESAFE_API_KEY":
                    return match[1].strip().strip("'\"")
        except OSError:
            continue
    return ""


def available() -> bool:
    return bool(load_api_key())


def estimate_cost_usd(usage: dict | None) -> float:
    tokens = (usage or {}).get("input_tokens", 0)
    return round(tokens * PRICE_PER_INPUT_TOKEN_USD, 6)


def ask(state, questions, model: str = MODEL, api_key: str | None = None, timeout: int = 60, retries: int = 2) -> dict:
    """One System One call. Raises nothing on transport failure; returns None."""
    key = api_key or load_api_key()
    if not key:
        return None
    payload = json.dumps({"model": model, "state": state, "questions": questions}, ensure_ascii=False).encode()
    backoff = [1.0, 3.0, 8.0]
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            ENDPOINT,
            data=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            return {
                "answers": body.get("answers", {}),
                "usage": body.get("usage", {}),
                "model": body.get("model", model),
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "cost_usd": estimate_cost_usd(body.get("usage")),
            }
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            LOG.warning("Jev HTTP %s: %s", error.code, error.read().decode("utf-8", "replace")[:200])
            return None
        except Exception as error:  # noqa: BLE001 - transport errors must not break the app
            if attempt < retries:
                time.sleep(backoff[min(attempt, len(backoff) - 1)])
                continue
            LOG.warning("Jev unavailable: %s", error)
            return None
    return None


def compact(text: str, limit: int = MAX_CANDIDATE_CHARS) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def candidate_state(question: str, candidates: list[dict], key: str = "text") -> tuple[str, dict[str, str]]:
    """Render candidates as i1..iN and return the label map Jev answers against."""
    labels = {}
    lines = []
    for index, candidate in enumerate(candidates[:MAX_CANDIDATES], start=1):
        label = f"i{index}"
        labels[label] = candidate["id"]
        lines.append(f"{label}: {compact(candidate.get(key, ''))}")
    state = "用户问题：" + question.strip() + "\n候选：\n" + "\n".join(lines)
    return state, labels


def rerank(question: str, hits: list[dict], api_key: str | None = None, timeout: int = 60) -> dict | None:
    """Order retrieved hits by Jev's per-candidate relevance, with a choice pick.

    Returns ``{"ordered": [...], "scores": {id: noul}, "choice_id": id|None,
    "usage": {...}, "latency_ms": int, "cost_usd": float}`` or ``None``.
    """
    if not hits or not (api_key or load_api_key()):
        return None
    state, labels = candidate_state(question, hits)
    questions = {
        "pick": {
            "type": "choice",
            "instructions": (
                "Which single candidate directly and completely answers the user's question? "
                "Pick none if no candidate answers it."
            ),
            "criteria": {
                **{label: compact(hit.get("text", "")) for label, hit in zip(labels, hits, strict=False)},
                "none": "no candidate answers the question",
            },
        },
        **{
            f"rel_{label}": {
                "type": "noul",
                "instructions": (
                    f"Is candidate {label} the change the user is asking about — same patch, same subject, "
                    "same stat — and does it directly answer the question?"
                ),
            }
            for label in labels
        },
    }
    result = ask(state, questions, api_key=api_key, timeout=timeout)
    if not result or "pick" not in result["answers"]:
        return None
    answers = result["answers"]
    scores = {}
    for label, chunk_id in labels.items():
        value = (answers.get(f"rel_{label}") or {}).get("noul")
        scores[chunk_id] = round(float(value), 4) if isinstance(value, (int, float)) else None
    choice_label = answers["pick"].get("choice")
    ordered = sorted(
        hits,
        key=lambda hit: (scores.get(hit["id"]) if scores.get(hit["id"]) is not None else -1.0, -hit.get("rank", 0)),
        reverse=True,
    )
    return {
        "ordered": ordered,
        "scores": scores,
        "choice_id": labels.get(choice_label) if choice_label in labels else None,
        "pick_probabilities": answers["pick"].get("probabilities", {}),
        "pick_confidence": answers["pick"].get("confidence"),
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "cost_usd": result["cost_usd"],
        "model": result["model"],
    }


def classify(question: str, options: dict[str, str], context: str = "", api_key: str | None = None) -> dict | None:
    """Pick one option (choice) for a question slot, e.g. field or intent."""
    if not options or not (api_key or load_api_key()):
        return None
    state = (context + "\n" if context else "") + "用户问题：" + question.strip()
    result = ask(
        state,
        {
            "pick": {
                "type": "choice",
                "instructions": "Which single category does the user's question belong to?",
                "criteria": options,
            }
        },
        api_key=api_key,
    )
    if not result or "pick" not in result["answers"]:
        return None
    answer = result["answers"]["pick"]
    return {
        "choice": answer.get("choice"),
        "probabilities": answer.get("probabilities", {}),
        "confidence": answer.get("confidence"),
        "latency_ms": result["latency_ms"],
        "cost_usd": result["cost_usd"],
    }


def judge(statement: str, evidence: str, api_key: str | None = None) -> dict | None:
    """Noul: does the evidence support the statement as written?"""
    if not (api_key or load_api_key()):
        return None
    state = f"陈述：{compact(statement, 600)}\n证据：{compact(evidence, 900)}"
    result = ask(
        state,
        {
            "supported": {
                "type": "noul",
                "instructions": "Does the evidence support the statement exactly as written (same numbers and same version)?",
            }
        },
        api_key=api_key,
    )
    if not result or "supported" not in result["answers"]:
        return None
    return {
        "supported": float(result["answers"]["supported"].get("noul", 0.0)),
        "latency_ms": result["latency_ms"],
        "cost_usd": result["cost_usd"],
    }


def judge_many(pairs: list[tuple[str, str]], api_key: str | None = None, timeout: int = 60) -> dict | None:
    """One call, one noul per (statement, evidence) pair — used as an answer self-check.

    Returns ``{"scores": [float, ...], "min": float, "mean": float, "usage": {...},
    "latency_ms": int, "cost_usd": float}`` or ``None``.
    """
    pairs = [(s, e) for s, e in pairs if s and e][:12]
    if not pairs or not (api_key or load_api_key()):
        return None
    state = "\n".join(
        f"陈述 {index}：{compact(statement, 400)}\n证据 {index}：{compact(evidence, 600)}"
        for index, (statement, evidence) in enumerate(pairs)
    )
    questions = {
        f"c{index}": {
            "type": "noul",
            "instructions": (
                f"Does 证据 {index} support 陈述 {index} exactly as written — same numbers, same patch, "
                "no extra claim that is not in the evidence?"
            ),
        }
        for index in range(len(pairs))
    }
    result = ask(state, questions, api_key=api_key, timeout=timeout)
    if not result:
        return None
    scores = []
    for index in range(len(pairs)):
        answer = result["answers"].get(f"c{index}") or {}
        value = answer.get("noul")
        scores.append(round(float(value), 4) if isinstance(value, (int, float)) else 0.0)
    return {
        "scores": scores,
        "min": min(scores),
        "mean": round(sum(scores) / len(scores), 4),
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "cost_usd": result["cost_usd"],
        "model": result["model"],
    }
