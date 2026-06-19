"""LearnWorlds API Extractor — modular, audit-grade extraction package (V1).

Phase 1 scope: assessment submissions -> submissions_export.csv + extraction_report.json.
Coexists with the legacy export_assessment_responses.py (left untouched).

API → CSV only. No answer validation, no grade recomputation, no semantic logic.
Token policy unchanged: never create/refresh/revoke tokens.
"""

__all__ = [
    "config",
    "client",
    "submissions",
    "grades",
    "users",
    "exam_config",
    "writers",
    "report",
]
