"""Unit tests for the deterministic (consensus-critical) logic."""

import json

import fact_evolution as fe


# --- sanitization / epistemic honesty ---------------------------------------


def test_scores_are_clamped_and_statuses_normalized():
    out = fe.sanitize_evaluation(json.dumps({"status": "true", "truth_confidence": 155, "evidence_confidence": 140}))
    assert out["status"] == "VERIFIED"
    assert out["truth_confidence"] == 100
    assert out["evidence_confidence"] == 100
    low = fe.sanitize_evaluation(json.dumps({"status": "confirmed", "truth_confidence": -9, "evidence_confidence": 70}))
    assert low["truth_confidence"] == 0


def test_garbage_model_output_degrades_to_uncertain():
    out = fe.sanitize_evaluation("not json at all")
    assert out["status"] == "UNCERTAIN"
    assert out["truth_confidence"] == 0


def test_weak_evidence_cannot_produce_verified():
    out = fe.sanitize_evaluation(
        json.dumps({"status": "VERIFIED", "truth_confidence": 95, "evidence_confidence": 12})
    )
    assert out["status"] == "UNCERTAIN"
    assert out["truth_confidence"] <= 60


def test_json_extraction_from_chatty_answer():
    raw = 'Sure! Here you go:\n```json\n{"status": "VERIFIED", "truth_confidence": 88}\n```\nHope that helps'
    assert json.loads(fe.extract_json_object(raw))["status"] == "VERIFIED"


def test_json_extraction_handles_braces_in_strings():
    raw = '{"explanation": "uses { and } inside", "status": "UNCERTAIN"}'
    assert json.loads(fe.extract_json_object(raw))["explanation"] == "uses { and } inside"


# --- source independence ----------------------------------------------------


def test_copies_of_one_source_are_not_independent_confirmations():
    urls = ["https://wire.example.com/a", "http://www.wire.example.com/b", "https://other.org/c"]
    assert fe.independent_source_count(urls) == 2


# --- delta classification ---------------------------------------------------


def _ev(status, truth, **kw):
    base = {"status": status, "truth_confidence": truth, "evidence_confidence": 80}
    base.update(kw)
    return base


def test_initial_and_unchanged():
    assert fe.classify_delta({}, _ev("VERIFIED", 90)) == "INITIAL"
    assert fe.classify_delta(_ev("VERIFIED", 90), _ev("VERIFIED", 92)) == "UNCHANGED"


def test_strengthened_and_weakened():
    assert fe.classify_delta(_ev("VERIFIED", 70), _ev("VERIFIED", 88)) == "STRENGTHENED"
    assert fe.classify_delta(_ev("VERIFIED", 90), _ev("VERIFIED", 62)) == "WEAKENED"


def test_reality_change_beats_confidence_movement():
    new = _ev("REJECTED", 93, reality_changed=True, change_cause="REALITY_CHANGED")
    assert fe.classify_delta(_ev("VERIFIED", 91), new) == "CHANGED"


def test_contradiction_and_resolution():
    assert fe.classify_delta(_ev("VERIFIED", 90), _ev("DISPUTED", 48)) == "CONTRADICTED"
    assert fe.classify_delta(_ev("DISPUTED", 48), _ev("REJECTED", 93)) == "RESOLVED"


def test_becoming_uncertain():
    assert fe.classify_delta(_ev("VERIFIED", 90), _ev("UNCERTAIN", 40)) == "UNCERTAIN"
    assert fe.is_meaningful("UNCHANGED") is False
    assert fe.is_meaningful("WEAKENED") is True


# --- confidence decay -------------------------------------------------------


def test_time_alone_does_not_decay_a_fresh_claim():
    assert fe.decayed_confidence(90, age=10 * fe.DAY, recheck=30 * fe.DAY, evidence_conf=85) == 90


def test_overdue_claim_with_weak_evidence_decays_faster():
    strong = fe.decayed_confidence(90, age=90 * fe.DAY, recheck=30 * fe.DAY, evidence_conf=90)
    weak = fe.decayed_confidence(90, age=90 * fe.DAY, recheck=30 * fe.DAY, evidence_conf=20)
    assert 90 > strong > weak >= 45


# --- evolution score --------------------------------------------------------


def _timeline(pairs):
    return [
        {"kind": "EVALUATION", "at": i, "status": s, "truth_confidence": c, "delta": "UNCHANGED"}
        for i, (s, c) in enumerate(pairs)
    ]


def test_stable_claim_scores_low():
    result = fe.evolution_score(_timeline([("VERIFIED", 91), ("VERIFIED", 92), ("VERIFIED", 94)]))
    assert result["label"] == "STABLE"


def test_volatile_claim_scores_high():
    result = fe.evolution_score(
        _timeline([("VERIFIED", 91), ("DISPUTED", 48), ("VERIFIED", 88), ("REJECTED", 93)])
    )
    assert result["label"] == "HIGHLY_VOLATILE"
    assert result["status_flips"] == 3


def test_single_evaluation_is_untested():
    assert fe.evolution_score(_timeline([("VERIFIED", 90)]))["label"] == "UNTESTED"


# --- anti-manipulation ------------------------------------------------------


def test_duplicate_evidence_is_rejected():
    fp = fe.evidence_fingerprint("https://a.com|same note")
    verdict = fe.novelty_verdict(fp, "a.com", [fp], ["a.com"])
    assert verdict == {"accepted": False, "weight": 0, "reason": "DUPLICATE_EVIDENCE"}


