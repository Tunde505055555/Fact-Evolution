# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Fact Evolution — a decentralized temporal truth layer for GenLayer.

An Intelligent Contract that tracks real-world claims as they evolve:
it evaluates a claim with independent validator reasoning over live web
evidence, then on every re-evaluation compares the new judgment with the
stored one, classifies *what actually changed* (reality vs. evidence vs.
wording vs. staleness), and appends an immutable event to the claim's
timeline. Historical evaluations are never mutated.

Schema safety (GenVM v0.2.16)
-----------------------------
Every public entrypoint uses only primitive, calldata-native types
(`str`, `int`, `bool`) with literal defaults, and returns `str`
boundary, and persistent storage is a single scalar `str` field holding one
JSON document. This is what keeps the schema loader deterministic and the
contract loadable in GenLayer Studio. See `docs/schema-safety.md`.
"""

import json
import typing

from genlayer import *
import genlayer as _genlayer_module

# `genlayer` v0.2.x exports a `gl` proxy through the star import; newer runners
# expose the same surface as the module itself. Resolve both so the module
# imports cleanly under whichever runner the node pins — an import-time
# AttributeError here is what makes a node report "cannot load contract schema".
try:  # pragma: no cover - depends on runner version
    gl  # type: ignore[used-before-def]  # noqa: B018
except NameError:  # pragma: no cover
    gl = _genlayer_module  # type: ignore[assignment]

Contract = getattr(gl, "Contract", None)
if Contract is None:  # pragma: no cover - newer runner layout
    Contract = gl.contract.Contract  # type: ignore[attr-defined]

try:  # pragma: no cover - hashlib presence depends on the runner's stdlib
    import hashlib as _hashlib
except Exception:  # pragma: no cover
    _hashlib = None  # type: ignore[assignment]


def digest_hex(text: str) -> str:
    """Deterministic content digest (sha256 when available, pure-python otherwise)."""
    data = str(text).encode("utf-8")
    if _hashlib is not None:
        return _hashlib.sha256(data).hexdigest()
    # FNV-1a 128-bit fallback: no imports, fully deterministic across validators.
    h = 0x6C62272E07BB014262B821756295C58D
    prime = 0x0000000001000000000000000000013B
    mask = (1 << 128) - 1
    for byte in data:
        h = ((h ^ byte) * prime) & mask
    return format(h, "032x") + format((h * prime) & mask, "032x")



def raw_message_field(name: str) -> str:
    """Read a raw transaction message field across runner versions."""
    raw = getattr(gl, "message_raw", None)
    if raw is None:
        raw = getattr(getattr(gl, "message", None), "raw", None)
    if raw is None:
        return ""
    try:
        return str(raw[name])
    except Exception:
        return ""


def parse_iso_seconds(value: str) -> int:
    """ISO-8601 datetime -> unix seconds, using integer arithmetic only.

    The runtime exposes the transaction time as a string; parsing it here keeps
    the contract free of `datetime` and of any host-dependent clock.
    """
    text = str(value or "").strip()
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        elif digits and len(digits) >= 14:
            break
        elif not ch.isdigit() and len(digits) < 14 and ch not in "-T:+. Z/":
            return 0
    if len(digits) < 14:
        return 0
    year = int(digits[0:4])
    month = int(digits[4:6])
    day = int(digits[6:8])
    hour = int(digits[8:10])
    minute = int(digits[10:12])
    second = int(digits[12:14])
    if not (1970 <= year <= 4000 and 1 <= month <= 12 and 1 <= day <= 31):
        return 0
    # days-from-civil (Howard Hinnant's algorithm), pure integer maths
    y = year - (1 if month <= 2 else 0)
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hour * 3600 + minute * 60 + second


# ---------------------------------------------------------------------------
# Domain constants (deterministic vocabulary — validated on chain)
# ---------------------------------------------------------------------------

STATUSES = (
    "PENDING",
    "VERIFIED",
    "CHANGED",
    "UNCERTAIN",
    "DISPUTED",
    "REJECTED",
    "STALE",
)

DELTAS = (
    "INITIAL",
    "UNCHANGED",
    "STRENGTHENED",
    "WEAKENED",
    "CHANGED",
    "CONTRADICTED",
    "UNCERTAIN",
    "RESOLVED",
)

CHANGE_CAUSES = (
    "REALITY_CHANGED",
    "EVIDENCE_CHANGED",
    "CLAIM_SCOPE_CHANGED",
    "SOURCE_OUTDATED",
    "APPARENT_ONLY",
    "NONE",
)

CLAIM_TYPES = (
    "company_status",
    "product_availability",
    "policy_status",
    "event_status",
    "public_announcement",
    "regulatory_status",
    "sports_outcome",
    "market_condition",
    "organizational_status",
    "general",
)

RELATIONS = (
    "SUPERSEDES",
    "SUPERSEDED_BY",
    "TEMPORAL_SUCCESSOR",
    "POTENTIALLY_CONTRADICTS",
    "SUPPORTS",
    "REFINES",
)

DAY = 86400
DEFAULT_RECHECK = 30 * DAY

# Per-type default freshness requirement (seconds) — fast-moving facts must be
# re-verified more often than structural ones.
TYPE_RECHECK = {
    "sports_outcome": 1 * DAY,
    "event_status": 2 * DAY,
    "market_condition": 3 * DAY,
    "product_availability": 7 * DAY,
    "public_announcement": 14 * DAY,
    "company_status": 30 * DAY,
    "organizational_status": 30 * DAY,
    "policy_status": 60 * DAY,
    "regulatory_status": 60 * DAY,
    "general": 30 * DAY,
}

EQ_PRINCIPLE = (
    "The two evaluations agree if: (1) the `status` field is identical, "
    "(2) `truth_confidence` values differ by at most 15 points, "
    "(3) `evidence_confidence` values differ by at most 20 points, "
    "(4) they do not disagree about whether the real-world situation changed, "
    "and (5) neither claims certainty that the other calls unresolved. "
    "Differences in wording, ordering of evidence, or phrasing of the "
    "explanation are irrelevant."
)


# ---------------------------------------------------------------------------
# Deterministic pure helpers (no chain access — unit-testable off chain)
# ---------------------------------------------------------------------------


def clamp_score(value: typing.Any) -> int:
    """Coerce any model output into an integer 0..100."""
    try:
        n = int(round(float(value)))
    except Exception:
        return 0
    if n < 0:
        return 0
    if n > 100:
        return 100
    return n


def normalize_status(value: typing.Any) -> str:
    s = str(value or "").strip().upper()
    if s in STATUSES and s != "PENDING":
        return s
    if s in ("TRUE", "SUPPORTED", "CONFIRMED"):
        return "VERIFIED"
    if s in ("FALSE", "REFUTED", "CONTRADICTED"):
        return "REJECTED"
    if s in ("UNKNOWN", "INSUFFICIENT", "INSUFFICIENT_EVIDENCE"):
        return "UNCERTAIN"
    if s in ("CONFLICTING", "CONFLICT"):
        return "DISPUTED"
    if s in ("OUTDATED", "EXPIRED"):
        return "STALE"
    return "UNCERTAIN"


def normalize_cause(value: typing.Any) -> str:
    s = str(value or "").strip().upper()
    return s if s in CHANGE_CAUSES else "NONE"


def normalize_claim_type(value: typing.Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in CLAIM_TYPES else "general"


def domain_of(url: str) -> str:
    u = str(url).strip().lower()
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix) :]
    if u.startswith("www."):
        u = u[4:]
    return u.split("/")[0]


def evidence_fingerprint(text: str) -> str:
    """Content hash — large evidence stays off chain, only the digest lands."""
    norm = " ".join(str(text).lower().split())
    return digest_hex(norm)[:32]


def independent_source_count(urls: typing.Any) -> int:
    """Ten sites copying one wire report are not ten confirmations."""
    seen = []
    for u in list(urls or []):
        d = domain_of(str(u))
        if d and d not in seen:
            seen.append(d)
    return len(seen)


def extract_json_object(raw: typing.Any) -> str:
    """Pull the first balanced JSON object out of an LLM response."""
    text = str(raw or "")
    start = text.find("{")
    if start < 0:
        return "{}"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    json.loads(candidate)
                except Exception:
                    return "{}"
                return candidate
    return "{}"


def sanitize_evaluation(raw_json: str) -> dict:
    """Deterministic validation of the non-deterministic judgment."""
    try:
        data = json.loads(raw_json)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    def strs(key: str, limit: int) -> list:
        items = data.get(key) or []
        if isinstance(items, str):
            items = [items]
        out = []
        for it in list(items)[:limit]:
            s = str(it).strip()
            if s:
                out.append(s[:400])
        return out

    status = normalize_status(data.get("status"))
    truth = clamp_score(data.get("truth_confidence"))
    evidence = clamp_score(data.get("evidence_confidence"))
    source_quality = clamp_score(data.get("source_quality"))
    freshness = clamp_score(data.get("freshness"))
    independence = clamp_score(data.get("independence"))

    supporting = strs("supporting_evidence", 6)
    contradicting = strs("contradicting_evidence", 6)
    sources = strs("sources_used", 8)

    # Epistemic honesty guards: never let confident wording outrun evidence.
    if evidence < 35 and status in ("VERIFIED", "REJECTED"):
        status = "UNCERTAIN"
    if contradicting and supporting and status == "VERIFIED" and truth < 70:
        status = "DISPUTED"
    if status == "UNCERTAIN":
        truth = min(truth, 60)

    return {
        "status": status,
        "truth_confidence": truth,
        "evidence_confidence": evidence,
        "source_quality": source_quality,
        "freshness": freshness,
        "independence": independence,
        "independent_sources": independent_source_count(sources),
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "sources_used": sources,
        "reality_changed": bool(data.get("reality_changed")),
        "change_cause": normalize_cause(data.get("change_cause")),
        "became_true_at": str(data.get("became_true_at") or "")[:64],
        "stopped_being_true_at": str(data.get("stopped_being_true_at") or "")[:64],
        "transition_datable": bool(data.get("transition_datable")),
        "temporal_scope": str(data.get("temporal_scope") or "unspecified")[:120],
        "contradiction_reason": str(data.get("contradiction_reason") or "")[:400],
        "missing_context": str(data.get("missing_context") or "")[:400],
        "explanation": str(data.get("explanation") or "No explanation returned.")[:1200],
    }


def classify_delta(previous: dict, current: dict) -> str:
    """Compare stored belief with the fresh judgment (see docs/evolution.md)."""
    if not previous:
        return "INITIAL"

    prev_status = str(previous.get("status", "PENDING"))
    new_status = str(current.get("status", "UNCERTAIN"))
    prev_truth = int(previous.get("truth_confidence", 0))
    new_truth = int(current.get("truth_confidence", 0))
    delta_truth = new_truth - prev_truth

    if current.get("reality_changed") and current.get("change_cause") == "REALITY_CHANGED":
        return "CHANGED"
    if new_status == "DISPUTED" and prev_status in ("VERIFIED", "REJECTED"):
        return "CONTRADICTED"
    if prev_status == "VERIFIED" and new_status == "REJECTED":
        return "CONTRADICTED"
    if prev_status == "REJECTED" and new_status == "VERIFIED":
        return "CONTRADICTED"
    if new_status in ("UNCERTAIN", "STALE") and prev_status not in ("UNCERTAIN", "STALE", "PENDING"):
        return "UNCERTAIN"
    if prev_status in ("DISPUTED", "UNCERTAIN", "STALE") and new_status in ("VERIFIED", "REJECTED"):
        return "RESOLVED"
    if delta_truth >= 8 and new_status == prev_status:
        return "STRENGTHENED"
    if delta_truth <= -15:
        return "WEAKENED"
    if new_status != prev_status:
        return "CHANGED"
    return "UNCHANGED"


def is_meaningful(delta: str) -> bool:
    return delta not in ("UNCHANGED",)


def transition_narrative(prev_status: str, current: dict, delta: str, timestamp: int) -> str:
    """`WHY DID IT CHANGE?` — one auditable sentence per transition."""
    new_status = str(current.get("status"))
    cause = str(current.get("change_cause", "NONE"))
    reason = {
        "REALITY_CHANGED": "the underlying real-world situation changed",
        "EVIDENCE_CHANGED": "the available evidence changed while reality may not have",
        "CLAIM_SCOPE_CHANGED": "the effective scope/wording of the claim shifted",
        "SOURCE_OUTDATED": "previously reliable sources are now outdated",
        "APPARENT_ONLY": "the conflict is apparent only (different dates, jurisdictions or definitions)",
        "NONE": "the evidence base was re-examined",
    }.get(cause, "the evidence base was re-examined")
    head = f"{prev_status} -> {new_status} ({delta})."
    return (
        f"{head} Reason: {reason}. Truth confidence {current.get('truth_confidence')}, "
        f"evidence confidence {current.get('evidence_confidence')}, freshness "
        f"{current.get('freshness')}. Evaluated at t={timestamp}."
    )


def decayed_confidence(truth: int, age: int, recheck: int, evidence_conf: int) -> int:
    """Time alone never decays a claim — age *relative to its own freshness
    requirement*, combined with weak evidence, does."""
    if recheck <= 0 or age <= recheck:
        return int(truth)
    overdue_periods = (age - recheck) / float(recheck)
    weakness = 1.0 + max(0.0, (60 - int(evidence_conf)) / 60.0)
    penalty = min(45.0, overdue_periods * 12.0 * weakness)
    return max(0, int(round(int(truth) - penalty)))


def evolution_score(timeline: list) -> dict:
    """Volatility derived from the real history, not an arbitrary constant."""
    events = [e for e in timeline if e.get("kind") == "EVALUATION"]
    n = len(events)
    if n <= 1:
        return {"score": 0, "label": "UNTESTED", "evaluations": n, "meaningful_changes": 0}

    status_flips = 0
    total_swing = 0
    meaningful = 0
    for i in range(1, n):
        if events[i].get("status") != events[i - 1].get("status"):
            status_flips += 1
        total_swing += abs(
            int(events[i].get("truth_confidence", 0)) - int(events[i - 1].get("truth_confidence", 0))
        )
        if is_meaningful(str(events[i].get("delta", "UNCHANGED"))):
            meaningful += 1

    transitions = n - 1
    flip_rate = status_flips / float(transitions)
    avg_swing = total_swing / float(transitions)
    score = int(round(min(100.0, flip_rate * 60.0 + min(avg_swing, 40.0))))
    if score < 20:
        label = "STABLE"
    elif score < 55:
        label = "MODERATELY_CHANGING"
    else:
        label = "HIGHLY_VOLATILE"
    return {
        "score": score,
        "label": label,
        "evaluations": n,
        "status_flips": status_flips,
        "meaningful_changes": meaningful,
        "avg_confidence_swing": int(round(avg_swing)),
    }


def novelty_verdict(fingerprint: str, domain: str, known_prints: list, known_domains: list) -> dict:
    """Anti-manipulation: resubmitted or non-independent evidence carries no weight."""
    if fingerprint in known_prints:
        return {"accepted": False, "weight": 0, "reason": "DUPLICATE_EVIDENCE"}
    if domain and domain in known_domains:
        return {"accepted": True, "weight": 40, "reason": "NON_INDEPENDENT_SOURCE"}
    return {"accepted": True, "weight": 100, "reason": "NOVEL_INDEPENDENT_SOURCE"}


def build_prompt(claim: dict, previous: dict, evidence: list, context: str, mode: str) -> str:
    prev_block = "None. This is the first evaluation."
    if previous:
        prev_block = json.dumps(
            {
                "status": previous.get("status"),
                "truth_confidence": previous.get("truth_confidence"),
                "evidence_confidence": previous.get("evidence_confidence"),
                "evaluated_at": previous.get("evaluated_at"),
                "temporal_scope": previous.get("temporal_scope"),
                "explanation": previous.get("explanation"),
            }
        )
    ev_block = json.dumps(evidence[-8:]) if evidence else "[]"
    return f"""You are an independent validator in a decentralized temporal truth layer.
