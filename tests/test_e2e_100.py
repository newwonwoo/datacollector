"""91 parameterized E2E cases (total with canonical 9 = 100)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

import pytest

from collector.events import EventLogger
from collector.hashing import transcript_hash
from collector.payload import new_payload
from collector.pipeline import detect_removed, manual_reinject, run_pipeline
from collector.services import MockError, build_mock_services
from collector.store import JSONStore


@dataclass
class Case:
    cid: str
    kind: str
    setup: Callable[[JSONStore, EventLogger], tuple[dict, object]]
    assertions: Callable[[dict, JSONStore, EventLogger], None]
    action: str = "run"  # run | reinject | detect_removed


def _payload(vid: str) -> dict:
    return new_payload(video_id=vid, run_id=f"run_{vid}")


_LONG_SUMMARY = (
    "단타 매매 전략 요약입니다. 장중 고점 돌파 시 분할 진입하고 "
    "거래량과 저항선을 반드시 확인합니다. 손절선은 직전 저점, 익절은 분할로 수행합니다."
)


def _svc_success(vid: str, similarity: float = 0.75, caption_source: str = "manual"):
    return build_mock_services(
        captions_map={vid: {"source": caption_source, "text": f"{vid} 자막 원문 내용 {random.randint(0, 999)}"}},
        llm_script=[{"summary": f"[{vid}] {_LONG_SUMMARY}", "rules": [f"{vid}_rule_1", f"{vid}_rule_2"], "tags": ["t1", "t2"]}],
        similarity=similarity,
    )


# ---------- Case generators ----------

def _gen_success_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"OK{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), _svc_success(_vid)
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "promoted"
            assert p["confidence"] == "confirmed"
            assert p["stage_status"]["package"] == "completed"
        out.append(Case(cid=f"SUCCESS-{i:02d}", kind="success", setup=setup, assertions=assertions))
    return out


def _gen_asr_inferred_cases(n: int) -> list[Case]:
    # ASR caption with lower similarity → inferred, not promoted
    out = []
    for i in range(n):
        vid = f"ASR{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), _svc_success(_vid, similarity=0.55, caption_source="asr")
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "reviewed_inferred"
            assert p["confidence"] == "inferred"
            assert p["stage_status"]["promote"] in ("skipped", "failed", "started")
        out.append(Case(cid=f"INFER-{i:02d}", kind="inferred", setup=setup, assertions=assertions))
    return out


def _gen_unverified_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"UNV{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), _svc_success(_vid, similarity=0.30)
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "reviewed_unverified"
            assert p["confidence"] == "unverified"
        out.append(Case(cid=f"UNVER-{i:02d}", kind="unverified", setup=setup, assertions=assertions))
    return out


def _gen_rule_c_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"DUP{i:08d}"
        text = f"동일 자막 {vid}"
        def setup(s, l, _vid=vid, _text=text):
            prior = _payload(_vid)
            prior["transcript_hash"] = transcript_hash(_text)
            prior["record_status"] = "promoted"
            s.upsert(prior)
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "manual", "text": _text}}
            )
        def assertions(p, s, l, _vid=vid):
            assert p["stage_status"]["extract"] == "skipped"
            assert any(e.get("reason") == "rule_c_duplicate" for e in l.events)
        out.append(Case(cid=f"DUP-{i:02d}", kind="rule_c", setup=setup, assertions=assertions))
    return out


def _gen_rule_b_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"RVB{i:08d}"
        def setup(s, l, _vid=vid):
            prior = _payload(_vid)
            prior["transcript_hash"] = transcript_hash("prev text")
            prior["summary"] = "prev summary"
            prior["rules"] = ["prev rule"]
            prior["record_status"] = "promoted"
            prior["payload_version"] = 1
            s.upsert(prior)
            return _payload(_vid), _svc_success(_vid)
        def assertions(p, s, l, _vid=vid):
            assert p["payload_version"] == 2
            assert p["history"] and p["history"][0]["prev_summary"] == "prev summary"
        out.append(Case(cid=f"REVB-{i:02d}", kind="rule_b", setup=setup, assertions=assertions))
    return out


def _gen_http429_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"H429{i:07d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), build_mock_services(
                captions_map={_vid: MockError("HTTP_429", "rl")}
            )
        def assertions(p, s, l, _vid=vid):
            assert p["failure_reason_code"] == "HTTP_429"
            assert p["stage_status"]["collect"] == "failed"
            assert p["record_status"] != "promoted"
        out.append(Case(cid=f"H429-{i:02d}", kind="http_429", setup=setup, assertions=assertions))
    return out


def _gen_reprompt_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"RPT{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "manual", "text": "자막"}},
                llm_script=[
                    MockError("SEMANTIC_JSON_SCHEMA_FAIL", "bad"),
                    {"summary": f"[{_vid}] {_LONG_SUMMARY}", "rules": ["r"], "tags": ["t"]},
                ],
                similarity=0.8,
            )
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "promoted"
            assert p["stage_status"]["extract"] == "completed"
        out.append(Case(cid=f"RPT-{i:02d}", kind="reprompt", setup=setup, assertions=assertions))
    return out


def _gen_sync_invalid_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"SNK{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "manual", "text": "자막"}},
                llm_script=[{"summary": f"[{_vid}] {_LONG_SUMMARY}", "rules": ["r"], "tags": ["t"]}],
                similarity=0.8,
                git_script=[MockError("GIT_CONFLICT", "c")] * 6,
            )
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "invalid"
            assert p["failure_reason_code"] == "GIT_CONFLICT"
            assert len(s.dlq) >= 1
        out.append(Case(cid=f"SNK-{i:02d}", kind="sync_invalid", setup=setup, assertions=assertions))
    return out


def _gen_no_transcript_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"NTS{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "none", "text": ""}}
            )
        def assertions(p, s, l, _vid=vid):
            assert p["failure_reason_code"] == "YT_NO_TRANSCRIPT"
            assert p["stage_status"]["collect"] == "failed"
        out.append(Case(cid=f"NTS-{i:02d}", kind="no_transcript", setup=setup, assertions=assertions))
    return out


def _gen_empty_rules_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"EMP{i:08d}"
        def setup(_s, _l, _vid=vid):
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "manual", "text": "자막"}},
                llm_script=[{"summary": "요약", "rules": [], "tags": []}],
            )
        def assertions(p, s, l, _vid=vid):
            assert p["failure_reason_code"] == "SEMANTIC_EMPTY_RULES"
            assert p["stage_status"]["normalize"] == "failed"
        out.append(Case(cid=f"EMP-{i:02d}", kind="empty_rules", setup=setup, assertions=assertions))
    return out


def _gen_archived_dedup_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"ARC{i:08d}"
        text = f"archived text {vid}"
        def setup(s, l, _vid=vid, _text=text):
            prior = _payload(_vid)
            prior["transcript_hash"] = transcript_hash(_text)
            prior["record_status"] = "promoted"
            s.upsert(prior)
            s.archive(prior["source_key"])
            return _payload(_vid), build_mock_services(
                captions_map={_vid: {"source": "manual", "text": _text}}
            )
        def assertions(p, s, l, _vid=vid):
            assert any(e.get("reason") == "rule_c_duplicate" for e in l.events)
        out.append(Case(cid=f"ARC-{i:02d}", kind="archived_dedup", setup=setup, assertions=assertions))
    return out


def _gen_removed_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"RMV{i:08d}"
        def setup(s, l, _vid=vid):
            prior = _payload(_vid)
            prior["record_status"] = "promoted"
            s.upsert(prior)
            services = build_mock_services(alive_map={_vid: False})
            return prior, services
        def assertions(p, s, l, _vid=vid):
            assert p["archive_state"] == "REMOVED"
            assert p["failure_reason_code"] == "YT_VIDEO_REMOVED"
        out.append(Case(cid=f"RMV-{i:02d}", kind="removed", setup=setup, assertions=assertions, action="detect_removed"))
    return out


def _gen_reinject_cases(n: int) -> list[Case]:
    out = []
    for i in range(n):
        vid = f"REI{i:08d}"
        def setup(_s, _l, _vid=vid):
            p = _payload(_vid)
            p["record_status"] = "invalid"
            return p, None
        def assertions(p, s, l, _vid=vid):
            assert p["record_status"] == "collected"
            assert p["retry_count"] == 1
            assert any(e["entity_type"] == "manual_action" for e in l.events)
        out.append(Case(cid=f"REI-{i:02d}", kind="reinject", setup=setup, assertions=assertions, action="reinject"))
    return out


# ---------- Build 91 cases ----------

def _build_cases() -> list[Case]:
    cases: list[Case] = []
    cases += _gen_success_cases(25)           # 25
    cases += _gen_asr_inferred_cases(10)      # 10
    cases += _gen_unverified_cases(6)         # 6
    cases += _gen_rule_c_cases(8)             # 8
    cases += _gen_rule_b_cases(8)             # 8
    cases += _gen_http429_cases(8)            # 8
    cases += _gen_reprompt_cases(6)           # 6
    cases += _gen_sync_invalid_cases(6)       # 6
    cases += _gen_no_transcript_cases(5)      # 5
    cases += _gen_empty_rules_cases(3)        # 3
    cases += _gen_archived_dedup_cases(3)     # 3
    cases += _gen_removed_cases(2)            # 2
    cases += _gen_reinject_cases(1)           # 1
    assert len(cases) == 91, f"expected 91 generated cases, got {len(cases)}"
    return cases


CASES = _build_cases()


@pytest.mark.parametrize("case", CASES, ids=[c.cid for c in CASES])
def test_generated(case: Case):
    store = JSONStore()
    logger = EventLogger()
    payload, services = case.setup(store, logger)
    if case.action == "run":
        run_pipeline(payload, services, store, logger)
    elif case.action == "reinject":
        manual_reinject(payload, store, logger, reason="fix")
    elif case.action == "detect_removed":
        detect_removed(payload, services, store, logger)
    case.assertions(payload, store, logger)


def test_total_case_count_is_100():
    """Meta-check: 9 canonical + 91 generated = 100."""
    from tests import test_e2e_canonical
    canonical = [n for n in dir(test_e2e_canonical) if n.startswith("test_sc")]
    assert len(canonical) == 9
    assert len(CASES) == 91
