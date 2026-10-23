"""Local context/generation plumbing benchmark; never creates an API client."""
import hashlib
import importlib.metadata
import math
import json
import os
import platform
import statistics
import subprocess
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .services import rag


class _OfflineClient:
    def __init__(self, response, model):
        import tiktoken
        self.model = model or "gpt-6-luna"
        try:
            self.encoding = tiktoken.encoding_for_model(self.model)
            self.fallback = None
        except KeyError:
            self.encoding = tiktoken.get_encoding("o200k_base")
            self.fallback = "unknown_model_o200k_base"
        self.response = response
        self.generate_calls = 0

    def count(self, text, model=None):
        return len(self.encoding.encode(text))

    def tokenizer_info(self):
        return {"model": self.model, "encoding": self.encoding.name, "fallback": self.fallback}

    def generate(self, prompt, system):
        self.generate_calls += 1
        return self.response


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))]


def _distribution(values):
    return {"sample_count": len(values), "p50_ms": _percentile(values, .50),
        "p95_ms": _percentile(values, .95), "p99_ms": _percentile(values, .99),
        "mean_ms": statistics.fmean(values) if values else None}


def _synthetic_cases(count=12):
    texts = [
        "Ээлжийн амралтыг ажилтны хүсэлт, эрх бүхий удирдлагын зөвшөөрлөөр олгоно.",
        "Жилийн амралтын хоногийг ажилласан хугацаа болон хөдөлмөрийн нөхцөлөөр тооцно.",
        "Онцгой нөхцөлд шууд удирдлага болон хүний нөөцийн алба хамтран шийдвэрлэнэ.",
        "Төлбөртэй чөлөө олгох эсэхийг хүчинтэй журмын холбогдох заалтаар тогтооно.",
    ]
    return [{"case_id": f"synthetic-{index + 1}", "question": f"Жишээ бодлогын нөхцөл {index + 1} юу вэ?",
        "evidence": texts[index % len(texts)]} for index in range(count)]


def _private_cases(data_dir):
    path = Path(data_dir) / "split-evaluation" / "datasets" / "ground-truth-gt-v1.json"
    if not path.is_file():
        return [], {"available": False, "approved_questions": None, "valid_query_variations": None,
            "source_sha256": None, "reason": "verified_ground_truth_file_missing"}
    raw = path.read_bytes()
    dataset = json.loads(raw.decode("utf8"))
    if dataset.get("reviewer_confirmed") is not True:
        return [], {"available": True, "approved_questions": 0, "valid_query_variations": 0,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "reason": "dataset_not_reviewer_confirmed"}
    cases, approved = [], [item for item in dataset.get("records", []) if item.get("human_confirmed") is True]
    for index, item in enumerate(approved):
        variants = item.get("query_variants") or {}
        evidence, candidate = item.get("evidence_text"), item.get("candidate_id", "")
        for language in ("mn_cyrl", "en", "mn_latn_draft"):
            question = variants.get(language)
            if isinstance(question, str) and question.strip() and isinstance(evidence, str) and evidence.strip():
                cases.append({"case_id": "verified-" + hashlib.sha256(str(candidate).encode()).hexdigest()[:12] + "-" + language,
                    "question": question, "evidence": evidence, "case_kind": "private_verified_reference",
                    "variant_kind": language})
    return cases, {"available": True, "dataset_version": dataset.get("dataset_version"),
        "snapshot_id": dataset.get("snapshot_id"), "approved_questions": len(approved),
        "valid_query_variations": dataset.get("valid_query_variation_count"),
        "dataset_sha256": dataset.get("dataset_sha256"),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "benchmark_cases": len(cases), "content_retained": False,
        "reason": None if cases else "no_usable_question_evidence_pairs"}