Judge ONE claim from evidence. You are rewarded for calibrated honesty, never for confidence.

CLAIM: {claim.get('text')}
CLAIM_TYPE: {claim.get('claim_type')}
CATEGORY: {claim.get('category')}
SUBMITTED_AT (unix): {claim.get('created_at')}
FRESHNESS_REQUIREMENT_SECONDS: {claim.get('recheck_interval')}
EVALUATION_MODE: {mode}

PREVIOUS_CONTRACT_BELIEF: {prev_block}
USER_SUBMITTED_EVIDENCE: {ev_block}

RETRIEVED_SOURCE_TEXT (may be truncated or empty):
{context[:12000]}

Reason through, in order:
1. TEMPORAL SCOPE: does the claim assert something about now, a past window, or the future?
   A claim true in the past is not automatically true today. Identify when it became true and,
   if applicable, when it stopped being true, and whether that transition can be confidently dated.
2. EVIDENCE: does each source *directly* establish the claim, or merely mention it? Prefer primary
   sources, but do not trust a source merely because it is official.
3. INDEPENDENCE: do sources trace back to one original report? Copies are not confirmations.
4. FRESHNESS: how old is the newest source relative to the freshness requirement?
5. CONTRADICTIONS: if credible sources disagree, state exactly what each asserts, their dates,
   whether they cover different periods / jurisdictions / definitions, and whether one supersedes
   the other. If it cannot be responsibly resolved, use DISPUTED or UNCERTAIN.
