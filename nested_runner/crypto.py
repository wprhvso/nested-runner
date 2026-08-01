from __future__ import annotations

import logging
import shutil
import subprocess

from nested_runner.config import GH_TIMEOUT, public_key_path
from nested_runner.errors import NestedError

log = logging.getLogger("nested")

_AGE_HINT = "age не найден — ставь: https://github.com/FiloSottile/age"


def recipient() -> str:
    path = public_key_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NestedError(f"не прочитал публичный ключ {path}: {exc}") from None

    for line in raw.splitlines():
        candidate = line.strip()
        if candidate.startswith("age1"):
            return candidate
    raise NestedError(f"в {path} нет ключа age1...")


def check_age() -> None:
    if shutil.which("age") is None:
        raise NestedError(_AGE_HINT)


def encrypt(plaintext: str) -> str:
    check_age()
    try:
        proc = subprocess.run(
            ["age", "-a", "-r", recipient()],
            input=plaintext,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise NestedError(_AGE_HINT) from None
    except subprocess.TimeoutExpired:
        raise NestedError("age не ответил") from None

    if proc.returncode != 0:
        raise NestedError(f"age не зашифровал: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    if not out:
        raise NestedError("age вернул пустой шифротекст")
    return out
