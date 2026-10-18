"""Separate process entry point; never starts a Flask app."""
import json
import sys
from pathlib import Path
from .parsing import parse_direct


def main():
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
    try:
        result = parse_direct(Path(spec["source"]), spec["extension"], spec["config"], spec["forced_pages"])
        code = 0
    except Exception as exc:
        result, code = {"error": str(exc)}, 1
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf8")
    return code


if __name__ == "__main__":
    sys.exit(main())