6. CHANGE DETECTION (only if a previous belief exists): distinguish reality changing from evidence
   changing, from the claim's scope changing, from sources merely going stale.
7. MISSING CONTEXT: name what you would need to be confident.

If the evidence is insufficient, return UNCERTAIN. Never invent facts, dates, or sources.

Reply with ONE JSON object and nothing else:
{{"status": "VERIFIED|CHANGED|UNCERTAIN|DISPUTED|REJECTED|STALE",
 "truth_confidence": 0-100,
 "evidence_confidence": 0-100,
 "source_quality": 0-100,
 "freshness": 0-100,
 "independence": 0-100,
 "supporting_evidence": ["short factual statement + source"],
 "contradicting_evidence": ["short factual statement + source"],
 "sources_used": ["url or publisher"],
 "reality_changed": true|false,
 "change_cause": "REALITY_CHANGED|EVIDENCE_CHANGED|CLAIM_SCOPE_CHANGED|SOURCE_OUTDATED|APPARENT_ONLY|NONE",
 "became_true_at": "ISO date or empty",
 "stopped_being_true_at": "ISO date or empty",
 "transition_datable": true|false,
 "temporal_scope": "short description",
 "contradiction_reason": "why credible sources disagree, or empty",
 "missing_context": "what is missing, or empty",
 "explanation": "2-4 sentences of calibrated reasoning"}}"""


def ok(payload: dict) -> str:
    return json.dumps({"ok": True, "data": payload})


def err(code: str, message: str) -> str:
    return json.dumps({"ok": False, "error": code, "message": message})


# ---------------------------------------------------------------------------
# Storage document
#
# GenLayer's schema loader is only reliably able to build the persistent
# schema for *scalar* storage fields. Nested containers
# (`TreeMap[str, DynArray[str]]` and friends) are what makes Studio report
# "could not load contract schema". So the whole domain model lives in a
# single `str` storage slot holding one JSON document, and the small
# dict/list views below give the contract code the same ergonomics with
# write-through persistence.
# ---------------------------------------------------------------------------

DOC_TABLES = ("claims", "states", "timelines", "evidence", "challenges", "relations", "text_index")


def empty_document() -> dict:
    doc: dict = {"claim_ids": [], "logical_clock": 0, "total_evaluations": 0}
    for name in DOC_TABLES:
        doc[name] = {}
    return doc


class _Rows:
    """A list column inside the JSON document; every mutation persists."""

    def __init__(self, owner, rows: list) -> None:
        self._owner = owner
        self._rows = rows

    def append(self, value) -> None:
        self._rows.append(value)
        self._owner._save()

    def __setitem__(self, index, value) -> None:
        self._rows[index] = value
        self._owner._save()

    def __getitem__(self, index):
        return self._rows[index]

    def __iter__(self):
        return iter(self._rows)

    def __len__(self) -> int:
        return len(self._rows)


class _Table:
    """A mapping column inside the JSON document; every mutation persists."""

    def __init__(self, owner, doc: dict, name: str) -> None:
        self._owner = owner
        self._data = doc[name]

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value
        self._owner._save()

    def get_or_insert_default(self, key: str) -> _Rows:
        if key not in self._data:
            self._data[key] = []
            self._owner._save()
        return _Rows(self._owner, self._data[key])


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class FactEvolution(Contract):
    # A single scalar storage field: the JSON document described above.
    state_json: str

    def __init__(self) -> None:
        self.state_json = json.dumps(empty_document())

    # -- internal: document access ---------------------------------------

    def _doc(self) -> dict:
        try:
            doc = json.loads(self.state_json)
        except Exception:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        base = empty_document()
        for key in base:
            if key not in doc:
                doc[key] = base[key]
        self._cache = doc
        return doc

    def _save(self) -> None:
        self.state_json = json.dumps(self._cache)

    def _table(self, name: str) -> _Table:
        return _Table(self, self._doc(), name)

    @property
    def claims(self) -> _Table:
        return self._table("claims")

    @property
    def states(self) -> _Table:
        return self._table("states")

    @property
    def timelines(self) -> _Table:
        return self._table("timelines")

    @property
    def evidence(self) -> _Table:
        return self._table("evidence")

    @property
    def challenges(self) -> _Table:
        return self._table("challenges")

    @property
    def relations(self) -> _Table:
        return self._table("relations")

    @property
    def text_index(self) -> _Table:
        return self._table("text_index")

    @property
    def claim_ids(self) -> _Rows:
        doc = self._doc()
        return _Rows(self, doc["claim_ids"])

    @property
    def logical_clock(self) -> int:
        return int(self._doc().get("logical_clock", 0))

    @property
    def total_evaluations(self) -> int:
        return int(self._doc().get("total_evaluations", 0))

    def _set_counter(self, name: str, value: int) -> None:
        doc = self._doc()
        doc[name] = int(value)
        self._save()

    # -- internal: time, ids, storage ------------------------------------

    def _now(self) -> int:
        """Consensus transaction time (unix seconds) with a monotonic fallback."""
        stamp = parse_iso_seconds(raw_message_field("datetime"))
        clock = self.logical_clock
        if stamp <= clock:
            stamp = clock + 1
        self._set_counter("logical_clock", stamp)
        return stamp

    def _sender(self) -> str:
        try:
            return str(gl.message.sender_address)
        except Exception:
            return "unknown"

    def _rows(self, table, claim_id: str) -> list:
        """Read a list column without creating storage (safe inside views)."""
        rows = table.get(claim_id, None)
        if rows is None:
            return []
        return list(rows)



    def _claim(self, claim_id: str) -> dict:
        raw = self.claims.get(claim_id, "")
        if not raw:
            return {}
        return json.loads(raw)

    def _state(self, claim_id: str) -> dict:
        raw = self.states.get(claim_id, "")
        if not raw:
            return {}
        return json.loads(raw)

    def _timeline(self, claim_id: str) -> list:
        return [json.loads(e) for e in self._rows(self.timelines, claim_id)]

    def _evidence(self, claim_id: str) -> list:
        return [json.loads(e) for e in self._rows(self.evidence, claim_id)]

    def _challenges(self, claim_id: str) -> list:
        return [json.loads(e) for e in self._rows(self.challenges, claim_id)]

    def _relations(self, claim_id: str) -> list:
        return [json.loads(e) for e in self._rows(self.relations, claim_id)]

    def _append_event(self, claim_id: str, event: dict) -> None:
        """Append-only. Historical events are never rewritten or deleted."""
        self.timelines.get_or_insert_default(claim_id).append(json.dumps(event))

    # -- internal: evaluation core ---------------------------------------

    def _gather_and_judge(self, claim: dict, previous: dict, evidence: list, mode: str) -> dict:
        urls = list(claim.get("sources") or [])
        for record in evidence[-4:]:
            u = str(record.get("url") or "")
            if u and u not in urls:
                urls.append(u)
        urls = urls[:4]
        prompt_claim = dict(claim)

        def leaf() -> str:
            context = ""
            for url in urls:
                try:
                    page = gl.nondet.web.render(url, mode="text")
                    context += f"\n--- SOURCE {url} ---\n{str(page)[:4000]}\n"
                except Exception:
                    context += f"\n--- SOURCE {url}: unreachable (treat as missing evidence) ---\n"
            prompt = build_prompt(prompt_claim, previous, evidence, context, mode)
            answer = gl.nondet.exec_prompt(prompt)
            return extract_json_object(answer)

        try:
            agreed = gl.eq_principle.prompt_comparative(leaf, EQ_PRINCIPLE)
        except Exception as exc:
            # Validators could not converge: refuse to manufacture certainty.
            return sanitize_evaluation(
                json.dumps(
                    {
                        "status": "UNCERTAIN",
                        "truth_confidence": 0,
                        "evidence_confidence": 0,
                        "explanation": f"Validators did not reach consensus on this evaluation ({str(exc)[:160]}). Reported as UNCERTAIN rather than asserting a result.",
                        "change_cause": "NONE",
                    }
                )
            )
        return sanitize_evaluation(extract_json_object(agreed))

    def _record_evaluation(self, claim_id: str, mode: str, trigger: str) -> dict:
        claim = self._claim(claim_id)
        previous = self._state(claim_id)
        evidence = self._evidence(claim_id)
        judgment = self._gather_and_judge(claim, previous, evidence, mode)

        now = self._now()
        prev_status = str(previous.get("status", "PENDING"))
        delta = classify_delta(previous, judgment)
        meaningful = is_meaningful(delta)

        judgment["claim_id"] = claim_id
        judgment["evaluated_at"] = now
        judgment["evaluation_index"] = len([e for e in self._timeline(claim_id) if e.get("kind") == "EVALUATION"]) + 1
        judgment["previous_status"] = prev_status
        judgment["delta"] = delta
        judgment["trigger"] = trigger
        judgment["mode"] = mode
        judgment["narrative"] = transition_narrative(prev_status, judgment, delta, now)
        judgment["last_meaningful_change"] = (
            now if meaningful else int(previous.get("last_meaningful_change", now))
        )

        self.states[claim_id] = json.dumps(judgment)
        self._append_event(
            claim_id,
            {
                "kind": "EVALUATION",
                "at": now,
                "status": judgment["status"],
                "previous_status": prev_status,
                "delta": delta,
                "meaningful": meaningful,
                "truth_confidence": judgment["truth_confidence"],
                "evidence_confidence": judgment["evidence_confidence"],
                "freshness": judgment["freshness"],
                "change_cause": judgment["change_cause"],
                "trigger": trigger,
                "narrative": judgment["narrative"],
                "actor": self._sender(),
            },
        )
        self._set_counter("total_evaluations", self.total_evaluations + 1)
        return judgment

    # -- public: claim lifecycle -----------------------------------------

    @gl.public.write
    def submit_claim(
        self,
        claim_text: str,
        category: str = "",
        claim_type: str = "general",
        sources_json: str = "[]",
        recheck_interval_seconds: int = 0,
    ) -> str:
        text = str(claim_text).strip()
        if len(text) < 12:
            return err("INVALID_CLAIM", "Claim text must be at least 12 characters.")
        if len(text) > 600:
            return err("INVALID_CLAIM", "Claim text must be at most 600 characters.")

        text_hash = evidence_fingerprint(text)
        existing = self.text_index.get(text_hash, "")
        if existing:
            return err("DUPLICATE_CLAIM", f"Identical claim already tracked as {existing}.")

        try:
            sources = json.loads(sources_json) if sources_json else []
            if not isinstance(sources, list):
                sources = []
        except Exception:
            return err("INVALID_SOURCES", "sources_json must be a JSON array of URLs.")
        sources = [str(s).strip()[:400] for s in sources[:8] if str(s).strip()]

        ctype = normalize_claim_type(claim_type)
        interval = int(recheck_interval_seconds)
        if interval <= 0:
            interval = TYPE_RECHECK.get(ctype, DEFAULT_RECHECK)
        interval = max(3600, min(interval, 365 * DAY))

        now = self._now()
        claim_id = "claim-" + digest_hex(f"{text_hash}:{now}")[:20]

        claim = {
            "claim_id": claim_id,
            "text": text,
            "text_hash": text_hash,
            "category": str(category)[:80],
            "claim_type": ctype,
            "sources": sources,
            "independent_source_count": independent_source_count(sources),
            "recheck_interval": interval,
            "created_at": now,
            "submitter": self._sender(),
        }
        self.claims[claim_id] = json.dumps(claim)
        self.text_index[text_hash] = claim_id
        self.claim_ids.append(claim_id)
        self._append_event(
            claim_id,
            {
                "kind": "SUBMITTED",
                "at": now,
                "status": "PENDING",
                "actor": claim["submitter"],
                "narrative": f"Claim registered with a {interval}s freshness requirement ({ctype}).",
            },
        )
        return ok({"claim_id": claim_id, "status": "PENDING", "claim": claim})

    @gl.public.write
    def evaluate_claim(self, claim_id: str) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        if self.states.get(claim_id, ""):
            return err("ALREADY_EVALUATED", "Use reevaluate_claim to produce a new evaluation.")
        return ok(self._record_evaluation(claim_id, "INITIAL", "initial_evaluation"))

    @gl.public.write
    def reevaluate_claim(self, claim_id: str, reason: str = "recheck") -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        if not self.states.get(claim_id, ""):
            return err("NOT_EVALUATED", "Call evaluate_claim first.")
        return ok(self._record_evaluation(claim_id, "REEVALUATION", str(reason)[:120]))

    @gl.public.write
    def submit_evidence(self, claim_id: str, url: str, note: str = "", trigger_reevaluation: bool = False) -> str:
        claim = self._claim(claim_id)
        if not claim:
            return err("UNKNOWN_CLAIM", "No such claim id.")
        link = str(url).strip()[:400]
        body = str(note).strip()[:600]
        if not link and not body:
            return err("EMPTY_EVIDENCE", "Provide a url and/or a note.")

        fingerprint = evidence_fingerprint(link + "|" + body)
        records = self._evidence(claim_id)
        known_prints = [str(r.get("fingerprint")) for r in records]
        known_domains = [str(r.get("domain")) for r in records if r.get("domain")] + [
            domain_of(s) for s in claim.get("sources", [])
        ]
        verdict = novelty_verdict(fingerprint, domain_of(link), known_prints, known_domains)
        if not verdict["accepted"]:
            return err(verdict["reason"], "This exact evidence was already submitted; it carries no new weight.")

        now = self._now()
        record = {
            "evidence_id": f"ev-{fingerprint[:12]}",
            "claim_id": claim_id,
            "url": link,
            "note_hash": evidence_fingerprint(body) if body else "",
            "note": body,
            "domain": domain_of(link),
            "fingerprint": fingerprint,
            "weight": verdict["weight"],
            "novelty": verdict["reason"],
            "submitted_at": now,
            "submitter": self._sender(),
        }
        self.evidence.get_or_insert_default(claim_id).append(json.dumps(record))
        self._append_event(
            claim_id,
            {
                "kind": "EVIDENCE",
                "at": now,
                "evidence_id": record["evidence_id"],
                "novelty": verdict["reason"],
                "weight": verdict["weight"],
                "actor": record["submitter"],
                "narrative": f"New evidence accepted ({verdict['reason']}, weight {verdict['weight']}).",
            },
        )
        if trigger_reevaluation and self.states.get(claim_id, ""):
            evaluation = self._record_evaluation(claim_id, "REEVALUATION", "new_evidence")
            return ok({"evidence": record, "evaluation": evaluation})
        return ok({"evidence": record, "evaluation": None})

    # -- public: challenges ----------------------------------------------

    @gl.public.write
    def challenge_claim(self, claim_id: str, argument: str, evidence_url: str = "") -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        if not self.states.get(claim_id, ""):
            return err("NOT_EVALUATED", "A claim must be evaluated before it can be challenged.")
        text = str(argument).strip()
        if len(text) < 20:
            return err("WEAK_CHALLENGE", "Explain the challenge in at least 20 characters.")

        previous = self._state(claim_id)
        now = self._now()
        challenge_id = "chal-" + digest_hex(f"{claim_id}:{evidence_fingerprint(text)}:{now}")[:16]

        if evidence_url:
            self.submit_evidence(claim_id, evidence_url, f"challenge evidence: {text[:200]}", False)

        record = {
            "challenge_id": challenge_id,
            "claim_id": claim_id,
            "argument": text[:800],
            "evidence_url": str(evidence_url)[:400],
            "opened_at": now,
            "challenger": self._sender(),
            "status": "OPEN",
            "original_evaluation": previous,  # preserved verbatim, never overwritten
            "reevaluation": None,
            "resolution": "",
        }
        self.challenges.get_or_insert_default(claim_id).append(json.dumps(record))
        self._append_event(
            claim_id,
            {
                "kind": "CHALLENGE_OPENED",
                "at": now,
                "challenge_id": challenge_id,
                "actor": record["challenger"],
                "narrative": "Challenge opened; a fresh independent evaluation is required.",
            },
        )
        # A challenge always triggers a fresh, independent evaluation.
        evaluation = self._record_evaluation(claim_id, "CHALLENGE", f"challenge:{challenge_id}")
        return ok({"challenge_id": challenge_id, "challenge": record, "evaluation": evaluation})

    @gl.public.write
    def resolve_challenge(self, claim_id: str, challenge_id: str) -> str:
        records = self._challenges(claim_id)
        index = -1
        for i in range(len(records)):
            if records[i].get("challenge_id") == challenge_id:
                index = i
                break
        if index < 0:
            return err("UNKNOWN_CHALLENGE", "No such challenge for this claim.")
        if records[index].get("status") != "OPEN":
            return err("ALREADY_RESOLVED", "This challenge is already resolved.")

        current = self._state(claim_id)
        original = records[index].get("original_evaluation") or {}
        now = self._now()
        delta = classify_delta(original, current)

        if delta in ("CONTRADICTED", "CHANGED"):
            outcome = "CHALLENGE_UPHELD"
        elif delta in ("UNCERTAIN",):
            outcome = "CHALLENGE_PARTIAL_UNRESOLVED"
        elif delta in ("RESOLVED", "STRENGTHENED", "UNCHANGED"):
            outcome = "ORIGINAL_UPHELD"
        else:
            outcome = "CHALLENGE_PARTIAL_UNRESOLVED"

        record = dict(records[index])
        record["status"] = "RESOLVED"
        record["reevaluation"] = current
        record["outcome"] = outcome
        record["resolved_at"] = now
        record["resolution"] = (
            f"{outcome}: original {original.get('status')} @{original.get('truth_confidence')} vs. "
            f"post-challenge {current.get('status')} @{current.get('truth_confidence')} (delta {delta}). "
            "Both evaluations are preserved."
        )
        self.challenges.get_or_insert_default(claim_id)[index] = json.dumps(record)
        self._append_event(
            claim_id,
            {
                "kind": "CHALLENGE_RESOLVED",
                "at": now,
                "challenge_id": challenge_id,
                "outcome": outcome,
                "actor": self._sender(),
                "narrative": record["resolution"],
            },
        )
        return ok(record)

    # -- public: freshness / decay ---------------------------------------

    @gl.public.write
    def apply_freshness_policy(self, claim_id: str) -> str:
        """Deterministic staleness check: no web access, no LLM, no fake certainty."""
        claim = self._claim(claim_id)
        current = self._state(claim_id)
        if not claim:
            return err("UNKNOWN_CLAIM", "No such claim id.")
        if not current:
            return err("NOT_EVALUATED", "Call evaluate_claim first.")

        now = self._now()
        age = now - int(current.get("evaluated_at", now))
        interval = int(claim.get("recheck_interval", DEFAULT_RECHECK))
        adjusted = decayed_confidence(
            int(current.get("truth_confidence", 0)),
            age,
            interval,
            int(current.get("evidence_confidence", 0)),
        )
        due = age > interval
        if not due or adjusted == int(current.get("truth_confidence", 0)):
            return ok({"claim_id": claim_id, "recheck_due": due, "changed": False, "state": current})

        prev_status = str(current.get("status"))
        updated = dict(current)
        updated["truth_confidence"] = adjusted
        updated["status"] = "STALE" if adjusted < 50 else prev_status
        updated["change_cause"] = "SOURCE_OUTDATED"
        updated["previous_status"] = prev_status
        updated["delta"] = "WEAKENED" if updated["status"] == prev_status else "UNCERTAIN"
        updated["evaluated_at"] = int(current.get("evaluated_at", now))
        updated["decay_applied_at"] = now
        updated["last_meaningful_change"] = (
            now if updated["status"] != prev_status else int(current.get("last_meaningful_change", now))
        )
        updated["narrative"] = (
            f"{prev_status} -> {updated['status']} (freshness policy). Evidence is {age}s old against a "
            f"{interval}s requirement; confidence decayed {int(current.get('truth_confidence', 0))} -> {adjusted}. "
            "Re-evaluation required."
        )
        self.states[claim_id] = json.dumps(updated)
        self._append_event(
            claim_id,
            {
                "kind": "DECAY",
                "at": now,
                "status": updated["status"],
                "previous_status": prev_status,
                "delta": updated["delta"],
                "meaningful": updated["status"] != prev_status,
                "truth_confidence": adjusted,
                "change_cause": "SOURCE_OUTDATED",
                "narrative": updated["narrative"],
                "actor": self._sender(),
            },
        )
        return ok({"claim_id": claim_id, "recheck_due": True, "changed": True, "state": updated})

    # -- public: relationships -------------------------------------------

    @gl.public.write
    def link_claims(self, claim_id_a: str, claim_id_b: str, relation: str = "TEMPORAL_SUCCESSOR") -> str:
        if not self.claims.get(claim_id_a, "") or not self.claims.get(claim_id_b, ""):
            return err("UNKNOWN_CLAIM", "Both claim ids must exist.")
        if claim_id_a == claim_id_b:
            return err("INVALID_RELATION", "A claim cannot be related to itself.")
        rel = str(relation).strip().upper()
        if rel not in RELATIONS:
            return err("INVALID_RELATION", f"relation must be one of {list(RELATIONS)}.")
        inverse = {
            "SUPERSEDES": "SUPERSEDED_BY",
            "SUPERSEDED_BY": "SUPERSEDES",
        }.get(rel, rel)

        now = self._now()
        self.relations.get_or_insert_default(claim_id_a).append(
            json.dumps({"claim_id": claim_id_b, "relation": rel, "at": now, "actor": self._sender()})
        )
        self.relations.get_or_insert_default(claim_id_b).append(
            json.dumps({"claim_id": claim_id_a, "relation": inverse, "at": now, "actor": self._sender()})
        )
        for cid, other, r in ((claim_id_a, claim_id_b, rel), (claim_id_b, claim_id_a, inverse)):
            self._append_event(
                cid,
                {
                    "kind": "RELATION",
                    "at": now,
                    "related_claim": other,
                    "relation": r,
                    "actor": self._sender(),
                    "narrative": f"Linked to {other} as {r}; evaluations must consider it as context.",
                },
            )
        return ok({"a": claim_id_a, "b": claim_id_b, "relation": rel, "inverse": inverse})

    # -- public: reads (deterministic views) -----------------------------

    @gl.public.view
    def get_current_state(self, claim_id: str) -> str:
        state = self._state(claim_id)
        if not state:
            return err("NOT_EVALUATED", "No evaluation stored for this claim.")
        return ok(state)

    @gl.public.view
    def get_snapshot(self, claim_id: str) -> str:
        claim = self._claim(claim_id)
        if not claim:
            return err("UNKNOWN_CLAIM", "No such claim id.")
        state = self._state(claim_id)
        timeline = self._timeline(claim_id)
        evo = evolution_score(timeline)
        top_support = (state.get("supporting_evidence") or [])[:2]
        top_conflict = (state.get("contradicting_evidence") or [])[:2]
        return ok(
            {
                "claim": claim.get("text"),
                "claim_id": claim_id,
                "status": state.get("status", "PENDING"),
                "truth_confidence": state.get("truth_confidence", 0),
                "evidence_confidence": state.get("evidence_confidence", 0),
                "last_evaluated": state.get("evaluated_at", 0),
                "last_meaningful_change": state.get("last_meaningful_change", 0),
                "temporal_scope": state.get("temporal_scope", "unspecified"),
                "key_supporting_evidence": top_support,
                "key_contradicting_evidence": top_conflict,
                "why": state.get("narrative", ""),
                "evolution": evo,
                "evolution_summary": " | ".join(
                    f"t={e.get('at')} {e.get('status')} @{e.get('truth_confidence')}"
                    for e in timeline
                    if e.get("kind") in ("EVALUATION", "DECAY")
                ),
                "open_challenges": len([c for c in self._challenges(claim_id) if c.get("status") == "OPEN"]),
            }
        )

    @gl.public.view
    def get_historical_state(self, claim_id: str, at_timestamp: int) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        cutoff = int(at_timestamp)
        best = {}
        for event in self._timeline(claim_id):
            if event.get("kind") in ("EVALUATION", "DECAY") and int(event.get("at", 0)) <= cutoff:
                best = event
        if not best:
            return ok({"claim_id": claim_id, "as_of": cutoff, "belief": "NO_BELIEF_YET"})
        return ok({"claim_id": claim_id, "as_of": cutoff, "belief": best})

    @gl.public.view
    def get_timeline(self, claim_id: str) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        return ok({"claim_id": claim_id, "events": self._timeline(claim_id)})

    @gl.public.view
    def get_evolution_timeline(self, claim_id: str) -> str:
        """Alias of `get_timeline` for callers using the spec name."""
        return self.get_timeline(claim_id)

    @gl.public.view
    def get_claim_metadata(self, claim_id: str) -> str:
        claim = self._claim(claim_id)
        if not claim:
            return err("UNKNOWN_CLAIM", "No such claim id.")
        return ok(claim)

    @gl.public.view
    def get_related_claims(self, claim_id: str) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        related = []
        for rel in self._relations(claim_id):
            other = self._claim(str(rel.get("claim_id")))
            other_state = self._state(str(rel.get("claim_id")))
            related.append(
                {
                    "relation": rel.get("relation"),
                    "claim_id": rel.get("claim_id"),
                    "text": other.get("text", ""),
                    "status": other_state.get("status", "PENDING"),
                    "truth_confidence": other_state.get("truth_confidence", 0),
                }
            )
        return ok({"claim_id": claim_id, "related": related})

    @gl.public.view
    def get_evidence_graph(self, claim_id: str) -> str:
        claim = self._claim(claim_id)
        if not claim:
            return err("UNKNOWN_CLAIM", "No such claim id.")
        state = self._state(claim_id)
        submitted = self._evidence(claim_id)
        domains = []
        for u in list(claim.get("sources", [])) + [r.get("url", "") for r in submitted]:
            d = domain_of(str(u))
            if d and d not in domains:
                domains.append(d)
        return ok(
            {
                "claim_id": claim_id,
                "nodes": {
                    "claim": claim.get("text"),
                    "sources": claim.get("sources", []),
                    "submitted_evidence": submitted,
                    "distinct_domains": domains,
                    "independent_source_count": len(domains),
                    "supporting_evidence": state.get("supporting_evidence", []),
                    "counter_evidence": state.get("contradicting_evidence", []),
                },
                "edges": {
                    "evaluations": [
                        {"at": e.get("at"), "status": e.get("status"), "delta": e.get("delta")}
                        for e in self._timeline(claim_id)
                        if e.get("kind") == "EVALUATION"
                    ],
                    "transitions": [
                        {"at": e.get("at"), "from": e.get("previous_status"), "to": e.get("status"), "why": e.get("narrative")}
                        for e in self._timeline(claim_id)
                        if e.get("kind") in ("EVALUATION", "DECAY") and e.get("meaningful")
                    ],
                    "relations": self._relations(claim_id),
                },
            }
        )

    @gl.public.view
    def get_evolution_score(self, claim_id: str) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        return ok({"claim_id": claim_id, "evolution": evolution_score(self._timeline(claim_id))})

    @gl.public.view
    def get_challenges(self, claim_id: str) -> str:
        if not self.claims.get(claim_id, ""):
            return err("UNKNOWN_CLAIM", "No such claim id.")
        return ok({"claim_id": claim_id, "challenges": self._challenges(claim_id)})

    @gl.public.view
    def list_claims(self, offset: int = 0, limit: int = 20) -> str:
        start = max(0, int(offset))
        count = max(1, min(int(limit), 100))
        ids = list(self.claim_ids)
        page = ids[start : start + count]
        items = []
        for cid in page:
            claim = self._claim(cid)
            state = self._state(cid)
            items.append(
                {
                    "claim_id": cid,
                    "text": claim.get("text", ""),
                    "claim_type": claim.get("claim_type", "general"),
                    "status": state.get("status", "PENDING"),
                    "truth_confidence": state.get("truth_confidence", 0),
                    "last_evaluated": state.get("evaluated_at", 0),
                }
            )
        return ok({"total": len(ids), "offset": start, "items": items})

    @gl.public.view
    def get_stats(self) -> str:
        by_status = {}
        for cid in list(self.claim_ids):
            s = self._state(cid).get("status", "PENDING")
            by_status[s] = int(by_status.get(s, 0)) + 1
        return ok(
            {
                "claims": len(list(self.claim_ids)),
                "evaluations": int(self.total_evaluations),
                "by_status": by_status,
                "statuses": list(STATUSES),
                "deltas": list(DELTAS),
                "claim_types": list(CLAIM_TYPES),
                "relations": list(RELATIONS),
            }
        )
