#!/usr/bin/env python3
"""
Print the exact call sequence (method + arguments) for each demo scenario, ready to paste
into GenLayer Studio or to feed a deployment script. No network, no side effects.

Usage:  python3 scripts/demo_calls.py [scenario]
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "examples" / "scenarios.json").read_text())
CLAIM = DATA["claims"]["company_nigeria"]
EXIT_CLAIM = DATA["claims"]["company_exit"]

SCENARIOS = {
    "stays_true": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("reevaluate_claim", {"claim_id": "<claim_id>", "reason": "scheduled recheck"}),
        ("reevaluate_claim", {"claim_id": "<claim_id>", "reason": "scheduled recheck"}),
        ("get_evolution_score", {"claim_id": "<claim_id>"}),
    ],
    "becomes_false": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("reevaluate_claim", {"claim_id": "<claim_id>", "reason": "exit notice reported"}),
        ("get_timeline", {"claim_id": "<claim_id>"}),
    ],
    "goes_stale": [
        ("submit_claim", dict(CLAIM, recheck_interval_seconds=604800)),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("apply_freshness_policy", {"claim_id": "<claim_id>"}),
        ("get_snapshot", {"claim_id": "<claim_id>"}),
    ],
    "sources_conflict": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("reevaluate_claim", {"claim_id": "<claim_id>", "reason": "two outlets disagree"}),
        ("get_current_state", {"claim_id": "<claim_id>"}),
    ],
    "apparent_contradiction": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("submit_evidence", {"claim_id": "<claim_id>", "url": "https://archive.example/2024-closure", "note": "article claiming closure", "trigger_reevaluation": True}),
        ("get_evidence_graph", {"claim_id": "<claim_id>"}),
    ],
    "new_primary_evidence": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("submit_evidence", {"claim_id": "<claim_id>", "url": "https://primary.gov/filing", "note": "official 2026-04 filing", "trigger_reevaluation": True}),
        ("submit_evidence", {"claim_id": "<claim_id>", "url": "https://primary.gov/filing", "note": "official 2026-04 filing"}),
    ],
    "challenge": [
        ("submit_claim", CLAIM),
        ("evaluate_claim", {"claim_id": "<claim_id>"}),
        ("challenge_claim", {"claim_id": "<claim_id>", "argument": "The company published an exit notice in July 2026.", "evidence_url": "https://example.gov.ng/notices/company-x-exit"}),
        ("resolve_challenge", {"claim_id": "<claim_id>", "challenge_id": "<challenge_id>"}),
        ("get_challenges", {"claim_id": "<claim_id>"}),
    ],
    "related_claims": [
        ("submit_claim", CLAIM),
        ("submit_claim", EXIT_CLAIM),
        ("link_claims", {"claim_id_a": "<claim_b_id>", "claim_id_b": "<claim_a_id>", "relation": "SUPERSEDES"}),
        ("get_related_claims", {"claim_id": "<claim_a_id>"}),
    ],
}


def render(name: str) -> None:
    print(f"\n# scenario: {name}")
    for method, args in SCENARIOS[name]:
        rendered = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
        print(f"{method}({rendered})")


def main() -> int:
    wanted = sys.argv[1:] or list(SCENARIOS)
    unknown = [w for w in wanted if w not in SCENARIOS]
    if unknown:
        print(f"unknown scenario(s): {unknown}\navailable: {list(SCENARIOS)}")
        return 1
    print("Fact Evolution — demo call sequences (replace <claim_id> with the id returned by submit_claim)")
    for name in wanted:
        render(name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
