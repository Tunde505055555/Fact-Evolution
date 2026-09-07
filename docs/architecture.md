# Architecture

## Consensus boundaries

| concern | execution | why |
| --- | --- | --- |
| claim validation, ids, dedupe, enums, score ranges, timestamps | deterministic | data integrity must be identical on every validator |
| web retrieval + reasoning about evidence | non-deterministic (`gl.nondet.web.render`, `gl.nondet.exec_prompt`) | subjective interpretation belongs to validators |
| agreement between validators | `gl.eq_principle_prompt_comparative` with an explicit principle | status must match; confidences may differ within tolerance |
| delta classification, transitions, decay, evolution score | deterministic, post-consensus | the same judgment always yields the same history |

The equivalence principle (see `EQ_PRINCIPLE` in the contract) accepts wording differences
but rejects disagreement about status, about whether reality changed, or confidence gaps
beyond 15 (truth) / 20 (evidence) points. If validators cannot converge, the contract
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
