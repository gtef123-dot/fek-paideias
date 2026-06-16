"""Polite HTTP helpers: browser UA, inter-request delay, retries with backoff."""
from __future__ import annotations

import time

import requests

from . import config

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})


def _retry(method: str, url: str, **kwargs) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = _session.request(
                method, url, timeout=config.REQUEST_TIMEOUT, **kwargs
            )
            resp.raise_for_status()
            time.sleep(config.REQUEST_DELAY)
            return resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = config.REQUEST_DELAY * (2 ** attempt)
            print(f"   [net] {method} {url} failed ({exc}); retry in {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {config.MAX_RETRIES} retries: {url}") from last_exc


def get(url: str, **kwargs) -> requests.Response:
    return _retry("GET", url, **kwargs)


def post(url: str, **kwargs) -> requests.Response:
    return _retry("POST", url, **kwargs)
