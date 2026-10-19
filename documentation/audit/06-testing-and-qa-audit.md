# 06 — QA ба тестийн аудит

## Ажиллуулсан шалгалт

Repository-оос гаднах snapshot-д эхний test discovery-оор 54 тест цугларсан. Бодит run:

| Файл | Үр дүн |
|---|---|
| `tests/test_core.py` + `tests/test_sdk.py` | 28 passed |
| `tests/test_native.py` + `tests/test_vector_restart.py` | 4 passed, 1 skipped |
| Нийт | 32 passed, 1 skipped; 21 integration тест ажиллаагүй |

Native skip нь бодит Монгол OCR хэрэгсэл/data байхгүйгээс болсон. Энэ нь application failure биш. `uv pip check` 120 суусан package-ийн dependency consistency-г PASS гэж гаргасан. Coverage хэмжээгүй.

## Integration safety

`tests/conftest.py` нь TEST_DATABASE_URL-ийн final database name `rag_test`-ээр эхэлбэл `drop_all/create_all/drop_all` ажиллуулна (`:43-63`). Энэ guard disposable DB мөн эсэхийг батлахгүй. Одоогийн PostgreSQL service ажиллаж байсан ч тухайн database-ийн агуулга/тусгаарлалтыг шалгаагүй. Тиймээс database route/lifecycle integration-ийг **NOT TESTED** гэж үлдээсэн; үйл ажиллагааны оноо болон readiness энэ хязгаарлалтыг тусгана. Test runner нь test output-ийг external basetemp-д бичсэн.

`tests/test_integration.py` smoke evaluation нь `test-results/mock-evaluation.json`-ийг repository root дотор бичдэг; тиймээс зөвхөн snapshot дээр ажиллуулах ёстой. `tests/browser_server.py` hardcoded `rag_browser_test`, repo-local `instance/browser-check` ашигладаг учир audit harness болгон ажиллуулаагүй.

## Test чанар ба coverage gap

Core tests chunking, normalization, RRF, metrics, provider mocks болон structured output-ийг hand-calculated assertions-оор хамардаг. SDK tests HTTPX MockTransport ашиглаж бодит API дуудаагүй. Lifecycle integration тестүүд rollback, reconcile, migration, session/CSRF зэрэг үнэ цэнтэй сценаритай боловч одоогийн run-д баталгаажаагүй.

Coverage, vulnerability, static style/dead-code scanner суусангүй (`coverage`, `ruff`, `bandit`, `pip_audit`, `vulture` байхгүй). Шинэ хэрэгсэл суулгаагүй. **AUDIT-001**-ээс гадна `ranking_metrics`-ийн олон gold unit нэг hit-д таарах edge case тестгүй бөгөөд энэ нь **AUDIT-004**-ийг орхисон.
