"""
End-to-end flow tests with the non-deterministic block replaced by scripted
validator judgments. Storage, transitions, timeline immutability, challenges,
decay and relationships are exercised exactly as on chain.
"""

import json

import fact_evolution as fe


class Harness(fe.FactEvolution):
    """Contract instance whose only patched surface is the nondet judgment."""

    def __init__(self, scripted):
        self.state_json = json.dumps(fe.empty_document())
        self._scripted = list(scripted)
        self._time = 1_770_000_000

    # deterministic clock for tests
    def _now(self):
        self._time += 3600
        return self._time

    def _sender(self):
        return "0xtest"

    def _gather_and_judge(self, claim, previous, evidence, mode):
        payload = self._scripted.pop(0) if self._scripted else {"status": "UNCERTAIN"}
        return fe.sanitize_evaluation(json.dumps(payload))



VERIFIED = {"status": "VERIFIED", "truth_confidence": 91, "evidence_confidence": 88, "freshness": 90, "sources_used": ["https://a.com", "https://b.org"], "supporting_evidence": ["Registry lists active operations"]}
STRONGER = {**VERIFIED, "truth_confidence": 99, "supporting_evidence": ["Primary registry filing 2026-04"]}
GONE = {"status": "REJECTED", "truth_confidence": 93, "evidence_confidence": 90, "reality_changed": True, "change_cause": "REALITY_CHANGED", "contradicting_evidence": ["Official exit notice"]}
CONFLICT = {"status": "DISPUTED", "truth_confidence": 48, "evidence_confidence": 62, "contradiction_reason": "Two credible outlets disagree", "contradicting_evidence": ["Outlet B reports closure"], "supporting_evidence": ["Outlet A reports operations"]}
DATED = {"status": "VERIFIED", "truth_confidence": 84, "evidence_confidence": 80, "change_cause": "APPARENT_ONLY", "temporal_scope": "as of 2026-08", "contradiction_reason": "Conflicting report refers to 2024"}


def _data(raw):
    parsed = json.loads(raw)
    assert parsed["ok"], parsed
    return parsed["data"]


def _new(scripted, **kw):
    c = Harness(scripted)
    res = _data(c.submit_claim("Company X currently operates in Nigeria.", claim_type="company_status", sources_json=json.dumps(["https://a.com/x"]), **kw))
    return c, res["claim_id"]


def test_submit_validates_and_dedupes():
    c = Harness([])
    assert json.loads(c.submit_claim("too short"))["error"] == "INVALID_CLAIM"
    c.submit_claim("Company X currently operates in Nigeria.")
    again = json.loads(c.submit_claim("Company X currently operates in Nigeria."))
    assert again["error"] == "DUPLICATE_CLAIM"


def test_claim_type_sets_freshness_requirement():
    c = Harness([])
    meta = _data(c.submit_claim("The match between A and B has finished today.", claim_type="sports_outcome"))
    assert meta["claim"]["recheck_interval"] == fe.DAY


def test_scenario_stays_true_across_evaluations():
    c, cid = _new([VERIFIED, VERIFIED, STRONGER])
    c.evaluate_claim(cid)
    c.reevaluate_claim(cid)
    last = _data(c.reevaluate_claim(cid))
    assert last["delta"] == "STRENGTHENED"
    assert _data(c.get_evolution_score(cid))["evolution"]["label"] == "STABLE"


def test_scenario_fact_becomes_false():
    c, cid = _new([VERIFIED, GONE])
    c.evaluate_claim(cid)
    after = _data(c.reevaluate_claim(cid))
    assert after["status"] == "REJECTED" and after["delta"] == "CHANGED"
    assert "REALITY_CHANGED" == after["change_cause"]


def test_scenario_conflicting_sources_produce_disputed_not_false():
    c, cid = _new([VERIFIED, CONFLICT])
    c.evaluate_claim(cid)
    after = _data(c.reevaluate_claim(cid))
    assert after["status"] == "DISPUTED" and after["delta"] == "CONTRADICTED"


