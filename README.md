# Fact Evolution — a decentralized temporal truth layer on GenLayer

Contract-only repository. No frontend app, no server-side AI gateway, no consensus
simulator — just the Intelligent Contract, its tests, docs, examples and tooling.

```
contracts/fact_evolution.py    the Intelligent Contract (single deployable file)
tools/check_schema.py          GenVM v0.2.16 schema-parser diagnostic
tests/                         deterministic + scripted-consensus test suite
examples/                      demo scenarios and calldata examples
scripts/                       lint + deploy tooling
docs/                          architecture, evolution engine, schema safety
```

## What it is

Most "AI fact checkers" answer *is this true?* once. Fact Evolution instead maintains,
per claim, an **immutable history of what the network believed, what changed, why it
changed, and how confident it is now**:

```
VERIFIED → STRENGTHENED → WEAKENED → DISPUTED → REJECTED
```

Every re-evaluation is compared with the stored belief, classified
(`UNCHANGED / STRENGTHENED / WEAKENED / CHANGED / CONTRADICTED / UNCERTAIN / RESOLVED`),
and appended to the claim's timeline. Nothing is ever overwritten or deleted.

The contract's core innovation is on chain, not in a UI:

- **Change detection** — reality changing is distinguished from evidence changing, from
  the claim's scope changing, from sources merely going stale (`change_cause`).
- **Temporal reasoning** — "currently", "as of", "no longer" are first-class; a claim true
  in the past is never assumed true today. Transitions are dated when datable.
- **Evidence graph** — sources, evidence, counter-evidence, past evaluations and
  transitions are linked; ten sites copying one wire report count as one source.
- **Contradiction engine** — credible disagreement yields `DISPUTED`/`UNCERTAIN`, with the
  reason recorded, instead of a coin-flip verdict.
- **Freshness policy + honest decay** — confidence decays only when evidence is overdue
  *relative to that claim's own recheck interval* and weak; stale claims become `STALE`.
- **Challenges** — anyone can challenge; a fresh independent evaluation runs and both the
  original and post-challenge evaluations are preserved with an explicit outcome.
- **Anti-manipulation** — evidence is fingerprinted; duplicates are rejected, same-domain
  submissions are down-weighted, novel independent sources get full weight.
- **No fake certainty** — insufficient evidence returns `UNCERTAIN`; if validators fail to
  converge under the equivalence principle, the contract reports `UNCERTAIN` rather than
  inventing a result.
- **Evolution score** — volatility (`STABLE / MODERATELY_CHANGING / HIGHLY_VOLATILE`)
  derived from the claim's real history.

## Public interface

Writes: `submit_claim`, `evaluate_claim`, `reevaluate_claim`, `submit_evidence`,
`challenge_claim`, `resolve_challenge`, `apply_freshness_policy`, `link_claims`.

Views: `get_current_state`, `get_snapshot`, `get_historical_state`, `get_timeline`,
`get_claim_metadata`, `get_related_claims`, `get_evidence_graph`, `get_evolution_score`,
`get_challenges`, `list_claims`, `get_stats`.

All entrypoints take only `str` / `int` / `bool` and return a JSON `str` envelope
(`{"ok": true, "data": ...}` or `{"ok": false, "error": ..., "message": ...}`).

## Quickstart

```bash
python3 tools/check_schema.py contracts/fact_evolution.py   # schema diagnostic
bash scripts/lint.sh                                        # schema + genvm lint (if installed)
python3 -m pytest tests -q                                  # 36 tests, no network
python3 scripts/deploy.py --studio http://localhost:4000     # deploy to GenLayer Studio
python3 scripts/demo_calls.py                                # print demo calldata
```

Then, against Studio:

```
submit_claim("Company X currently operates in Nigeria.", "africa", "company_status",
             "[\"https://example.gov.ng/registry/company-x\"]", 2592000)
evaluate_claim(<claim_id>)          # non-deterministic, validator consensus
reevaluate_claim(<claim_id>, "new evidence surfaced")
get_snapshot(<claim_id>) / get_timeline(<claim_id>)
```

## Docs

- [`docs/architecture.md`](docs/architecture.md) — storage model, consensus boundaries, gas notes
- [`docs/evolution-engine.md`](docs/evolution-engine.md) — delta classification, change causes, decay, evolution score
- [`docs/schema-safety.md`](docs/schema-safety.md) — schema-parser diagnosis and the v0.2.16 safe subset
- [`examples/demo_scenarios.md`](examples/demo_scenarios.md) — seven adversarial demo scenarios