def _sample(case, index, client):
    text = case["evidence"]
    identity = hashlib.sha256(case["case_id"].encode("utf8")).hexdigest()[:16]
    hit = {"id": "offline-chunk-" + identity, "title": "Benchmark fixture", "filename": "",
        "version_number": 1, "text": text,
        "metadata": {"document_id": "offline-document-" + identity,
            "version_id": "offline-version-" + identity, "char_start": 0, "char_end": len(text)}}
    response = json.dumps({"status": "answered", "answer": "Энэ мэдээлэл баталгаатай гэж зөвхөн жишээнд байна [S1].",
        "sources": ["S1"]}, ensure_ascii=False)
    client.response = response
    stage = {}
    started = time.perf_counter()
    mark = time.perf_counter()
    prompt, selected, references = rag.build_context(case["question"], [hit], [], client, include_references=True)
    stage["context_assembly_and_budget_ms"] = (time.perf_counter() - mark) * 1000
    mark = time.perf_counter()
    request_payload = json.dumps({"prompt": prompt, "evidence": selected}, ensure_ascii=False, separators=(",", ":"))
    stage["prompt_serialization_ms"] = (time.perf_counter() - mark) * 1000
    stage["serialized_request_bytes"] = len(request_payload.encode("utf8"))
    mark = time.perf_counter()
    answer, validation = rag._model_answer(client, prompt, case["question"], selected)
    stage["mock_generation_orchestration_and_validation_ms"] = (time.perf_counter() - mark) * 1000
    mark = time.perf_counter()
    rag.validate_answer(json.dumps(answer, ensure_ascii=False), selected)
    stage["output_validation_ms"] = (time.perf_counter() - mark) * 1000
    stage["end_to_end_offline_ms"] = (time.perf_counter() - started) * 1000
    stage["estimated_request_tokens"] = rag._request_token_estimate(client, prompt, rag.SYSTEM)
    stage["evidence_tokens"] = client.count(text)
    stage["validated"] = bool(answer and answer.get("sources") == ["S1"] and references["S1"]["chunk_id"] == hit["id"])
    stage["repair_attempted"] = bool(validation.get("repair_attempted"))
    stage["case_id_sha256"] = hashlib.sha256(case["case_id"].encode("utf8")).hexdigest()
    stage["iteration"] = index
    return stage


