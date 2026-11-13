"""Evidence-first, CPU-only APCTT Stage 1 assessment module.

This is a transparent rules baseline: it reports only directly matched text as evidence,
never treats a reported TRL label as proof, and abstains when the checklist chain is incomplete.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Each criterion has evidence patterns intended to capture report content, not TRL labels.
# Alternatives are conservative phrases; all terms in a tuple must occur on the same page.
RULES: dict[str, tuple[tuple[str, ...], ...]] = {
    "APCTT-C01": (("scientific principle",), ("engineering principle",), ("underlying principle",), ("physical principle",), ("mechanism", "observed"), ("principles", "observations")),
    "APCTT-C02": (("intended application",), ("proposed application",), ("concept", "application"), ("technology is intended",), ("design concept",), ("how the", "will operate")),
    "APCTT-C03": (("proof of concept",), ("laboratory results", "demonstrate"), ("analytical results", "demonstrate"), ("critical function", "demonstrated"), ("feasibility", "experiment"), ("bench-scale", "demonstrated")),
    "APCTT-C04": (("tested in a laboratory",), ("laboratory environment", "tested"), ("laboratory testing",), ("lab-scale", "tested"), ("bench test", "laboratory")),
    "APCTT-C05": (("relevant environment", "tested"), ("simulated environment", "tested"), ("representative environment", "tested"), ("field conditions", "test"), ("environmentally representative", "tested")),
    "APCTT-C06": (("prototype", "relevant environment", "demonstrated"), ("prototype", "representative environment", "demonstrated"), ("representative model", "demonstrated"), ("subsystem prototype", "tested")),
    "APCTT-C07": (("operational environment", "prototype", "demonstrated"), ("actual operating environment", "prototype"), ("field demonstration", "prototype"), ("operational setting", "demonstrated")),
    "APCTT-C08": (("qualified", "requirements", "system"), ("qualification testing",), ("system qualification",), ("final system", "acceptance test"), ("completed system", "qualified")),
    "APCTT-C09": (("successfully deployed",), ("successful operation",), ("operational deployment",), ("in operation", "months"), ("routine operation",), ("operational use",)),
    "APCTT-C10": (("acceptance criteria", "met"), ("requirements", "met"), ("performance requirement", "achieved"), ("measured", "target", "achieved"), ("test results", "pass")),
    "APCTT-C11": (("test conditions",), ("test configuration",), ("test article",), ("prototype configuration",), ("experimental setup",), ("environment", "instrumentation", "test")),
    "APCTT-C12": (("assessment target",), ("the assessed", "component"), ("the assessed", "subsystem"), ("the assessed", "system"), ("target technology",), ("this technology", "tested")),
}

MAX_EVIDENCE_PER_CRITERION = 3
MAX_SPAN_CHARS = 360


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _positive_match(text: str, terms: tuple[str, ...]) -> tuple[int, int] | None:
    """Return a source range for a co-located, affirmative evidence phrase."""
    folded = text.casefold()
    positions = [folded.find(term.casefold()) for term in terms]
    if any(pos < 0 for pos in positions):
        return None
    start, end = min(positions), max(pos + len(term) for pos, term in zip(positions, terms))
    # Keep multi-part matches local enough to be a meaningful single-page quotation.
    if end - start > 700:
        return None
    excerpt = folded[max(0, start - 90):min(len(folded), end + 90)]
    # Avoid claiming a negated or explicitly failed test as positive support.
    if re.search(r"\b(not|never|no|failed|failure|unable to|did not|wasn't|were not)\b", excerpt):
        return None
    return max(0, start - 90), min(len(text), end + 90)


def _page_evidence(pages: list[tuple[str, int, str]], rules: tuple[tuple[str, ...], ...]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for document_id, page_number, text in pages:
        if not text.strip():
            continue
        for terms in rules:
            span_range = _positive_match(text, terms)
            if span_range is None:
                continue
            start, end = span_range
            span = _norm(text[start:end])
            if len(span) > MAX_SPAN_CHARS:
                span = span[:MAX_SPAN_CHARS].rsplit(" ", 1)[0]
            if span:
                found.append({"document_id": document_id, "page": page_number, "supporting_span": span})
                break
        if len(found) >= MAX_EVIDENCE_PER_CRITERION:
            break
    return found


class AssessmentModule:
    """Rules-based evidence locator; conservative TRL completion with explicit abstention."""

    def __init__(self) -> None:
        self._checklist: Mapping[str, Any] = {}
        self._criterion_ids: list[str] = []

    def load(self, config: Mapping[str, Any], asset_root: Path) -> None:
        del asset_root  # No model downloads or mutable assets are needed.
        self._checklist = config["checklist"]
        self._criterion_ids = [str(item["criterion_id"]) for item in self._checklist["criteria"]]

    def assess(self, case: Mapping[str, Any], context: Any) -> dict[str, Any]:
        from pypdf import PdfReader

        pages: list[tuple[str, int, str]] = []
        page_counts: dict[str, int] = {}
        for document in case["documents"]:
            doc_id = str(document["document_id"])
            page_counts[doc_id] = int(document["page_count"])
            reader = PdfReader(str(document["path"]))
            for page_no, page in enumerate(reader.pages, start=1):
                if context is not None and hasattr(context, "remaining_ms") and context.remaining_ms() < 1500:
                    return self._abstain(case, "insufficient evidence", "Time budget nearly exhausted during PDF extraction.")
                pages.append((doc_id, page_no, page.extract_text() or ""))

        statuses: dict[str, str] = {}
        evidence: dict[str, list[dict[str, Any]]] = {}
        missing: dict[str, str] = {}
        rationale: dict[str, str] = {}
        for cid in self._criterion_ids:
            rules = RULES.get(cid, ())
            refs = _page_evidence(pages, rules) if rules else []
            # Defend the result against corrupt or surprising PDF metadata.
            refs = [ref for ref in refs if 1 <= ref["page"] <= page_counts.get(ref["document_id"], 0)]
            evidence[cid] = refs
            statuses[cid] = "met" if refs else "insufficient evidence"
            if not refs:
                missing[cid] = "No affirmative, criterion-specific supporting passage was located in the extracted report text."
            else:
                first = refs[0]
                rationale[cid] = f"Matched a criterion-specific passage on {first['document_id']} page {first['page']}."

        result: dict[str, Any] = {
            "schema_version": case["schema_version"],
            "checklist_version": self._checklist["version"],
            "case_id": case["case_id"],
            "assessment_target": dict(case["assessment_target"]),
            "assessment_status": "abstained",
            "predicted_trl": None,
            "criterion_status": statuses,
            "evidence_reference": evidence,
            "missing_evidence": missing,
            "abstention_reason": "insufficient evidence",
            "error_code": None,
            "criterion_rationale": rationale,
        }
        # Completion requires a contiguous, directly evidenced TRL ladder and all three
        # cross-cutting checklist criteria. This trades coverage for defensibility.
        milestone_ids = [f"APCTT-C{i:02d}" for i in range(1, 10)]
        crosscut_ids = [f"APCTT-C{i:02d}" for i in range(10, 13)]
        required_ids = set(milestone_ids + crosscut_ids)
        if required_ids.issubset(statuses):
            contiguous = 0
            for trl, cid in enumerate(milestone_ids, start=1):
                if statuses[cid] != "met":
                    break
                contiguous = trl
            if contiguous and all(statuses[cid] == "met" for cid in crosscut_ids):
                result.update(
                    assessment_status="completed",
                    predicted_trl=contiguous,
                    abstention_reason=None,
                    trl_rationale=(
                        f"The directly evidenced checklist milestones form a contiguous chain through TRL {contiguous}; "
                        "criteria C10-C12 also have supporting report passages."
                    ),
                    confidence=0.62,
                    uncertainty={"kind": "trl_interval", "lower": max(1, contiguous - 1), "upper": contiguous},
                )
                # Completed outputs must not contain missing_evidence entries for met items.
                result["missing_evidence"] = {}
        return result

    def _abstain(self, case: Mapping[str, Any], reason: str, detail: str) -> dict[str, Any]:
        ids = self._criterion_ids
        return {
            "schema_version": case["schema_version"],
            "checklist_version": self._checklist["version"],
            "case_id": case["case_id"],
            "assessment_target": dict(case["assessment_target"]),
            "assessment_status": "abstained",
            "predicted_trl": None,
            "criterion_status": {cid: "insufficient evidence" for cid in ids},
            "evidence_reference": {cid: [] for cid in ids},
            "missing_evidence": {cid: detail for cid in ids},
            "abstention_reason": reason,
            "error_code": None,
        }

    def reset_case(self) -> None:
        """No per-case state is retained."""

    def close(self) -> None:
        """No resources need releasing."""
