"""Nested walk-forward integrity for synthetic branch tests.

Dated real fold definitions are not approved. No iid fold confidence is emitted.
Reserved holdouts cannot be borrowed. Future observations cannot enter a decision.
"""

from __future__ import annotations

from .validate import parse_number


def assess(block: dict) -> dict:
    folds = block["folds"]
    reserved = set(block["reserved_holdout_ids"])
    if len(folds) != 4:
        return _blocked("FOLD_COUNT")
    seen_ids: set[str] = set()
    seen_times: set[int] = set()
    positive = 0
    qualifying = 0
    previous_test_end = None
    for index, fold in enumerate(folds):
        train_end = fold["train_end_t"]
        embargo_start = fold["embargo_start_t"]
        embargo_end = fold["embargo_end_t"]
        test_start = fold["test_start_t"]
        test_end = fold["test_end_t"]
        if not (
            embargo_start == train_end + 1
            and embargo_start < embargo_end
            and embargo_end == test_start
            and test_start < test_end
        ):
            return _blocked("EMBARGO")
        if index and (previous_test_end is None or train_end < previous_test_end - 1 or train_end >= test_start):
            return _blocked("NOT_NESTED")
        net = parse_number(fold["net"])
        if net > 0:
            positive += 1
        for trade in fold["oof"]:
            ident = trade["id"]
            stamp = trade["t"]
            if ident in reserved:
                return _blocked("RESERVED_BORROWED")
            if ident in seen_ids or stamp in seen_times:
                return _blocked("DUPLICATE_OOF")
            if stamp < test_start or stamp >= test_end or stamp <= train_end:
                return _blocked("LEAKAGE")
            seen_ids.add(ident)
            seen_times.add(stamp)
            qualifying += 1
        previous_test_end = test_end
    return {
        "status": "INTEGRITY_OK",
        "fold_count": 4,
        "positive_folds": positive,
        "qualifying_oof": qualifying,
        "definition_approval": "NOT_APPROVED",
        "real_dates": None,
        "iid_fold_pvalue": None,
        "fold_confidence_status": "NOT_TESTED",
        "coordinate_system": "SYNTHETIC_INDEX",
    }


def assess_causal(block: dict) -> dict:
    observations = {row["id"]: row for row in block["observations"]}
    accepted = []
    leakage = []
    for decision in block["decisions"]:
        used_ids = decision["used_observation_ids"]
        if not used_ids:
            leakage.append(decision["id"])
            continue
        used = []
        leaked = False
        for ident in used_ids:
            observation = observations.get(ident)
            if observation is None or observation["available_t"] > decision["decision_t"]:
                leaked = True
                break
            used.append(
                {
                    "id": ident,
                    "available_t": observation["available_t"],
                    "value": observation["value"],
                }
            )
        if leaked:
            leakage.append(decision["id"])
            continue
        accepted.append(
            {
                "id": decision["id"],
                "decision_t": decision["decision_t"],
                "used": used,
            }
        )
    return {
        "status": "LEAKAGE" if leakage else "NO_LEAKAGE",
        "leakage_ids": leakage,
        "accepted": accepted,
    }


def project_causal(assessment: dict, t: int) -> list[dict]:
    return [row for row in assessment["accepted"] if row["decision_t"] <= t]


def _blocked(reason: str) -> dict:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "positive_folds": None,
        "qualifying_oof": None,
        "definition_approval": "NOT_APPROVED",
        "real_dates": None,
        "iid_fold_pvalue": None,
        "fold_confidence_status": "NOT_TESTED",
    }


def positive_count(assessment: dict) -> int | None:
    if assessment["status"] != "INTEGRITY_OK":
        return None
    return assessment["positive_folds"]


def qualifying_count(assessment: dict) -> int | None:
    if assessment["status"] != "INTEGRITY_OK":
        return None
    return assessment["qualifying_oof"]


def fold_summary(assessment: dict) -> dict:
    """JSON-safe view. Fractions are already absent."""
    if assessment["status"] != "INTEGRITY_OK":
        return {
            "status": assessment["status"],
            "reason": assessment.get("reason"),
            "positive_folds": None,
            "qualifying_oof": None,
            "definition_approval": "NOT_APPROVED",
            "real_dates": None,
            "iid_fold_pvalue": None,
            "fold_confidence_status": "NOT_TESTED",
        }
    return {
        "status": "INTEGRITY_OK",
        "positive_folds": assessment["positive_folds"],
        "qualifying_oof": assessment["qualifying_oof"],
        "definition_approval": "NOT_APPROVED",
        "real_dates": None,
        "iid_fold_pvalue": None,
        "fold_confidence_status": "NOT_TESTED",
    }