def test_scenario_apparent_contradiction_is_dated_not_a_change():
    c, cid = _new([VERIFIED, DATED])
    c.evaluate_claim(cid)
    after = _data(c.reevaluate_claim(cid))
    assert after["change_cause"] == "APPARENT_ONLY"
    assert after["status"] == "VERIFIED"


def test_scenario_evidence_goes_stale():
    c, cid = _new([VERIFIED])
    c.evaluate_claim(cid)
    c._time += 200 * fe.DAY
    result = _data(c.apply_freshness_policy(cid))
    assert result["changed"] and result["state"]["change_cause"] == "SOURCE_OUTDATED"
    assert result["state"]["truth_confidence"] < 91


def test_history_is_immutable_and_queryable_by_time():
    c, cid = _new([VERIFIED, GONE])
    first = _data(c.evaluate_claim(cid))
    mid = first["evaluated_at"] + 1
    _data(c.reevaluate_claim(cid))
    past = _data(c.get_historical_state(cid, mid))
    assert past["belief"]["status"] == "VERIFIED"
    assert _data(c.get_current_state(cid))["status"] == "REJECTED"
    kinds = [e["kind"] for e in _data(c.get_timeline(cid))["events"]]
    assert kinds == ["SUBMITTED", "EVALUATION", "EVALUATION"]


def test_evidence_novelty_and_reevaluation_trigger():
    c, cid = _new([VERIFIED, STRONGER])
    c.evaluate_claim(cid)
    first = _data(c.submit_evidence(cid, "https://primary.gov/filing", "official filing", True))
    assert first["evidence"]["novelty"] == "NOVEL_INDEPENDENT_SOURCE"
    assert first["evaluation"]["delta"] == "STRENGTHENED"
    dup = json.loads(c.submit_evidence(cid, "https://primary.gov/filing", "official filing"))
    assert dup["error"] == "DUPLICATE_EVIDENCE"


def test_challenge_preserves_original_and_resolves():
    c, cid = _new([VERIFIED, GONE])
    original = _data(c.evaluate_claim(cid))
    challenge = _data(c.challenge_claim(cid, "The company published an exit notice in July 2026.", "https://gov.ng/notice"))
    assert challenge["challenge"]["original_evaluation"]["status"] == original["status"]
    resolved = _data(c.resolve_challenge(cid, challenge["challenge_id"]))
    assert resolved["outcome"] == "CHALLENGE_UPHELD"
    assert resolved["original_evaluation"]["status"] == "VERIFIED"
    assert resolved["reevaluation"]["status"] == "REJECTED"
    assert json.loads(c.resolve_challenge(cid, challenge["challenge_id"]))["error"] == "ALREADY_RESOLVED"


def test_related_claims_are_bidirectional():
    c, a = _new([])
    b = _data(c.submit_claim("Company X closed its Nigerian operations in July 2026."))["claim_id"]
    _data(c.link_claims(b, a, "SUPERSEDES"))
    assert _data(c.get_related_claims(a))["related"][0]["relation"] == "SUPERSEDED_BY"
    assert json.loads(c.link_claims(a, a, "SUPERSEDES"))["error"] == "INVALID_RELATION"


def test_snapshot_and_evidence_graph():
    c, cid = _new([VERIFIED])
    c.evaluate_claim(cid)
    snap = _data(c.get_snapshot(cid))
    assert snap["status"] == "VERIFIED" and "VERIFIED" in snap["evolution_summary"]
    graph = _data(c.get_evidence_graph(cid))
    assert graph["nodes"]["independent_source_count"] >= 1


def test_guards_reject_out_of_order_calls():
    c, cid = _new([VERIFIED])
    assert json.loads(c.reevaluate_claim(cid))["error"] == "NOT_EVALUATED"
    c.evaluate_claim(cid)
    assert json.loads(c.evaluate_claim(cid))["error"] == "ALREADY_EVALUATED"
    assert json.loads(c.get_snapshot("claim-missing"))["error"] == "UNKNOWN_CLAIM"


def test_stats_track_totals():
    c, cid = _new([VERIFIED])
    c.evaluate_claim(cid)
    stats = _data(c.get_stats())
    assert stats["claims"] == 1 and stats["evaluations"] == 1
    assert stats["by_status"]["VERIFIED"] == 1