def run_offline_generation_benchmark(data_dir, config, iterations=5):
    if not 1 <= iterations <= 20:
        raise ValueError("Offline generation iterations must be between 1 and 20.")
    private_cases, annotation = _private_cases(data_dir)
    synthetic = _synthetic_cases()
    cases = private_cases + synthetic
    if not cases:
        raise ValueError("No private verified or synthetic generation fixtures are available.")
    from flask import current_app
    from flask import has_app_context
    if not has_app_context():
        raise RuntimeError("Run the offline generation benchmark inside the Flask CLI.")
    current_app.config.update(config)
    run_id = uuid.uuid4().hex[:12]
    response = json.dumps({"status": "answered", "answer": "Энэ мэдээлэл баталгаатай гэж зөвхөн жишээнд байна [S1].",
        "sources": ["S1"]}, ensure_ascii=False)
    client = _OfflineClient(response, config.get("GENERATION_MODEL"))
    tracemalloc.start()
    process_cpu_start = time.process_time()
    try:
        cold = [_sample(case, 0, client) for case in cases]
        warmup = [_sample(case, 0, client) for case in cases]
        timed = [_sample(case, repeat + 1, client) for repeat in range(iterations) for case in cases]
        _current, allocation_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    cpu_seconds = time.process_time() - process_cpu_start
    stage_names = tuple(key for key in timed[0] if key.endswith("_ms"))
    latency = {name: _distribution([row[name] for row in timed]) for name in stage_names}
    try:
        import psutil
        memory = {"python_tracemalloc_peak_bytes": allocation_peak,
            "process_peak_rss_bytes": getattr(psutil.Process(os.getpid()).memory_info(), "peak_wset", None),
            "process_current_rss_bytes": psutil.Process(os.getpid()).memory_info().rss,
            "rss_measurement": "Windows process working-set peak where available; includes benchmark process"}
    except (ImportError, OSError, AttributeError):
        memory = {"python_tracemalloc_peak_bytes": allocation_peak, "process_peak_rss_bytes": None,
            "process_current_rss_bytes": None, "rss_measurement": "unavailable"}
    try:
        git_revision = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True,
            text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_revision = None
    tracked_changes = subprocess.run(["git", "diff", "--binary", "HEAD", "--", "app", "tests"],
        capture_output=True, timeout=10).stdout
    scoped_files = [Path("app/services/rag.py"), Path("app/services/openai_provider.py"),
        Path("app/generation_benchmark.py"), Path("app/cli.py"), Path("tests/test_rag_reliability.py"),
        Path("tests/test_sdk.py"), Path("tests/test_generation_benchmark.py")]
    tree_hash = hashlib.sha256(tracked_changes)
    for path in scoped_files:
        if path.is_file():
            tree_hash.update(str(path).encode("utf8"))
            tree_hash.update(path.read_bytes())
    result = {"schema_version": 1, "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "git_revision": git_revision,
        "scoped_worktree_sha256": tree_hash.hexdigest(),
        "configuration": {"generation_model": config.get("GENERATION_MODEL"),
            "embedding_model_unchanged": config.get("EMBEDDING_MODEL"),
            "chunk_size_characters": config.get("CHUNK_SIZE"), "chunk_overlap_characters": config.get("CHUNK_OVERLAP"),
            "reasoning_effort": config.get("OPENAI_REASONING_EFFORT"),
            "context_chunks": config.get("CONTEXT_CHUNKS"), "context_tokens": config.get("CONTEXT_TOKENS"),
            "history_tokens": config.get("HISTORY_TOKENS"), "input_tokens": config.get("INPUT_TOKENS"),
            "output_tokens": config.get("OUTPUT_TOKENS"), "prompt_revision": rag.PROMPT_REVISION,
            "schema_revision": "rag-answer-v2", "tokenizer": client.tokenizer_info(), "chunker_changed": False},
        "private_annotation_dataset": annotation,
        "fixture_counts": {"private_reference_variations": len(private_cases), "synthetic_cases": len(synthetic),
            "cases_per_iteration": len(cases), "warmup_cases": len(warmup), "timed_sample_count": len(timed)},
        "input_order": "private annotation record order and language key order; synthetic fixtures appended in numeric order",
        "cold_initialization": {name: _distribution([row[name] for row in cold]) for name in stage_names},
        "latency": latency,
        "cold_sample_records": cold, "timed_sample_records": timed,
        "token_estimates": {"request_tokens_p50": _percentile([row["estimated_request_tokens"] for row in timed], .5),
            "evidence_tokens_p50": _percentile([row["evidence_tokens"] for row in timed], .5),
            "estimates_are_provider_usage": False},
        "outcomes": {"validated_samples": sum(row["validated"] for row in timed), "invalid_samples": sum(not row["validated"] for row in timed),
            "repair_calls": sum(row["repair_attempted"] for row in timed), "mock_generate_calls": client.generate_calls},
        "memory": memory, "process_cpu_seconds": cpu_seconds,
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
            "packages": {name: importlib.metadata.version(name) for name in ("Flask", "openai", "tiktoken")}},
        "network_api_calls": 0, "postgresql_access": False, "chroma_access": False,
        "raw_question_evidence_answer_retained": False,
        "sample_records_contain_query_hashes_and_measurements_only": True,
        "latency_scope": "local deterministic fake response; excludes retrieval and real provider inference"}
    result_path = Path(data_dir) / "generation-evaluation" / "offline" / run_id / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf8")
    result_path.write_bytes(raw)
    result["private_result_path"] = str(result_path)
    result["private_result_sha256"] = hashlib.sha256(raw).hexdigest()
    return result
