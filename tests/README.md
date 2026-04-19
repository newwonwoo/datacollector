# E2E Tester

End-to-end test harness for the v10 YouTube Source Adapter design. Runs 100
cases against a mocked YouTube / LLM / Git Sync backend.

## Layout
- `collector/` — minimal reference implementation (payload, store, events, stages, pipeline, mocks).
- `tests/test_e2e_canonical.py` — 9 canonical scenarios from Appendix_B.
- `tests/test_e2e_100.py` — 91 parameterized generated cases. Together = 100.

## Case breakdown (100)
| Bucket | Count |
|---|---|
| Canonical (Appendix_B SC-01..09) | 9 |
| New collect success | 25 |
| ASR-source → inferred | 10 |
| Low similarity → unverified | 6 |
| Rule C duplicate (active) | 8 |
| Rule B reprocess | 8 |
| HTTP_429 retry_wait | 8 |
| LLM reprompt then success | 6 |
| Sync failure → invalid + DLQ | 6 |
| YT_NO_TRANSCRIPT | 5 |
| SEMANTIC_EMPTY_RULES | 3 |
| Rule C against ARCHIVED | 3 |
| Video removed → REMOVED | 2 |
| Manual reinject | 1 |

## Run
```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Notes
- No network or real API keys needed.
- The tester exercises stage transitions, record/stage/run state machine,
  event log invariants, dedup Rule A/B/C, DLQ routing, and manual admin paths.
