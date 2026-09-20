"""Review local answer feedback and turn it into candidate evaluation cases.

Reads ``runtime/feedback.jsonl`` (written by the 准确/有误/可能过时 buttons) and
writes ``docs/反馈复核.md``: a summary plus, for every incorrect/outdated answer,
the question, the evidence it cited and a ready-to-edit case skeleton.

Usage:
    python scripts/review_feedback.py
    python scripts/review_feedback.py --drafts tests/cases/feedback_drafts.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import DOCS, PATCH_DATA, RUNTIME  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FEEDBACK = RUNTIME / "feedback.jsonl"
LABELS = {"accurate": "准确", "incorrect": "有误", "outdated": "可能过时"}


def load_feedback(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="复核本地反馈并生成候选评测案例")
    parser.add_argument("--drafts", default="", help="把待复核样本写成案例草稿到这个路径")
    args = parser.parse_args()

    records = load_feedback(FEEDBACK)
    if not records:
        print(f"还没有反馈记录（{FEEDBACK} 不存在或为空）。")
        print("先在页面上回答后点「准确 / 有误 / 可能过时」，反馈会写进这个文件。")
        return 0

    corpus = {}
    knowledge = PATCH_DATA / "knowledge.json"
    if knowledge.exists():
        corpus = {row["id"]: row for row in json.loads(knowledge.read_text(encoding="utf-8"))["chunks"]}

    counts = collections.Counter(record.get("result", "?") for record in records)
    by_day = collections.Counter(time.strftime("%Y-%m-%d", time.localtime(record.get("time", 0))) for record in records)
    problems = [record for record in records if record.get("result") in ("incorrect", "outdated")]

    lines = [
        "# 反馈复核",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　反馈总数：{len(records)}",
        "",
        "| 类型 | 条数 |",
        "|---|---|",
    ]
    for key in ("accurate", "incorrect", "outdated"):
        lines.append(f"| {LABELS.get(key, key)} | {counts.get(key, 0)} |")
    lines += ["", "每日反馈：" + "、".join(f"{day} {n}" for day, n in sorted(by_day.items())), ""]

    if not problems:
        lines += ["没有「有误 / 可能过时」样本，暂不需要复核。", ""]
    else:
        lines += [
            f"## 待复核 {len(problems)} 条",
            "",
            "> 复核步骤：① 在国服公告里核对这条回答；② 如果系统答错了，把它写成评测案例（草稿见文件末尾）；"
            "③ 如果是语料缺口，记进 `docs/解析覆盖率.md` 的说明。",
            "",
            "| 时间 | 问题 | 用户备注 | 引用的证据 |",
            "|---|---|---|---|",
        ]
        for record in problems[-40:]:
            when = time.strftime("%m-%d %H:%M", time.localtime(record.get("time", 0)))
            evidence = "、".join(record.get("evidence_ids") or [])[:80] or "—"
            note = (record.get("note") or "").replace("|", "／")[:60] or "—"
            lines.append(f"| {when} | {record.get('question', '')[:60]} | {note} | {evidence} |")
        lines.append("")
        for record in problems[-40:]:
            lines.append(f"### {record.get('question', '')[:80]}")
            lines.append(f"- 反馈：{LABELS.get(record.get('result'), record.get('result'))}")
            if record.get("note"):
                lines.append(f"- 备注：{record['note']}")
            query = record.get("query") or {}
            lines.append(
                f"- 解析：主体 {query.get('subject') or '—'}｜版本 {', '.join(query.get('patches') or []) or '—'}"
                f"｜技能 {query.get('ability') or '—'}｜字段 {', '.join(query.get('field_keys') or []) or '—'}"
            )
            for evidence_id in record.get("evidence_ids") or []:
                row = corpus.get(evidence_id)
                if row:
                    lines.append(f"- 证据 {evidence_id}：{row.get('text', '')[:120]}")
            lines.append("")

    DOCS.mkdir(exist_ok=True)
    (DOCS / "反馈复核.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"反馈 {len(records)} 条（有误/过时 {len(problems)} 条）→ {DOCS / '反馈复核.md'}")

    if args.drafts and problems:
        drafts = {
            "note": "由 runtime/feedback.jsonl 的『有误/可能过时』样本生成的案例草稿；必须先在公告里人工核对再启用。",
            "cases": [
                {
                    "id": f"f{index:02d}",
                    "question": record.get("question", ""),
                    "kind": "single",
                    "expect": {
                        "patch": (record.get("query") or {}).get("patches", [""])[0] if (record.get("query") or {}).get("patches") else "",
                        "subject": (record.get("query") or {}).get("subject") or "",
                        "field_key": ((record.get("query") or {}).get("field_keys") or [""])[0],
                        "value": "TODO 从公告抄写正确的新值",
                    },
                    "source_url": "TODO 公告链接",
                }
                for index, record in enumerate(problems[-40:], start=1)
            ],
        }
        Path(args.drafts).write_text(json.dumps(drafts, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"草稿 {len(drafts['cases'])} 条 → {args.drafts}（source_url 与 value 需要人工填）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
