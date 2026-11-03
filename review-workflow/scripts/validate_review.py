#!/usr/bin/env python3
"""Validate review handoff structure, evidence-state invariants and cited artifacts, not bug correctness."""
import argparse
import json
from pathlib import Path
import sys


STRATEGIES = {"static", "hypothesis", "dodgy-diff", "intent-aware"}
ASSESSMENTS = ("pending", "bug", "expected_change", "test_or_environment_issue", "not_a_bug", "uncertain")
AUTHOR_RESPONSES = (None, "fixed", "wrong", "intended", "not_worth_it")
ARTIFACT_PREFIXES = ("experiments/", "snapshots/", "./", "../")
REPRODUCING_OUTCOMES = ("differential_failure", "head_only_failure")
VERIFICATION_STATUSES = ("not_requested", "pending", "unavailable", "applied")
VERDICTS = ("confirmed", "refuted", "uncertain")


def artifact_paths(finding, base):
    """Evidence entries naming run artifacts; other entries are free-text notes."""
    evidence = finding.get("evidence")
    for entry in evidence if isinstance(evidence, list) else []:
        if isinstance(entry, str) and (entry.startswith(ARTIFACT_PREFIXES) or Path(entry).is_absolute()):
            yield entry, (base / entry).resolve()


def experiment_result(path):
    for candidate in (path, *path.parents):
        if candidate.parent.name == "experiments" and (candidate / "result.json").is_file():
            return json.loads((candidate / "result.json").read_text(encoding="utf-8-sig"))
    return None


