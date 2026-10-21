"""Separate process entry point; never starts a Flask app."""
import json
import sys
import time
import os
from pathlib import Path
from .parsing import parse_direct


def _peak_rss():
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = Counters(); counters.cb = ctypes.sizeof(counters)
            kernel = ctypes.windll.kernel32
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.windll.psapi
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            ok = psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
            return (int(counters.PeakWorkingSetSize), "windows_peak_working_set") if ok else (None, None)
        except Exception:
            return None, None
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024), "getrusage_peak_rss"
    except Exception:
        return None, None


def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
    started, cpu_started = time.perf_counter(), time.process_time()
    try:
        result = parse_direct(Path(spec["source"]), spec["extension"], spec["config"], spec["forced_pages"])
        code = 0
    except Exception as exc:
        result, code = {"error": str(exc)}, 1
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")
    if spec.get("metrics"):
        peak, source = _peak_rss()
        Path(spec["metrics"]).write_text(json.dumps({"cpu_ms": round((time.process_time() - cpu_started) * 1000, 2),
            "worker_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "peak_memory_bytes": peak, "memory_source": source}), encoding="utf8")
    return code


if __name__ == "__main__":
    sys.exit(main())
