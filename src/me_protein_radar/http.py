from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from .io_utils import RadarError


USER_AGENT = "ME-Protein-Paper-Radar/0.1 (public academic metadata client)"


def request_bytes(
    url: str,
    *,
    timeout: int = 30,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    retries: int = 2,
) -> bytes:
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml, */*"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST" if data is not None else "GET")
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RadarError(f"Request failed after {retries + 1} attempts: {url}: {type(last_error).__name__}")


def request_json(url: str, **kwargs: Any) -> Any:
    payload = request_bytes(url, **kwargs)
    try:
        return json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RadarError(f"Invalid JSON response from {url}") from exc

