"""Short-lived child process for private Split timing; emits anonymous metrics only."""
import json
import sys
import statistics


def main():
    from .split_benchmark import _timed_worker
    sys.stdin.reconfigure(encoding="utf8", errors="strict")
    sys.stdout.reconfigure(encoding="utf8", errors="strict")
    request = json.load(sys.stdin)
    payload = (request["blocks"], request["config"], request["version_id"],
               request["model"], request["doc_id"])
    import time
    _timed_worker(payload)  # warm-up
    cpu_start = time.process_time()
    measured = [_timed_worker(payload) for _ in range(request.get("iterations", 6)-1)]
    worker_cpu_ms = (time.process_time()-cpu_start)*1000
    result = measured[-1]
    for key in ("raw_chunk_count", "fitted_chunk_count", "covered_source_characters", "embedding_input_tokens_estimate"):
        if len({run["metrics"][key] for run in measured}) != 1:
            raise RuntimeError("Nondeterministic repeated Split result.")
    for field in ("chunking_ms", "guard_ms", "metrics_tokenization_ms", "total_ms", "cpu_ms", "chunking_cpu_ms", "guard_cpu_ms", "metrics_tokenization_cpu_ms", "chunking_python_peak_bytes", "worker_rss_sample_bytes"):
        values = [run[field] for run in measured if run.get(field) is not None]
        result[field] = statistics.median(values) if values else None
    result["iterations"] = len(measured)
    result["warmup_runs"] = 1
    result["worker_cpu_total_ms"] = worker_cpu_ms
    result["worker_cpu_per_iteration_ms"] = worker_cpu_ms/max(1, len(measured))
    result["status"] = "split_success"
    result["doc_id"] = request["doc_id"]
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