def test_same_domain_evidence_is_downweighted_not_dropped():
    verdict = fe.novelty_verdict("new-print", "a.com", ["old"], ["a.com"])
    assert verdict["accepted"] and verdict["weight"] == 40


def test_novel_independent_evidence_gets_full_weight():
    assert fe.novelty_verdict("new", "b.org", ["old"], ["a.com"])["weight"] == 100


# --- narrative + prompt -----------------------------------------------------


def test_transition_narrative_explains_cause():
    text = fe.transition_narrative(
        "VERIFIED",
        _ev("UNCERTAIN", 45, change_cause="SOURCE_OUTDATED", evidence_confidence=40, freshness=20),
        "UNCERTAIN",
        1770000000,
    )
    assert "VERIFIED -> UNCERTAIN" in text and "outdated" in text


def test_prompt_includes_previous_belief_and_temporal_instructions():
    claim = {"text": "Company X currently operates in Nigeria.", "claim_type": "company_status", "category": "", "created_at": 1, "recheck_interval": fe.DAY}
    prompt = fe.build_prompt(claim, _ev("VERIFIED", 90), [], "source text", "REEVALUATION")
    assert "TEMPORAL SCOPE" in prompt and "PREVIOUS_CONTRACT_BELIEF" in prompt
    assert "VERIFIED" in prompt


def test_claim_types_and_freshness_defaults_are_consistent():
    for t in fe.CLAIM_TYPES:
        assert t in fe.TYPE_RECHECK
    assert fe.TYPE_RECHECK["sports_outcome"] < fe.TYPE_RECHECK["regulatory_status"]


# --- consensus grid ---------------------------------------------------------


def test_scores_are_snapped_to_the_consensus_grid():
    out = fe.sanitize_evaluation(
        json.dumps(
            {
                "status": "VERIFIED",
                "truth_confidence": 87,
                "evidence_confidence": 83,
                "source_quality": 71,
                "freshness": 64,
                "independence": 55,
            }
        )
    )
    for field in ("truth_confidence", "evidence_confidence", "source_quality", "freshness", "independence"):
        assert out[field] % fe.SCORE_BUCKET == 0, field
    assert out["truth_confidence"] == 90 and out["evidence_confidence"] == 80


def test_close_but_different_answers_collapse_to_one_consensus_digest():
    a = fe.sanitize_evaluation(json.dumps({"status": "VERIFIED", "truth_confidence": 87, "evidence_confidence": 83}))
    b = fe.sanitize_evaluation(json.dumps({"status": "VERIFIED", "truth_confidence": 92, "evidence_confidence": 78}))
    assert a["consensus_digest"] == b["consensus_digest"]
    assert a["consensus"] == b["consensus"]


def test_different_change_causes_are_a_consensus_disagreement():
    base = {"status": "CHANGED", "truth_confidence": 80, "evidence_confidence": 80, "reality_changed": True}
    a = fe.sanitize_evaluation(json.dumps({**base, "change_cause": "REALITY_CHANGED"}))
    b = fe.sanitize_evaluation(json.dumps({**base, "change_cause": "EVIDENCE_CHANGED"}))
    assert a["consensus_digest"] != b["consensus_digest"]


def test_change_cause_and_reality_flag_cannot_contradict_each_other():
    out = fe.sanitize_evaluation(
        json.dumps({"status": "CHANGED", "truth_confidence": 80, "evidence_confidence": 80, "change_cause": "REALITY_CHANGED"})
    )
    assert out["reality_changed"] is True
    other = fe.sanitize_evaluation(
        json.dumps({"status": "CHANGED", "truth_confidence": 80, "evidence_confidence": 80, "reality_changed": True})
    )
    assert other["change_cause"] == "REALITY_CHANGED"


def test_agreeing_answers_can_never_produce_different_deltas():
    previous = _ev("VERIFIED", 80)
    for raw_truth in range(0, 101):
        current = fe.sanitize_evaluation(
            json.dumps({"status": "VERIFIED", "truth_confidence": raw_truth, "evidence_confidence": 80})
        )
        neighbour = fe.sanitize_evaluation(
            json.dumps({"status": "VERIFIED", "truth_confidence": current["truth_confidence"], "evidence_confidence": 80})
        )
        # same bucket -> same digest -> necessarily the same recorded delta
        assert current["consensus_digest"] == neighbour["consensus_digest"]
        assert fe.classify_delta(previous, current) == fe.classify_delta(previous, neighbour)


def test_thresholds_sit_on_the_grid():
    for bound in (
        fe.STRENGTHEN_DELTA,
        abs(fe.WEAKEN_DELTA),
        fe.WEAK_EVIDENCE_FLOOR,
        fe.DISPUTE_TRUTH_FLOOR,
        fe.UNCERTAIN_TRUTH_CAP,
        fe.STALE_TRUTH_FLOOR,
    ):
        assert bound % fe.SCORE_BUCKET == 0


def test_decay_stays_on_the_grid():
    for age in (31, 45, 90, 200, 400):
        value = fe.decayed_confidence(90, age=age * fe.DAY, recheck=30 * fe.DAY, evidence_conf=50)
        assert value % fe.SCORE_BUCKET == 0


def test_canonical_answer_is_byte_stable():
    payload = {"status": "VERIFIED", "truth_confidence": 88, "evidence_confidence": 81, "explanation": "x"}
    first = fe.canonical_answer_json(fe.sanitize_evaluation(json.dumps(payload)))
    second = fe.canonical_answer_json(fe.sanitize_evaluation(first))
    assert first == second
