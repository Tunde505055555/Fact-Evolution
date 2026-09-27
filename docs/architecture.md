# Architecture

## Consensus boundaries

| concern | execution | why |
| --- | --- | --- |
| claim validation, ids, dedupe, enums, score ranges, timestamps | deterministic | data integrity must be identical on every validator |
| web retrieval + reasoning about evidence | non-deterministic (`gl.nondet.web.render`, `gl.nondet.exec_prompt`) | subjective interpretation belongs to validators |
| agreement between validators | `gl.eq_principle_prompt_comparative` over *canonicalized* records | every field that can move the history must match exactly |
| delta classification, transitions, decay, evolution score | deterministic, post-consensus | the same judgment always yields the same history |

Each validator's leaf canonicalizes its own judgment (`sanitize_evaluation`) *before*
comparison, so validators compare the exact record that would be persisted, never the raw
model numbers. Scores are quantized onto a fixed grid (multiples of 10, `canonical_score`),
and the equivalence principle (see `EQ_PRINCIPLE`) requires `status`, all five scores,
`reality_changed`, `change_cause` and `transition_datable` to be **exactly** equal; only
wording, ordering and evidence phrasing may differ. There is no numeric tolerance, because
a tolerance wider than a transition threshold would let two mutually accepted outputs write
different histories (e.g. 65 vs 80 against a prior 80 → `WEAKENED` vs `UNCHANGED`).
Every state-transition threshold reads only canonical values, and the thresholds fall
strictly between grid points, so an accepted evaluation has exactly one possible history. If validators cannot converge, the contract
records `UNCERTAIN` with an explanation — it never manufactures certainty, and appeals
happen through GenLayer's own consensus machinery.

Nothing outside the contract may decide truth: all state transitions happen inside
`@gl.public.write` methods, callers only supply claim text, evidence references and
triggers. Submitter and challenger addresses come from `gl.message.sender_address`.

## Storage model

```
claims      TreeMap[str, str]              claim_id -> claim metadata JSON
states      TreeMap[str, str]              claim_id -> current evaluation JSON
timelines   TreeMap[str, DynArray[str]]    claim_id -> append-only event JSON
evidence    TreeMap[str, DynArray[str]]    claim_id -> evidence records JSON
challenges  TreeMap[str, DynArray[str]]    claim_id -> challenge records JSON
relations   TreeMap[str, DynArray[str]]    claim_id -> relation records JSON
claim_ids   DynArray[str]                  enumeration
text_index  TreeMap[str, str]              sha256(text) -> claim_id (dedupe)
logical_clock, total_evaluations : u256
```

`timelines` is strictly append-only: `_append_event` is the only writer and there is no
delete or in-place edit path. `states` holds the *current* belief; every past belief remains
retrievable from the timeline, which is what makes `get_historical_state` truthful rather
than reconstructed.

`challenges[i]` is rewritten exactly once — from `OPEN` to `RESOLVED` — and that record
embeds both the original evaluation (captured at open time) and the post-challenge
evaluation, so neither is lost.

## Time

`_now()` prefers the consensus block timestamp and otherwise falls back to a monotonic
on-chain logical clock, guaranteeing strictly increasing, deterministic ordering of events
in every environment (including Studio, where block time may not be exposed). Timestamps
are stored as integers; no wall-clock calls happen inside non-deterministic blocks.

## Gas and size discipline

- Only digests and short references are stored: evidence text is capped and also hashed
  (`note_hash`), page content never lands on chain.
- At most 4 URLs are fetched per evaluation and each page is truncated to 4 KB before the
  prompt, capping non-deterministic cost.
- Evaluations bound every list they persist (≤6 supporting / ≤6 contradicting / ≤8 sources)
  and every string field (≤400/1200 chars).
- Reads are paginated (`list_claims(offset, limit≤100)`) and views never write.
- No unnecessary personal data is accepted or stored — the ABI has no fields for it.

## Privacy

Claims and evidence are public by construction. Submitters are recorded as addresses only.
Any bulky or sensitive artifact should be referenced by URL plus fingerprint, which is what
`submit_evidence` stores.

## Composability

Every read returns a stable JSON envelope, so other Intelligent Contracts, agents,
prediction markets and DAOs can consume `get_snapshot` / `get_current_state` /
`get_evolution_score` as a shared, evolving truth feed, and `get_historical_state` to settle
"what was known at time T" disputes.

## Validator binding of all consequential fields

Agreement uses `gl.eq_principle.strict_eq`: every validator independently
fetches sources, runs the model, canonicalizes, and must emit a byte-identical
record over `BOUND_FIELDS` — status, the five canonical scores,
`reality_changed`, `change_cause`, `temporal_scope`, `became_true_at`,
`stopped_being_true_at`, `transition_datable`.

- `temporal_scope` is a closed enum (CURRENT, PAST_WINDOW, FUTURE, TIMELESS, UNSPECIFIED).
- Transition dates are canonicalized to `YYYY-MM` and kept only if that
  year-month appears in the validator's own retrieved source text
  (user notes cannot attest a date). Inconsistent pairs are dropped.
  `transition_datable` is derived from the surviving dates, never taken from the model.
- Free text (explanation, evidence snippets, source lists, contradiction
  reasons) is discarded: it is never stored, shown as belief, or replayed.
  The next reevaluation prompt receives only bound fields plus `evaluated_at`.
- The consensus-failure fallback is a constant UNCERTAIN record.
