from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ProviderError


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload=response.read().decode("utf-8")
            return json.loads(payload) if payload.strip() else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Cannot reach {url}: {exc}") from exc

