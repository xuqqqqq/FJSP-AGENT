"""Read and briefly cache the models actually exposed by a configured service."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any


_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def discover_model_catalog(provider: str, base_url: str, api_key: str) -> dict[str, Any]:
    key = (provider, base_url.rstrip("/"), hashlib.sha256(api_key.encode()).hexdigest())
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now < cached[0]:
        return cached[1]
    request = urllib.request.Request(
        key[1] + "/models",
        headers={
            "Authorization": "Bearer " + api_key,
            "User-Agent": "Mozilla/5.0 FJSP-Model-Catalog/1.0",
        },
    )
    result: dict[str, Any] = {"models": [], "error": None}
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.load(response).get("data")
        if not isinstance(data, list):
            raise ValueError("Model endpoint did not return a data array")
        models = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            remote_id = item["id"].strip()
            if not remote_id:
                continue
            model = {"id": f"{provider}/{remote_id}"}
            limit = item.get("max_model_len") or item.get("context_length")
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                model["context_limit"] = limit
            models[model["id"]] = model
        result["models"] = list(models.values())
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTP {exc.code}"
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result["error"] = type(exc).__name__
    _CACHE[key] = (now + (60 if result["error"] is None else 5), result)
    return result
