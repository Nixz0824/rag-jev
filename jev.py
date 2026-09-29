"""TypeSafe System One (Jev) client: typed decisions over retrieved candidates.

Jev returns calibrated choices and probabilities instead of generated text. This
module owns transport and *primitives only* — which decision to ask for, and when, is
business policy and lives in ``routing.py`` (semantic fallback + hierarchical routing)
and ``patch_engine.py`` (rerank stage, answer self-check). Keeping the split means this
file never grows category tables or retrieval rules.

Primitives, roughly in the order one query meets them:

* ``choose``        — one multi-question choice call, namespaced answers (generic form)
* ``classify``      — single-slot choice, the historical call site
* ``classify_many`` — the same decision for several slots in one HTTP call (cost control)
* ``shortlist``     — pick 1 of N already-narrowed entities, never "guess among 170 names"
* ``rerank``        — which retrieved change answers the question (choice + noul per hit)
* ``judge``         — is this statement supported by this evidence (noul per claim)
* ``judge_many``    — the same, batched, used as the answer self-check

Every function degrades to ``None`` on a missing key or a network error so the
caller can fall back to deterministic retrieval. Degradation is never silent: the
caller records the failure in ``session["trace"]`` and ``session["jev"]["phases"]``.
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
# A rerank only applies when the winner is clearly ahead; a near-tie keeps the
# retrieval order, because two indistinguishable rows are equally correct.
MIN_RERANK_GAP = 0.15


# The four roles Jev plays in this pipeline, in lifecycle order. Every call is booked
# against exactly one of them so the UI and the evaluations can answer "how much did
# routing cost compared to reranking" instead of reporting one blended number.
PHASES = ("semantic_fallback", "hierarchical_routing", "rerank", "evidence_judge")
PHASE_LABELS = {
    "semantic_fallback": "语义补全",
    "hierarchical_routing": "分层路由",
    "rerank": "候选重排",
    "evidence_judge": "证据判读",
}
# What happened to a phase. BYPASSED is a first-class outcome, not a failure: the
# deterministic parser having already decided the slot is a designed result.
PHASE_CALLED = "called"
PHASE_BYPASSED = "bypassed"
PHASE_FALLBACK = "fallback"
PHASE_FAILED = "failed"


def empty_phases() -> dict[str, dict]:
    return {
        phase: {
            "phase": phase,
            "label": PHASE_LABELS[phase],
            "status": PHASE_BYPASSED,
            "calls": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
            "detail": "",
        }
        for phase in PHASES
    }


def book(phases: dict, phase: str, status: str, cost_usd: float = 0.0,
         latency_ms: int = 0, detail: str = "", calls: int = 0) -> None:
    """Record one phase outcome. Cumulative, so a phase that calls twice adds up."""
    entry = phases.setdefault(phase, {
        "phase": phase, "label": PHASE_LABELS.get(phase, phase), "status": PHASE_BYPASSED,
        "calls": 0, "cost_usd": 0.0, "latency_ms": 0, "detail": "",
    })
    if status != PHASE_BYPASSED or entry["status"] == PHASE_BYPASSED:
        entry["status"] = status
    entry["calls"] += int(calls or (1 if status == PHASE_CALLED else 0))
    entry["cost_usd"] = round(entry["cost_usd"] + float(cost_usd or 0.0), 6)
    entry["latency_ms"] += int(latency_ms or 0)
    if detail:
        entry["detail"] = detail


def phase_totals(phases: dict) -> dict:
    """Sum the breakdown so the UI can print a total that matches the per-phase lines."""
    entries = list((phases or {}).values())
    return {
        "calls": sum(int(entry.get("calls", 0)) for entry in entries),
        "cost_usd": round(sum(float(entry.get("cost_usd", 0.0)) for entry in entries), 6),
        "latency_ms": sum(int(entry.get("latency_ms", 0)) for entry in entries),
    }


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
    ranked_scores = sorted((value for value in scores.values() if value is not None), reverse=True)
    gap = (ranked_scores[0] - ranked_scores[1]) if len(ranked_scores) > 1 else 1.0
    decisive = gap >= MIN_RERANK_GAP
    if not decisive:
        ordered = list(hits)
    return {
        "ordered": ordered,
        "scores": scores,
        "decisive": decisive,
        "gap": round(gap, 4),
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


def choice_result(answer: dict, result: dict, key: str | None = None) -> dict:
    """Normalise one choice answer into the shape the router reads.

    A choice answer is only useful when it is well formed: without the picked label
    there is nothing to act on, so that case returns ``None`` and the caller keeps its
    previous decision instead of treating a missing label as a choice of "nothing".
    """
    if not isinstance(answer, dict):
        return None
    picked = answer.get("choice")
    if picked in (None, ""):
        return None
    probabilities = answer.get("probabilities")
    probabilities = probabilities if isinstance(probabilities, dict) else {}
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)):
        # Some responses carry only the distribution; the picked label's own
        # probability is then the honest confidence, and 0 is honest when absent.
        confidence = probabilities.get(picked, 0.0)
    return {
        "choice": picked,
        "probabilities": {str(name): float(value) for name, value in probabilities.items()
                          if isinstance(value, (int, float))},
        "confidence": round(float(confidence), 4),
        "label": key,
        "latency_ms": result.get("latency_ms", 0),
        "cost_usd": result.get("cost_usd", 0.0),
        "usage": result.get("usage", {}),
        "model": result.get("model", MODEL),
    }


def classify_many(
    question: str,
    slots: dict[str, dict[str, str]],
    context: str = "",
    api_key: str | None = None,
    timeout: int = 60,
) -> dict | None:
    """Ask several independent slot-choice questions in **one** HTTP call.

    Hierarchical routing needs to know a handful of slots at once (intent, mode,
    ability, field). Asking them one call at a time multiplies latency and cost for no
    accuracy gain, because the questions are independent and the state text is shared.

    ``slots`` maps a slot name to its ``{label: description}`` criteria, e.g.
    ``{"ability": {"W": "W（技能名）", "base": "基础属性"}}``. Returns
    ``{"slots": {name: <choice_result>}, "usage": …, "latency_ms": …, "cost_usd": …}``
    with unanswerable slots simply absent, or ``None`` when the call failed.
    """
    slots = {name: options for name, options in slots.items() if options}
    if not slots or not (api_key or load_api_key()):
        return None
    state = (context + "\n" if context else "") + "用户问题：" + question.strip()
    questions = {
        name: {
            "type": "choice",
            "instructions": (
                f"Which single {name} does the user's question belong to? "
                "Answer with the label that best fits; do not invent labels."
            ),
            "criteria": options,
        }
        for name, options in slots.items()
    }
    result = ask(state, questions, api_key=api_key, timeout=timeout)
    if not result:
        return None
    resolved = {}
    for name in slots:
        parsed = choice_result(result["answers"].get(name), result, key=name)
        if parsed:
            resolved[name] = parsed
    return {
        "slots": resolved,
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "cost_usd": result["cost_usd"],
        "model": result["model"],
    }


def shortlist(
    question: str,
    candidates: dict[str, str],
    instructions: str = "",
    api_key: str | None = None,
    timeout: int = 60,
) -> dict | None:
    """Pick one of an already-narrowed candidate set (lexical/fuzzy shortlist).

    Same transport as ``classify`` but with wording for an entity decision, so the
    router can say "these three names are lexically close to what the user typed;
    which one did they mean" without this module owning the shortlist policy.
    """
    if len(candidates) < 2 or not (api_key or load_api_key()):
        return None
    state = "用户问题：" + question.strip() + "\n候选对象（只能从中选一个）："
    result = ask(
        state,
        {
            "pick": {
                "type": "choice",
                "instructions": instructions
                or (
                    "Which single candidate is the object the user is asking about? "
                    "Pick none if none of them is the object the user meant."
                ),
                "criteria": {**candidates, "none": "以上都不是用户想问的对象"},
            }
        },
        api_key=api_key,
        timeout=timeout,
    )
    if not result or "pick" not in result["answers"]:
        return None
    parsed = choice_result(result["answers"]["pick"], result, key="pick")
    if not parsed:
        return None
    parsed["candidates"] = list(candidates)
    return parsed


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
