#!/usr/bin/env python3
"""Deterministic reconciler orchestrator.

Usage:
    python -m reconcile.run_reconcile --label <activity-folder>
    python -m reconcile.run_reconcile --assessment-dir output/<label>
    python -m reconcile.run_reconcile --label <x> --grades-csv output/course_<slug>/course_grades_<ts>.csv

Reads the latest submissions_export_*.csv and exam_config_as_is_*.csv from the
assessment folder, applies the deterministic rules, and writes the reports to
<folder>/reconcile/. No API call, no LLM.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Reuse extractor infrastructure (paths, slugify, writers).
from extractor.config import OUTPUT_DIR, ExtractorError, slugify
from extractor.writers import write_csv, write_xlsx

from .core import build_config_index, check_answer, join_key, norm, reconcile_grade, to_decimal

REPORT_COLUMNS = [
    "assessment_id", "user_id", "username", "email",
    "blockId", "blockType", "question_number", "description",
    "submitted_answer", "configured_correct_answer", "configured_accepted_answers",
    "points", "max_points", "derived_score_status",
    "verifiable", "is_correct", "flag",
]
GRADE_COLUMNS = [
    "assessment_id", "user_id", "username", "email",
    "official_grade", "sum_points", "sum_max",
    "derived_pct", "derived_pct_rounded", "status",
]
CONSISTENCY_COLUMNS = [
    "assessment_id", "blockId", "blockType", "description",
    "normalized_answer", "n_students", "distinct_points", "flag",
]
QUEUE_COLUMNS = [
    "assessment_id", "blockId", "blockType", "question_number", "description",
    "reason", "n_students", "answer_distribution", "note",
]


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _latest(folder: Path, stem: str):
    files = sorted(glob.glob(str(folder / f"{stem}_*.csv")), key=os.path.getmtime)
    return files[-1] if files else None


def _read_csv(path: str):
    import pandas as pd

    return pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")


def _resolve_dir(label: str | None, assessment_dir: str | None) -> Path:
    if assessment_dir:
        p = Path(assessment_dir)
        return p if p.is_absolute() else Path.cwd() / p
    if label:
        return OUTPUT_DIR / slugify(label)
    raise ExtractorError("Provide --label <folder> or --assessment-dir <path>.")


def run(label=None, assessment_dir=None, grades_csv=None) -> int:
    run_ts = _timestamp()
    folder = _resolve_dir(label, assessment_dir)
    if not folder.exists():
        raise ExtractorError(f"Assessment folder not found: {folder}")

    sub_path = _latest(folder, "submissions_export")
    cfg_path = _latest(folder, "exam_config_as_is")
    if not sub_path:
        raise ExtractorError(f"No submissions_export_*.csv in {folder}")
    if not cfg_path:
        raise ExtractorError(
            f"No exam_config_as_is_*.csv in {folder} (run run_exam_config first)."
        )
    submissions = _read_csv(sub_path)
    exam_config = _read_csv(cfg_path)
    print(f"Loaded {len(submissions)} submission rows, {len(exam_config)} config rows.")

    cfg_index = build_config_index(exam_config)

    # Optional official grades from course_grades, keyed by (assessment_id, user_id).
    grade_override = {}
    if grades_csv:
        for g in _read_csv(grades_csv):
            grade_override[(g.get("learningUnit_id"), g.get("user_id"))] = g.get("grade")
        print(f"Loaded {len(grade_override)} course-grade record(s) for override.")

    # ---- per-answer reconciliation rows + accumulators ----
    report_rows = []
    grade_acc = defaultdict(lambda: {"pts": Decimal(0), "max": Decimal(0), "meta": {}})
    consistency = defaultdict(lambda: defaultdict(set))   # (aid,blockId) -> answer -> {points}
    queue = defaultdict(lambda: {"answers": defaultdict(lambda: {"n": 0, "points": set()})})
    flag_counts = defaultdict(int)
    verifiable_counts = defaultdict(int)

    for r in submissions:
        aid = r.get("assessment_id"); uid = r.get("user_id")
        bt = r.get("blockType"); ans = r.get("answer")
        pts = r.get("points"); mx = r.get("blockMaxScore", r.get("max_points"))
        bid = r.get("blockId"); desc = r.get("description")

        cfg = cfg_index.get(join_key(desc))
        chk = check_answer(bt, ans, pts, mx, cfg)
        verifiable_counts[chk["verifiable"]] += 1
        if chk["flag"]:
            flag_counts[chk["flag"]] += 1

        report_rows.append({
            "assessment_id": aid, "user_id": uid,
            "username": r.get("username"), "email": r.get("email"),
            "blockId": bid, "blockType": bt,
            "question_number": (cfg or {}).get("question_number", ""),
            "description": desc, "submitted_answer": ans,
            "configured_correct_answer": chk["configured_correct"],
            "configured_accepted_answers": chk["accepted"],
            "points": pts, "max_points": mx,
            "derived_score_status": r.get("derived_score_status"),
            "verifiable": chk["verifiable"],
            "is_correct": ("" if chk["is_correct"] is None else chk["is_correct"]),
            "flag": chk["flag"] or "",
        })

        # grade accumulator
        gp = to_decimal(pts); gm = to_decimal(mx)
        acc = grade_acc[(aid, uid)]
        if gp is not None: acc["pts"] += gp
        if gm is not None: acc["max"] += gm
        acc["meta"] = {"username": r.get("username"), "email": r.get("email"),
                       "grade": r.get("grade")}

        # cross-student consistency (only meaningful within a question)
        consistency[(aid, bid)][norm(ans)].add(str(pts))

        # manual review queue (questions without a usable key)
        if chk["verifiable"] in ("no_config_match", "no_answer_key", "unverifiable_mcma"):
            q = queue[(aid, bid)]
            q["blockType"] = bt; q["description"] = desc
            q["question_number"] = (cfg or {}).get("question_number", "")
            q["reason"] = chk["verifiable"]
            a = q["answers"][norm(ans)]
            a["n"] += 1; a["points"].add(str(pts))

    # ---- grade reconciliation ----
    grade_rows = []
    grade_status_counts = defaultdict(int)
    for (aid, uid), acc in grade_acc.items():
        official = grade_override.get((aid, uid), acc["meta"].get("grade"))
        gr = reconcile_grade(acc["pts"], acc["max"], official)
        grade_status_counts[gr["status"]] += 1
        grade_rows.append({
            "assessment_id": aid, "user_id": uid,
            "username": acc["meta"].get("username"), "email": acc["meta"].get("email"),
            "official_grade": gr["official_grade"],
            "sum_points": gr["sum_points"], "sum_max": gr["sum_max"],
            "derived_pct": gr["derived_pct"],
            "derived_pct_rounded": gr["derived_pct_rounded"],
            "status": gr["status"],
        })

    # ---- consistency report (same question + same answer, different points) ----
    consistency_rows = []
    desc_by_block = {}
    bt_by_block = {}
    for r in submissions:
        desc_by_block[(r.get("assessment_id"), r.get("blockId"))] = r.get("description")
        bt_by_block[(r.get("assessment_id"), r.get("blockId"))] = r.get("blockType")
    for (aid, bid), answers in consistency.items():
        for nans, ptset in answers.items():
            if len(ptset) > 1:  # same answer text scored differently
                consistency_rows.append({
                    "assessment_id": aid, "blockId": bid,
                    "blockType": bt_by_block.get((aid, bid)),
                    "description": desc_by_block.get((aid, bid)),
                    "normalized_answer": nans,
                    "n_students": "",  # filled below if needed
                    "distinct_points": sorted(ptset),
                    "flag": "inconsistent_scoring",
                })

    # ---- manual review queue (deduplicated per question) ----
    queue_rows = []
    for (aid, bid), q in queue.items():
        n_students = sum(a["n"] for a in q["answers"].values())
        dist = {ans: {"n": a["n"], "points": sorted(a["points"])}
                for ans, a in q["answers"].items()}
        notes = {
            "no_config_match": "Question not present in the exam-config export "
                               "(e.g. 'match' type is not exported). Review parametrization once.",
            "no_answer_key": "Config row exists but has no configured correct answer. Review once.",
            "unverifiable_mcma": "Multi-select answer could not be reconstructed unambiguously. Review once.",
        }
        queue_rows.append({
            "assessment_id": aid, "blockId": bid,
            "blockType": q.get("blockType"),
            "question_number": q.get("question_number", ""),
            "description": q.get("description"),
            "reason": q.get("reason"),
            "n_students": n_students,
            "answer_distribution": dist,
            "note": notes.get(q.get("reason"), ""),
        })

    # ---- write outputs ----
    out_dir = folder / "reconcile"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for rows, cols, stem in [
        (report_rows, REPORT_COLUMNS, "reconciliation_report"),
        (grade_rows, GRADE_COLUMNS, "grade_reconciliation"),
        (consistency_rows, CONSISTENCY_COLUMNS, "consistency_report"),
        (queue_rows, QUEUE_COLUMNS, "manual_review_queue"),
    ]:
        if rows:
            c = write_csv(rows, cols, out_dir, run_ts, filename_stem=stem)
            x = write_xlsx(rows, cols, out_dir, run_ts, filename_stem=stem)
            outputs[stem] = {"csv": str(c), "xlsx": str(x), "rows": len(rows)}
        else:
            outputs[stem] = {"rows": 0}

    summary = {
        "tool": "learnworlds-reconcile",
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "assessment_folder": str(folder),
        "inputs": {"submissions": os.path.basename(sub_path),
                   "exam_config": os.path.basename(cfg_path)},
        "submission_rows": len(submissions),
        "config_questions": len(exam_config),
        "verifiable_breakdown": dict(verifiable_counts),
        "flag_counts": dict(flag_counts),
        "grade_status_counts": dict(grade_status_counts),
        "inconsistent_scoring_questions": len(consistency_rows),
        "manual_review_questions": len(queue_rows),
        "notes": [
            "Flags are deterministic; partial-credit (answer_accepted_but_partial) is informational.",
            "derived_pct = round(Σpoints/Σmax*100); compared to the official grade only when present.",
            "No grade recomputation as truth, no cross-CSV semantic judgement, no API/LLM.",
        ],
        "outputs": outputs,
    }
    summary_path = out_dir / f"reconciliation_summary_{run_ts}.json"
    with summary_path.open("w", encoding="utf-8") as h:
        json.dump(summary, h, ensure_ascii=False, indent=2)

    # ---- console summary ----
    print(f"\nVerifiable: {dict(verifiable_counts)}")
    print(f"Flags: {dict(flag_counts) or '(none)'}")
    print(f"Grade reconciliation: {dict(grade_status_counts)}")
    print(f"Inconsistent-scoring questions: {len(consistency_rows)}")
    print(f"Manual-review questions (deduped): {len(queue_rows)}")
    print(f"\nWrote reports to: {out_dir}")
    print(f"Summary: {summary_path}")
    print("Done.")
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Deterministic LearnWorlds reconciler.")
    p.add_argument("--label", help="Activity folder name under output/.")
    p.add_argument("--assessment-dir", help="Path to the assessment output folder.")
    p.add_argument("--grades-csv", help="Optional course_grades CSV for the official grade.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return run(label=args.label, assessment_dir=args.assessment_dir,
                   grades_csv=args.grades_csv)
    except ExtractorError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        import os as _os
        if _os.getenv("DEBUG", "").lower() in {"1", "true", "yes"}:
            raise
        print(f"\nUNEXPECTED ERROR: {exc}\nSet DEBUG=true for a traceback.\n",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
