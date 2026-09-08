# The evolution engine

## 1. Evaluate

`evaluate_claim` / `reevaluate_claim` run one non-deterministic block per call:
up to four sources are fetched (`gl.nondet.web.render`, truncated to 4 KB each), the
claim, the *previous contract belief* and all accepted evidence go into the validator
prompt, and the model must answer with a single JSON object. Validators converge through
`gl.eq_principle_prompt_comparative`.

`sanitize_evaluation` then applies deterministic guards to the agreed judgment:

- scores clamped to 0..100, statuses mapped onto the contract vocabulary;
- `evidence_confidence < 35` can never yield `VERIFIED` or `REJECTED` → `UNCERTAIN`;
- supporting **and** contradicting evidence with truth < 70 → `DISPUTED`;
- `UNCERTAIN` caps truth confidence at 60;
- unparseable output degrades to `UNCERTAIN` @ 0 rather than an invented verdict.

## 2. Classify the delta

`classify_delta(previous, current)` — evaluated deterministically, in priority order:

| delta | condition |
| --- | --- |
| `INITIAL` | no previous belief |
| `CHANGED` | validators report `reality_changed` with cause `REALITY_CHANGED`, or the status moved for another reason |
| `CONTRADICTED` | `VERIFIED`↔`REJECTED` flip, or a confident status became `DISPUTED` |
| `UNCERTAIN` | a determinate status became `UNCERTAIN`/`STALE` |
| `RESOLVED` | `DISPUTED`/`UNCERTAIN`/`STALE` became `VERIFIED`/`REJECTED` |
| `STRENGTHENED` | same status, truth confidence up ≥ 8 |
| `WEAKENED` | truth confidence down ≥ 15 |
| `UNCHANGED` | none of the above |

Everything except `UNCHANGED` is *meaningful* and updates `last_meaningful_change`.

## 3. Explain the change

`transition_narrative` renders one auditable sentence per transition:

```
VERIFIED -> UNCERTAIN (UNCERTAIN). Reason: previously reliable sources are now outdated.
Truth confidence 45, evidence confidence 40, freshness 20. Evaluated at t=1770...
```

`change_cause` is the heart of change detection and is deliberately separate from the
delta:

| cause | meaning |
| --- | --- |
| `REALITY_CHANGED` | the world moved |
| `EVIDENCE_CHANGED` | only what we can see moved |
| `CLAIM_SCOPE_CHANGED` | wording/scope of the claim effectively shifted |
| `SOURCE_OUTDATED` | sources aged out |
| `APPARENT_ONLY` | conflict explained by different dates, jurisdictions or definitions |
| `NONE` | routine re-examination |

## 4. Append, never overwrite

Every call appends one event to `timelines[claim_id]`:
`SUBMITTED`, `EVALUATION`, `EVIDENCE`, `DECAY`, `CHALLENGE_OPENED`, `CHALLENGE_RESOLVED`,
`RELATION`. `get_historical_state(claim_id, t)` replays the timeline up to `t`, so the
contract can always answer "what did you believe on that date?" without mutating anything.

## 5. Freshness and honest decay

`apply_freshness_policy` is fully deterministic — no web, no LLM. It compares the age of
the current evaluation with the claim's own `recheck_interval` (defaulted per claim type:
1 day for sports outcomes, 60 days for regulatory status) and applies:

```
overdue_periods = (age - recheck) / recheck
weakness        = 1 + max(0, (60 - evidence_confidence) / 60)
penalty         = min(45, overdue_periods * 12 * weakness)
```

A claim within its interval never decays. An overdue claim with strong evidence decays
slowly; an overdue claim with weak evidence decays faster, and below 50 becomes `STALE`.

## 6. Evolution score

Derived from the claim's real evaluation history: status flip rate (×60) plus average
confidence swing (capped at 40), producing `STABLE` (<20), `MODERATELY_CHANGING` (<55) or
`HIGHLY_VOLATILE`. A single evaluation is `UNTESTED` — never mislabeled "stable".

## 7. Challenges

`challenge_claim` snapshots the current evaluation into the challenge record, optionally
files the attached evidence, then *always* runs a fresh independent evaluation.
`resolve_challenge` compares the snapshot with the post-challenge evaluation and records
`CHALLENGE_UPHELD`, `ORIGINAL_UPHELD` or `CHALLENGE_PARTIAL_UNRESOLVED`. Both evaluations
survive in the record and in the timeline.

## 8. Anti-manipulation

`submit_evidence` fingerprints `url|note` (SHA-256, truncated). Identical resubmission is
rejected outright (`DUPLICATE_EVIDENCE`); a new item from an already-used domain is accepted
at weight 40 (`NON_INDEPENDENT_SOURCE`); a genuinely new domain gets weight 100. Claim text
is also hashed, so the same claim cannot be forked to escape its own history.

## 9. Claim relationships

`link_claims(a, b, relation)` writes a bidirectional edge with the correct inverse
(`SUPERSEDES` ↔ `SUPERSEDED_BY`), so "Company X operates in Nigeria" and "Company X closed
its Nigerian operations in July 2026" are recognized as temporally related and potentially
contradictory rather than isolated statements. `get_related_claims` returns each neighbour's
current status and confidence for use in the next evaluation's context.