def check_files(data, base):
    """Cross-check cited artifacts against the run directories they came from."""
    errors = []
    run = base / "run.json"
    if run.is_file():
        run_id = json.loads(run.read_text(encoding="utf-8-sig")).get("run_id")
        if data.get("current_snapshot") != run_id:
            errors.append(f"current_snapshot must equal run_id {run_id!r} of the adjacent run.json")
    artifacts = data["verification"].get("artifacts") if isinstance(data.get("verification"), dict) else None
    for entry in artifacts if isinstance(artifacts, list) else []:
        if isinstance(entry, str) and not (base / entry).exists():
            errors.append(f"verification artifact does not exist: {entry}")
    for i, finding in enumerate(data.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        prefix = f"findings[{i}]"
        results = []
        for entry, path in artifact_paths(finding, base):
            if not path.exists():
                errors.append(f"{prefix}: evidence path does not exist: {entry}")
            elif (result := experiment_result(path)) is not None:
                results.append(result)
        if finding.get("evidence_level") == "reproduced":
            # Reproduction must point at a recorded differential run of the same strategy and snapshot.
            if not any(r.get("kind") == finding.get("strategy")
                       and r.get("outcome") in REPRODUCING_OUTCOMES
                       and r.get("run_id") == finding.get("verified_snapshot") for r in results):
                errors.append(f"{prefix}: reproduced evidence must cite an experiment of the same strategy "
                              f"with outcome {'/'.join(REPRODUCING_OUTCOMES)} on verified_snapshot")
    return errors


def validate(data, base=None):
    errors = []
    if not isinstance(data, dict):
        return ["Review must be an object"]

    def require(condition, message):
        if not condition:
            errors.append(message)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def texts(value):
        return isinstance(value, list) and all(text(v) for v in value)

    require(data.get("schema_version") == 3, "schema_version must be 3")
    require(data.get("level") in ("quick", "standard", "deep"), "level must be quick, standard or deep")
    for key in ("review_id", "scope", "current_snapshot"):
        require(text(data.get(key)), f"{key} must be nonempty")
    round_no, cap = data.get("round"), data.get("max_rounds")
    valid_rounds = all(type(v) is int and v > 0 for v in (round_no, cap))
    require(valid_rounds, "round/max_rounds must be positive integers")
    if valid_rounds:
        require(round_no <= cap, "Round cap exceeded: explicitly revise scope/budget before continuing")
    require(data.get("review_mode") in ("independent_context", "self_check"), "Invalid review_mode")
    require(data.get("status") in ("in_progress", "reported", "needs_human"), "Invalid review status")
    delivered = data.get("status") in ("reported", "needs_human")
    verification = data.get("verification")
    verdicts = {}
    require(isinstance(verification, dict) and verification.get("status") in VERIFICATION_STATUSES,
            "verification.status must be " + " / ".join(VERIFICATION_STATUSES))
    if isinstance(verification, dict):
        applied = verification.get("status") == "applied"
        # The mode reflects verifier conclusions actually merged into this record, not a verifier that was started.
        require((data.get("review_mode") == "independent_context") == applied,
                "review_mode=independent_context if and only if verification.status=applied")
        verdicts = verification.get("verdicts") or {}
        if applied:
            require(texts(verification.get("artifacts")) and bool(verification["artifacts"]),
                    "applied verification must list the saved verifier output in artifacts")
            require(isinstance(verdicts, dict) and bool(verdicts) and all(v in VERDICTS for v in verdicts.values()),
                    "applied verification needs verdicts {finding_id: " + "/".join(VERDICTS) + "}")
        else:
            require(not verdicts, "verdicts belong to an applied verification; rewrite the whole record when applying them")
        if not isinstance(verdicts, dict):
            verdicts = {}
    intent = data.get("intent")
    require(isinstance(intent, dict), "intent must be an object")
    if isinstance(intent, dict):
        for key in ("confirmed", "inferred", "unknown"):
            require(texts(intent.get(key)), f"intent.{key} must be a list of strings")
    require(isinstance(data.get("budget"), dict) and bool(data.get("budget")), "budget must be a nonempty object")
    require(texts(data.get("limitations")), "limitations must be a list of strings")
    require(texts(data.get("source_fix_suggestions", [])), "source_fix_suggestions must be a list of strings")
    strategies = data.get("strategies")
    require(isinstance(strategies, list) and bool(strategies), "strategies must be nonempty")
    if isinstance(strategies, list):
        for i, entry in enumerate(strategies):
            if not isinstance(entry, dict):
                errors.append(f"strategies[{i}] must be an object")
                continue
            require(entry.get("name") in STRATEGIES, f"strategies[{i}]: invalid name")
            require(entry.get("status") in ("completed", "partial", "skipped", "blocked"), f"strategies[{i}]: invalid status")
            require(text(entry.get("reason")), f"strategies[{i}]: reason required")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return errors + ["findings must be a list"]
    seen = set()
    ids = {f["id"] for f in findings if isinstance(f, dict) and isinstance(f.get("id"), str)}
    for fid in verdicts:
        require(fid in ids, f"verification.verdicts names unknown finding {fid!r}")
    unsettled = False
    for i, finding in enumerate(findings):
        prefix = f"findings[{i}]"
        if not isinstance(finding, dict):
            errors.append(prefix + " must be an object")
            continue
        for key in ("id", "title", "root_cause", "trigger", "impact", "expected_basis", "location", "counterevidence"):
            require(text(finding.get(key)), f"{prefix}.{key} must be nonempty")
        fid = finding.get("id")
        if isinstance(fid, str):
            require(fid not in seen, f"Duplicate finding ID: {fid}")
            seen.add(fid)
        require(finding.get("strategy") in STRATEGIES, f"{prefix}: invalid strategy")
        require(finding.get("origin") in ("introduced", "pre_existing", "unknown"), f"{prefix}: invalid origin")
        require(finding.get("severity") in (None, "P0", "P1", "P2", "P3"), f"{prefix}: invalid severity")
        level, assessment, status = (finding.get(k) for k in ("evidence_level", "assessment", "status"))
        require(level in ("hypothesis", "static_confirmed", "reproduced"), f"{prefix}: invalid evidence_level")
        require(assessment in ASSESSMENTS, f"{prefix}: invalid assessment")
        require(status in ("candidate", "open", "resolved", "dismissed", "blocked", "stale"), f"{prefix}: invalid status")
        require(texts(finding.get("evidence")), f"{prefix}: evidence must be a list of strings")
        response = finding.get("author_response")
        require(response in AUTHOR_RESPONSES, f"{prefix}: invalid author_response")
        require(finding.get("dismiss_cost") in (None, "low", "medium", "high"), f"{prefix}: invalid dismiss_cost")
        if response == "fixed":
            require(status in ("open", "stale", "resolved"), f"{prefix}: an author fix must be verified (open/stale until resolved)")
        if status in ("open", "resolved"):
            require(assessment == "bug", f"{prefix}: open/resolved finding must be assessed as bug")
            require(level in ("static_confirmed", "reproduced"), f"{prefix}: confirmed evidence required")
            require(finding.get("severity") is not None, f"{prefix}: confirmed bug needs severity")
        if status == "dismissed":
            # A confirmed bug may be dismissed only when the author accepts the risk.
            require(assessment in ("expected_change", "test_or_environment_issue", "not_a_bug")
                    or (assessment == "bug" and response == "not_worth_it"),
                    f"{prefix}: dismissal requires a concrete assessment")
        if status in ("open", "resolved", "dismissed"):
            require(bool(finding.get("evidence")), f"{prefix}: disposition requires evidence")
        if isinstance(fid, str) and verdicts.get(fid) == "refuted":
            require(status not in ("open", "resolved"), f"{prefix}: verifier refuted it; reassess before opening")
        if delivered:
            # A delivered record carries a conclusion for every finding; half-applied updates surface here.
            require(assessment != "pending", f"{prefix}: assess every finding before reporting")
            require(not (assessment == "bug" and status == "candidate"),
                    f"{prefix}: a bug assessment must be opened on current_snapshot, or reassessed as uncertain")
        related = finding.get("related", [])
        require(isinstance(related, list) and all(isinstance(r, str) and r in ids and r != fid for r in related),
                f"{prefix}: related must list other finding IDs")
        if status == "open":
            require(finding.get("verified_snapshot") == data.get("current_snapshot"), f"{prefix}: stale conclusion; revalidate or mark stale")
        history = finding.get("history")
        if status in ("resolved", "dismissed"):
            # Closed conclusions keep the snapshot they were reached on; reopen only on relevant change.
            snap = finding.get("verified_snapshot")
            require(text(snap) and isinstance(history, list)
                    and any(isinstance(e, dict) and e.get("snapshot") == snap for e in history),
                    f"{prefix}: closed finding must cite a verified snapshot recorded in history")
        require(isinstance(history, list) and bool(history), f"{prefix}: nonempty history required")
        if isinstance(history, list):
            for event in history:
                require(isinstance(event, dict) and all(text(event.get(k)) for k in ("snapshot", "action", "reason")), f"{prefix}: history entries need snapshot/action/reason")
                if isinstance(event, dict):
                    require(type(event.get("round")) is int and event["round"] > 0, f"{prefix}: history round must be positive")
        unsettled |= status in ("candidate", "open", "blocked", "stale")
    if valid_rounds and round_no == cap and unsettled and data.get("status") != "in_progress":
        require(data.get("status") == "needs_human", "Unsettled findings at round cap require needs_human")
    if base is not None:
        errors.extend(check_files(data, Path(base)))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review")
    args = parser.parse_args()
    try:
        path = Path(args.review).resolve()
        errors = validate(json.loads(path.read_text(encoding="utf-8-sig")), base=path.parent)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
