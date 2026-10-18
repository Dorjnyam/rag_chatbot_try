"""Small filesystem helpers and a cross-process local mutation lock."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from flask import current_app


def root():
    return Path(current_app.config["DATA_DIR"]).resolve()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def path(relative):
    result = (root() / relative).resolve()
    if not result.is_relative_to(root()):
        raise ValueError("Artifact path outside application data directory")
    return result


def write_json(relative, value):
    target = path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")
    temporary.replace(target)


def read_json(relative):
    return json.loads(path(relative).read_text(encoding="utf8"))


@contextmanager
def mutation_lock():
    lock = root() / "mutation.lock"
    with lock.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Another document operation is running. Retry after it finishes.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
