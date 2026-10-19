# Аудитын command log

2026-10-19 (Asia/Ulaanbaatar), Python 3.12.15. Commands run from external snapshot `C:\Users\dorjnyam.e\AppData\Local\Temp\rag-audit-20261019\snapshot`; interpreter is the workspace `.venv` executable. No `.env`, `instance`, credentials, company documents, or API requests were copied.

| Command / check | Result |
|---|---|
| `python -m pytest --collect-only -q -p no:cacheprovider` | 54 tests collected, 0.93s |
| `python -m pytest -q -p no:cacheprovider --basetemp <external> tests/test_core.py tests/test_sdk.py` | 28 passed, 2.12s |
| `python -m pytest -q -p no:cacheprovider --basetemp <external> tests/test_native.py tests/test_vector_restart.py` | 4 passed, 1 skipped, 16.08s |
| `uv pip check --python .venv\Scripts\python.exe` | Checked 120 packages; all installed packages compatible |
| `git diff --check` | No whitespace errors |
| Import availability probe | `coverage`, `ruff`, `vulture`, `bandit`, `pip_audit`, `sentence_transformers` unavailable |
| Database integration tests | NOT TESTED; fixture destructive guard is prefix-only, dedicated disposable DB isolation not established |
| Browser visual verification | BLOCKED by saved browser permission denial; no workaround attempted |

Combined runtime outcome: **32 PASS, 1 SKIPPED**. The remaining 21 tests are database/Flask integration tests and were intentionally not run. The one skipped native test needs local OCR language data/tooling. The test suite output was captured by the audit operator; this file records the outcome, not a synthetic claim of full-suite success.

`final-integrity-comparison.json` records identical hashes for 82 source/config/documentation files between the initial snapshot and final working tree. HEAD advanced from `2cdd1d1aae48d08dc2e2232d58becdabbabf8249` to `6c2840a12936dae96bda2fe8bd7524c87fba5fd1` during the audit; the checked content hashes did not change. Only `documentation/audit/` was created by the audit.
