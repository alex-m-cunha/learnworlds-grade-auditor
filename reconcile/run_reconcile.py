#!/usr/bin/env python3
"""Deterministic reconciler orchestrator.

Preferred usage (unified launcher — shared run folder):
    python -m reconcile.run_reconcile --run-dir output/<program>/<label>/<ts>

Manual CLI (point at a timestamped run folder directly):
    python -m reconcile.run_reconcile --assessment-dir output/<program>/<label>

Reads submissions_export.csv and exam_config_as_is.csv, applies deterministic
rules, and writes reports to <run-dir>/reconcile/. No API call, no LLM.

Output layout (inside the shared run folder):
    <run-dir>/
      reconcile/
        reconciliation_report/
          reconciliation_report.csv
          reconciliation_report.xlsx
        grade_reconciliation/
          grade_reconciliation.csv
          grade_reconciliation.xlsx
        consistency_report/
          consistency_report.csv
          consistency_report.xlsx
        manual_review_queue/
          manual_review_queue.csv
          manual_review_queue.xlsx
        reconciliation_summary.json
        reconciliation_summary.md

Note on --label: resolves to output/<label>/ (single level). With the current
output structure (output/<program>/<label>/), prefer --assessment-dir or --run-dir.
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

from .core import (
    build_config_index, build_merged_index, build_inferred_index,
    check_answer, check_inferred_answer,
    join_key, norm, reconcile_grade, to_decimal,
)

REPORT_COLUMNS = [
    "assessment_id", "user_id", "username", "email",
    "blockId", "blockType", "question_number", "description",
    "submitted_answer", "configured_correct_answer", "configured_accepted_answers",
    "points", "max_points", "derived_score_status",
    "verifiable", "is_correct", "flag", "answer_matched_source",
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


def _build_summary_md(summary: dict, activity_name: str) -> str:
    ts = summary.get("run_timestamp", "")
    sub_rows = summary.get("submission_rows", 0)
    cfg_q = summary.get("config_questions", 0)
    ver = summary.get("verifiable_breakdown", {})
    flags = summary.get("flag_counts", {})
    grades = summary.get("grade_status_counts", {})
    inconsistent = summary.get("inconsistent_scoring_questions", 0)
    queue_n = summary.get("manual_review_questions", 0)
    outputs = summary.get("outputs", {})
    ak = summary.get("answer_key", {})

    def _row(label, val):
        return f"| {label} | {val} |"

    lines = [
        f"# Reconciliação — {activity_name}",
        "",
        f"**Run:** {ts}",
        f"**Linhas de submissão:** {sub_rows} &nbsp;|&nbsp; **Perguntas no gabarito:** {cfg_q}",
        "",
        "---",
        "",
        "## Verificabilidade das respostas",
        "",
        "| Estado | Respostas |",
        "|--------|----------:|",
        _row("Verificável (`yes`)", ver.get("yes", 0)),
        _row("Sem match no gabarito (`no_config_match`)", ver.get("no_config_match", 0)),
        _row("Sem chave de resposta (`no_answer_key`)", ver.get("no_answer_key", 0)),
        _row("Multi-resposta ambíguo (`unverifiable_mcma`)", ver.get("unverifiable_mcma", 0)),
        _row("Verificado por inferência (`inferred`)", ver.get("inferred", 0)),
        _row("Inferência sem resposta extraída (`inferred_unresolved`)", ver.get("inferred_unresolved", 0)),
        "",
        "---",
        "",
        "## Flags de contradição",
        "",
    ]

    if flags:
        lines += [
            "| Flag | Ocorrências |",
            "|------|------------:|",
        ]
        flag_labels = {
            "answer_accepted_but_zero": "Resposta aceite mas 0 pontos (`answer_accepted_but_zero`)",
            "answer_correct_per_doc_but_zero": "Resposta correcta (gabarito docente) rejeitada pelo LW com 0 pontos — erro de parametrização (`answer_correct_per_doc_but_zero`) ⚠️",
            "answer_not_accepted_but_full": "Resposta não aceite mas nota máxima (`answer_not_accepted_but_full`)",
            "answer_accepted_but_partial": "Crédito parcial (`answer_accepted_but_partial`) ℹ️",
        }
        for key, flabel in flag_labels.items():
            if key in flags:
                lines.append(_row(flabel, flags[key]))
        for key, val in flags.items():
            if key not in flag_labels:
                lines.append(_row(f"`{key}`", val))
    else:
        lines.append("Sem flags de contradição. ✅")

    lines += [
        "",
        "---",
        "",
        "## Reconciliação de notas (por aluno)",
        "",
        "| Estado | Alunos |",
        "|--------|-------:|",
        _row("Match (`match`)", grades.get("match", 0)),
        _row("Mismatch (`mismatch`)", grades.get("mismatch", 0)),
        _row("Sem nota oficial (`grade_unavailable`)", grades.get("grade_unavailable", 0)),
        "",
        "---",
        "",
        "## Coerência entre alunos",
        "",
        (
            f"Perguntas com a mesma resposta pontuada de forma diferente: **{inconsistent}**"
            + (" ✅" if inconsistent == 0 else "")
        ),
        "",
        "---",
        "",
        "## Fila de revisão manual",
        "",
        (
            f"Perguntas a rever (deduplicadas): **{queue_n}**"
            + (" ✅" if queue_n == 0 else "")
        ),
        "",
        "---",
        "",
    ]

    if ak:
        conf = ak.get("confidence_breakdown", {})
        match = ak.get("answers_match_breakdown", {})
        needs = ak.get("needs_review", 0)
        discrepancies = ak.get("discrepancies", 0)
        sources = ak.get("source_docs", [])
        lines += [
            "## Gabarito docente (Word → LLM)",
            "",
            f"**Guiões:** {', '.join(f'`{s}`' for s in sources) if sources else '—'}",
            "",
            "| Confiança | Perguntas |",
            "|-----------|----------:|",
            _row("Alta (`high`)", conf.get("high", 0)),
            _row("Média (`medium`)", conf.get("medium", 0)),
            _row("Baixa (`low`)", conf.get("low", 0)),
            _row("Sem match (`unmatched`)", conf.get("unmatched", 0)),
            "",
            "| Cruzamento LW vs Word doc | Perguntas |",
            "|---------------------------|----------:|",
            _row("Concordam (`yes`)", match.get("yes", 0)),
            _row("Discrepância (`no`)", match.get("no", 0)),
            _row("Só em LW (`lw_only`)", match.get("lw_only", 0)),
            _row("Só no doc (`doc_only`)", match.get("doc_only", 0)),
            "",
            (
                f"Discrepâncias LW ≠ Word doc: **{discrepancies}**"
                + (" ✅" if discrepancies == 0 else "")
            ),
            f"A necessitar revisão humana: **{needs}**"
            + (" ✅" if needs == 0 else ""),
            (
                f"Perguntas do Word doc sem match no LW (texto divergente): **{ak.get('no_lw_match_count', 0)}**"
                + (" ✅" if ak.get("no_lw_match_count", 0) == 0 else " ⚠️ — verificar manualmente")
            ),
            "",
            "---",
            "",
        ]

    inf = ak.get("inferred", {}) if ak else {}
    if inf:
        inf_conf = inf.get("confidence_breakdown", {})
        lines += [
            "## Respostas inferidas (preenchimento de lacunas / correspondência)",
            "",
            "> ⚠️ Estas respostas foram inferidas pelo LLM a partir do gabarito ID / Docente "
            "para perguntas que o LW não exporta. Tratar como indicativo — verificação humana necessária.",
            "",
            "| Confiança | Perguntas |",
            "|-----------|----------:|",
            _row("Alta (`high`)", inf_conf.get("high", 0)),
            _row("Média (`medium`)", inf_conf.get("medium", 0)),
            _row("Baixa (`low`)", inf_conf.get("low", 0)),
            _row("Sem match (`unmatched`)", inf_conf.get("unmatched", 0)),
            "",
            f"Verificadas (resposta extraída e validada pelo utilizador): **{inf.get('matched_count', 0)}**",
            f"Sem resposta extraída (`inferred_unresolved`): **{inf.get('unresolved_count', 0)}**",
            "",
            "---",
            "",
        ]

    lines += [
        "## Outputs",
        "",
    ]

    output_labels = {
        "reconciliation_report": "Relatório completo (1 linha por aluno × pergunta)",
        "grade_reconciliation": "Reconciliação de notas (1 linha por aluno)",
        "consistency_report": "Coerência entre alunos",
        "manual_review_queue": "Fila de revisão manual (deduplicada)",
    }
    for stem, info in outputs.items():
        n = info.get("rows", 0)
        olabel = output_labels.get(stem, stem)
        lines.append(f"- `{stem}/` — {olabel}: **{n}** linha(s)")

    lines += [
        "",
        "---",
        "",
        "> ℹ️ `answer_accepted_but_partial` é informativo (crédito parcial configurado), não uma contradição.",
        "> Flags determinísticos — sem API, sem LLM.",
    ]

    return "\n".join(lines) + "\n"


def _latest(folder: Path, stem: str):
    """Find the most recently modified <stem>.csv inside any timestamped sub-run.

    Searches new layout first (folder/<ts>/<step>/<stem>.csv), then the previous
    flat layout (folder/<ts>/<stem>.csv), then the legacy timestamped-filename layout.
    """
    # Current layout: folder/<ts>/<step_name>/<stem>.csv
    files = sorted(
        glob.glob(str(folder / "*" / "*" / f"{stem}.csv")),
        key=os.path.getmtime,
    )
    if files:
        return files[-1]
    # Previous layout: folder/<ts>/<stem>.csv
    files = sorted(
        glob.glob(str(folder / "*" / f"{stem}.csv")),
        key=os.path.getmtime,
    )
    if files:
        return files[-1]
    # Legacy: folder/<stem>_<ts>.csv
    files = sorted(
        glob.glob(str(folder / f"{stem}_*.csv")),
        key=os.path.getmtime,
    )
    return files[-1] if files else None


def _read_csv(path: str):
    import pandas as pd

    return pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records")


def _resolve_dir(label: str | None, assessment_dir: str | None) -> Path:
    if assessment_dir:
        p = Path(assessment_dir)
        return p if p.is_absolute() else Path.cwd() / p
    if label:
        # Support both output/<label>/ and output/<program>/<label>/ layouts.
        # Try reading program from assessment.cfg; fall back to flat layout.
        try:
            from extractor.config import _load_cfg_file, PROJECT_ROOT
            cfg = _load_cfg_file(PROJECT_ROOT / "assessment.cfg")
            program = (cfg.get("PROGRAM") or "").strip()
            if program:
                candidate = OUTPUT_DIR / slugify(program) / slugify(label)
                if candidate.exists():
                    return candidate
        except Exception:
            pass
        return OUTPUT_DIR / slugify(label)
    raise ExtractorError("Provide --label <folder> or --assessment-dir <path>.")


def run(label=None, assessment_dir=None, grades_csv=None, run_dir=None) -> int:
    if run_dir:
        # Unified launcher path: all inputs are in fixed step subfolders.
        rd = Path(run_dir)
        sub_path = str(rd / "submissions" / "submissions_export.csv")
        cfg_path = str(rd / "exam_config" / "exam_config_as_is.csv")
        if not Path(sub_path).exists():
            raise ExtractorError(f"submissions_export.csv not found at: {sub_path}")
        if not Path(cfg_path).exists():
            raise ExtractorError(f"exam_config_as_is.csv not found at: {cfg_path}")
        # If not explicitly provided, look for grades in the run-dir grades step.
        if not grades_csv:
            candidate = rd / "grades" / "course_grades.csv"
            if candidate.exists():
                grades_csv = str(candidate)
        reconcile_dir = rd / "reconcile"
        folder = rd  # used only for display
    else:
        folder = _resolve_dir(label, assessment_dir)
        if not folder.exists():
            raise ExtractorError(f"Assessment folder not found: {folder}")

        sub_path = _latest(folder, "submissions_export")
        cfg_path = _latest(folder, "exam_config_as_is")
        if not sub_path:
            raise ExtractorError(f"No submissions_export.csv found under {folder}")
        if not cfg_path:
            raise ExtractorError(
                f"No exam_config_as_is.csv found under {folder} "
                "(run run_exam_config first)."
            )

        # Write reconcile output alongside the inputs that produced it.
        run_folder = Path(sub_path).parent
        # If sub_path is in a raw/ subfolder (shouldn't happen but guard), step up.
        if run_folder.name == "raw":
            run_folder = run_folder.parent
        reconcile_dir = run_folder / "reconcile"

    submissions = _read_csv(sub_path)
    exam_config = _read_csv(cfg_path)
    print(f"Loaded {len(submissions)} submission rows, {len(exam_config)} config rows.")

    # Load answer key early so build_merged_index() can enrich the config index.
    ak_rows: list = []
    inferred_rows: list = []
    if run_dir:
        ak_early_path = Path(run_dir) / "answer_key" / "manual_answer_key.csv"
        if ak_early_path.exists():
            ak_rows = _read_csv(str(ak_early_path))
        inferred_path = Path(run_dir) / "answer_key" / "inferred_answer_key.csv"
        if inferred_path.exists():
            inferred_rows = _read_csv(str(inferred_path))

    cfg_index = build_merged_index(exam_config, ak_rows) if ak_rows else build_config_index(exam_config)
    inferred_index = build_inferred_index(inferred_rows) if inferred_rows else {}

    # Detect Word doc questions that couldn't be matched to any LW question by text.
    ak_no_lw_match_count = 0
    if ak_rows:
        ak_no_lw_match_count = sum(
            1 for row in ak_rows
            if join_key(row.get("question_text", "")) not in cfg_index
        )

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
        inferred_entry = None
        if cfg is None and inferred_index:
            inferred_entry = inferred_index.get(join_key(desc))
            chk = (
                check_inferred_answer(bt, ans, pts, mx, inferred_entry)
                if inferred_entry
                else check_answer(bt, ans, pts, mx, None)
            )
        else:
            chk = check_answer(bt, ans, pts, mx, cfg)
        verifiable_counts[chk["verifiable"]] += 1
        if chk["flag"]:
            flag_counts[chk["flag"]] += 1

        report_rows.append({
            "assessment_id": aid, "user_id": uid,
            "username": r.get("username"), "email": r.get("email"),
            "blockId": bid, "blockType": bt,
            "question_number": (cfg or {}).get("question_number", "") or (inferred_entry or {}).get("doc_question_number", ""),
            "description": desc, "submitted_answer": ans,
            "configured_correct_answer": chk["configured_correct"],
            "configured_accepted_answers": chk["accepted"],
            "points": pts, "max_points": mx,
            "derived_score_status": r.get("derived_score_status"),
            "verifiable": chk["verifiable"],
            "is_correct": ("" if chk["is_correct"] is None else chk["is_correct"]),
            "flag": chk["flag"] or "",
            "answer_matched_source": chk.get("answer_matched_source", ""),
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

        # manual review queue (questions without a usable key or with low-confidence inference)
        if chk["verifiable"] in ("no_config_match", "no_answer_key", "unverifiable_mcma", "inferred_unresolved"):
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
                    "n_students": "",
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
            "inferred_unresolved": "Pergunta inferida do Word doc mas sem resposta extraída pelo LLM — "
                                   "verificação manual necessária.",
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

    # ---- answer key stats (ak_rows and inferred_rows loaded earlier) ----
    answer_key_summary: dict = {}
    if inferred_rows:
        from collections import Counter as _Counter
        inf_conf = _Counter(r.get("confidence", "") for r in inferred_rows)
        inf_matched = sum(
            1 for v in inferred_index.values()
            if v.get("inferred_correct_answer") and v.get("confidence") != "unmatched"
        )
        inf_unresolved = sum(
            1 for v in inferred_index.values()
            if not v.get("inferred_correct_answer") or v.get("confidence") == "unmatched"
        )
        answer_key_summary["inferred"] = {
            "questions": len(inferred_rows),
            "confidence_breakdown": dict(inf_conf),
            "matched_count": inf_matched,
            "unresolved_count": inf_unresolved,
        }
        print(
            f"Inferidas: {len(inferred_rows)} pergunta(s), "
            f"high={inf_conf.get('high', 0)}, "
            f"medium={inf_conf.get('medium', 0)}, "
            f"verificadas={inf_matched}, sem_resposta={inf_unresolved}."
        )
    if ak_rows:
        from collections import Counter
        conf_counts = Counter(r.get("confidence", "") for r in ak_rows)
        match_counts = Counter(r.get("answers_match", "") for r in ak_rows)
        sources = sorted({r.get("source_doc", "") for r in ak_rows if r.get("source_doc")})
        doc_override_count = sum(1 for v in cfg_index.values() if v.get("doc_overrides_lw"))
        answer_key_summary = {
            "questions": len(ak_rows),
            "confidence_breakdown": dict(conf_counts),
            "answers_match_breakdown": dict(match_counts),
            "discrepancies": match_counts.get("no", 0),
            "needs_review": sum(1 for r in ak_rows if r.get("needs_review") == "true"),
            "source_docs": sources,
            "doc_override_count": doc_override_count,
            "no_lw_match_count": ak_no_lw_match_count,
        }
        print(
            f"Answer key: {len(ak_rows)} question(s), "
            f"high={conf_counts.get('high', 0)}, "
            f"unmatched={conf_counts.get('unmatched', 0)}, "
            f"discrepancies={match_counts.get('no', 0)}, "
            f"doc_overrides={doc_override_count}, "
            f"no_lw_match={ak_no_lw_match_count}."
        )

    # ---- write outputs ----
    # Each report gets its own subfolder; summary JSON stays at reconcile/ root.
    reconcile_dir.mkdir(parents=True, exist_ok=True)

    outputs = {}
    for rows, cols, stem in [
        (report_rows, REPORT_COLUMNS, "reconciliation_report"),
        (grade_rows, GRADE_COLUMNS, "grade_reconciliation"),
        (consistency_rows, CONSISTENCY_COLUMNS, "consistency_report"),
        (queue_rows, QUEUE_COLUMNS, "manual_review_queue"),
    ]:
        report_dir = reconcile_dir / stem
        report_dir.mkdir(exist_ok=True)
        c = write_csv(rows, cols, report_dir, stem)
        x = write_xlsx(rows, cols, report_dir, stem)
        outputs[stem] = {"csv": str(c), "xlsx": str(x), "rows": len(rows)}

    summary = {
        "tool": "learnworlds-reconcile",
        "run_timestamp": datetime.now().isoformat(timespec="seconds"),
        "assessment_folder": str(folder),
        "reconcile_dir": str(reconcile_dir),
        "inputs": {"submissions": os.path.basename(sub_path),
                   "exam_config": os.path.basename(cfg_path)},
        "submission_rows": len(submissions),
        "config_questions": len(exam_config),
        "verifiable_breakdown": dict(verifiable_counts),
        "flag_counts": dict(flag_counts),
        "grade_status_counts": dict(grade_status_counts),
        "inconsistent_scoring_questions": len(consistency_rows),
        "manual_review_questions": len(queue_rows),
        "answer_key": answer_key_summary,
        "notes": [
            "Flags are deterministic; partial-credit (answer_accepted_but_partial) is informational.",
            "derived_pct = round(Σpoints/Σmax*100); compared to the official grade only when present.",
            "No grade recomputation as truth, no cross-CSV semantic judgement, no API/LLM.",
        ],
        "outputs": outputs,
    }
    summary_path = reconcile_dir / "reconciliation_summary.json"
    with summary_path.open("w", encoding="utf-8") as h:
        json.dump(summary, h, ensure_ascii=False, indent=2)

    md_path = reconcile_dir / "reconciliation_summary.md"
    activity_name = label or (Path(run_dir).name if run_dir else reconcile_dir.parent.parent.name)
    md_path.write_text(_build_summary_md(summary, activity_name), encoding="utf-8")

    # ---- console summary ----
    print(f"\nVerifiable: {dict(verifiable_counts)}")
    print(f"Flags: {dict(flag_counts) or '(none)'}")
    print(f"Grade reconciliation: {dict(grade_status_counts)}")
    print(f"Inconsistent-scoring questions: {len(consistency_rows)}")
    print(f"Manual-review questions (deduped): {len(queue_rows)}")
    print(f"\nWrote reports to: {reconcile_dir}")
    print(f"Summary: {summary_path}")
    print("Done.")
    return 0


def _parse_args(argv):
    p = argparse.ArgumentParser(description="Deterministic LearnWorlds reconciler.")
    p.add_argument("--label", help="Activity folder name under output/.")
    p.add_argument("--assessment-dir", help="Path to the assessment output folder.")
    p.add_argument("--grades-csv", help="Optional course_grades CSV for the official grade.")
    p.add_argument(
        "--run-dir",
        help="Shared run folder from the unified launcher. When set, inputs are read "
        "from <run-dir>/submissions/ and <run-dir>/exam_config/; output goes to "
        "<run-dir>/reconcile/.",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        return run(label=args.label, assessment_dir=args.assessment_dir,
                   grades_csv=args.grades_csv, run_dir=args.run_dir)
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
