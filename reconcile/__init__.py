"""Deterministic reconciler for LearnWorlds assessment extracts.

Separate phase from extraction. Joins the extracted CSVs and FLAGS discrepancies
with exact, auditable rules — it does NOT recompute grades as truth, does NOT make
pedagogical judgements, and uses NO API and NO LLM.

Outputs (per assessment) under output/<label>/reconcile/:
  - reconciliation_report     : one row per (learner, question) with flags
  - grade_reconciliation      : official grade vs derived (Σpoints/Σmax*100)
  - consistency_report        : same question + same answer scored differently
  - manual_review_queue       : unique questions with no answer key (review once)
  - reconciliation_summary.json
"""

__all__ = ["core", "run_reconcile"]
