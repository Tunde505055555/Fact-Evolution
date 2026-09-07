# Demo scenarios

Each scenario is a deterministic walkthrough of the contract's distinctive behaviour.
The scripted validator judgments live in `examples/scenarios.json` and are asserted by
`tests/test_contract_flow.py`, so the demos are executable, not prose:

```bash
python3 -m pytest tests/test_contract_flow.py -q
python3 scripts/demo_calls.py            # prints the exact Studio calldata per scenario
```

## 1. A fact that stays true

`submit_claim` → `evaluate_claim` → `reevaluate_claim` ×2 with consistent evidence.
Timeline: `VERIFIED 91 → VERIFIED 91 (UNCHANGED) → VERIFIED 99 (STRENGTHENED)`.
`get_evolution_score` → `STABLE`. Test: `test_scenario_stays_true_across_evaluations`.

## 2. A fact that becomes false

Second evaluation returns `reality_changed: true`, `change_cause: REALITY_CHANGED`.
Delta is `CHANGED` (not merely "wrong before"), status `REJECTED @93`, and the previous
`VERIFIED @91` belief remains queryable via `get_historical_state`.
Test: `test_scenario_fact_becomes_false`.

## 3. A fact that becomes uncertain because evidence went stale

No new evaluation at all — `apply_freshness_policy` runs 200 days after the last evaluation
on a 30-day claim. Confidence decays, `change_cause: SOURCE_OUTDATED`, status drops toward
`STALE`, and a `DECAY` event is appended. Test: `test_scenario_evidence_goes_stale`.

## 4. Two credible sources conflict

The evaluation returns supporting *and* contradicting evidence with mid confidence.
Result: `DISPUTED @48`, delta `CONTRADICTED`, `contradiction_reason` recorded — the contract
refuses to pick a winner. Test: `test_scenario_conflicting_sources_produce_disputed_not_false`.

## 5. Apparent contradiction caused by different dates

Same inputs as scenario 4 except the conflicting report refers to 2024 while the claim is
about now. Validators return `change_cause: APPARENT_ONLY` with a `temporal_scope`, and the
status stays `VERIFIED` — new information, not a changed fact.
Test: `test_scenario_apparent_contradiction_is_dated_not_a_change`.

## 6. New primary evidence raises confidence

`submit_evidence(claim_id, "https://primary.gov/filing", "official filing", true)` is
accepted as `NOVEL_INDEPENDENT_SOURCE` (weight 100) and triggers a re-evaluation with delta
`STRENGTHENED`. Resubmitting the identical item returns `DUPLICATE_EVIDENCE`.
Test: `test_evidence_novelty_and_reevaluation_trigger`.

## 7. A challenged claim is re-evaluated and resolved

`challenge_claim` preserves the original `VERIFIED` evaluation, runs a fresh independent one
that returns `REJECTED`, and `resolve_challenge` records `CHALLENGE_UPHELD` with both
evaluations intact. Re-resolving returns `ALREADY_RESOLVED`.
Test: `test_challenge_preserves_original_and_resolves`.

## Bonus: temporally related claims

```
A: "Company X currently operates in Nigeria."
B: "Company X closed its Nigerian operations in July 2026."
link_claims(B, A, "SUPERSEDES")
```

`get_related_claims(A)` reports `SUPERSEDED_BY B` with B's status and confidence, so the next
evaluation of A treats B as context. Test: `test_related_claims_are_bidirectional`.
