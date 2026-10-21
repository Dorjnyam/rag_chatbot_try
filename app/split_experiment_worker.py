"""Child process for private, isolated A1-A3 document runs."""
import json
import sys


def main():
    from .split_experiments import _worker_document
    sys.stdin.reconfigure(encoding="utf8", errors="strict")
    sys.stdout.reconfigure(encoding="utf8", errors="strict")
    request = json.load(sys.stdin)
    result = _worker_document((request["doc_id"], request["blocks"], request["strategies"], request["snapshot_id"]))
    json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
