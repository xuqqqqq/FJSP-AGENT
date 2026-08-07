"""Runtime capability probes shared by the Web service and orchestration."""

from __future__ import annotations

import platform
import sys
from typing import Any


def solver_runtime_status() -> dict[str, Any]:
    """Report the interpreter and optional solver libraries used by candidates."""

    try:
        import ortools
    except Exception as exc:  # noqa: BLE001 - capability probes must remain diagnostic.
        ortools_available = False
        ortools_version = None
        ortools_error = f"{type(exc).__name__}: {exc}"
    else:
        ortools_available = True
        ortools_version = str(getattr(ortools, "__version__", "unknown"))
        ortools_error = None

    return {
        "python_executable": str(sys.executable),
        "python_version": platform.python_version(),
        "ortools_available": ortools_available,
        "ortools_version": ortools_version,
        "ortools_error": ortools_error,
    }
