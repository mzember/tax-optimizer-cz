"""Thin HTTP wrapper with injectable mock for tests."""

import time
import urllib.request
import urllib.error
from typing import Callable

_mock: Callable[[str], bytes] | None = None


def inject_mock(fn: Callable[[str], bytes] | None) -> None:
    global _mock
    _mock = fn


def get(url: str, retry: int = 3, delay: float = 2.5) -> bytes:
    if _mock is not None:
        return _mock(url)
    last_exc: Exception | None = None
    for attempt in range(retry):
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                return resp.read()
        except urllib.error.URLError as e:
            last_exc = e
            if attempt < retry - 1:
                time.sleep(delay)
    raise RuntimeError(f"HTTP GET failed after {retry} attempts: {url}") from last_exc
