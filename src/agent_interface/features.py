"""Shipped-feature registry — tracks autonomously-shipped agi features and
judges each by whether it actually gets *used*.

The goal is explicit: an auto-shipped feature that nobody uses is a failure, not
a success. The optimizer registers each feature it ships here; after a grace
window, :func:`evaluate` consults the usage ledger (:mod:`agent_interface.usage`)
and marks every feature ``used`` or ``failed``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

MANIFEST_PATH = Path.home() / ".config" / "agi" / "features.json"
DEFAULT_GRACE_SECONDS = 2 * 24 * 60 * 60  # 2 days for a feature to get used


def _load() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"features": []}
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {"features": []}


def _save(data: dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, indent=2))


def register(
    feature_id: str,
    title: str,
    *,
    task_id: Optional[str] = None,
    helps: Optional[str] = None,
    now: Optional[float] = None,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> None:
    """Record a freshly-shipped feature so its usage can be judged later."""
    data = _load()
    if any(f["id"] == feature_id for f in data["features"]):
        return  # idempotent
    data["features"].append({
        "id": feature_id,
        "title": title,
        "task_id": task_id,
        "helps": helps,
        "shipped_at": time.time() if now is None else now,
        "grace_seconds": grace_seconds,
        "status": "shipped",
    })
    _save(data)


def list_features() -> list[dict[str, Any]]:
    return _load()["features"]


def evaluate(conn, now: Optional[float] = None) -> dict[str, list]:
    """Judge shipped features whose grace window has elapsed.

    used   = at least one recorded use since it shipped.
    failed = grace elapsed with zero uses.
    Pending features (still within grace) are left untouched.
    """
    from agent_interface.usage import usage_count

    now = time.time() if now is None else now
    data = _load()
    used: list[dict] = []
    failed: list[dict] = []
    changed = False

    for f in data["features"]:
        if f["status"] != "shipped":
            continue
        if now - f["shipped_at"] < f["grace_seconds"]:
            continue  # still within grace
        count = usage_count(conn, f["id"], since=f["shipped_at"])
        f["status"] = "used" if count > 0 else "failed"
        f["uses"] = count
        changed = True
        (used if count > 0 else failed).append(f)

    if changed:
        _save(data)
    if used or failed:
        _notify(used, failed)
    return {"used": used, "failed": failed}


def _notify(used: list[dict], failed: list[dict]) -> None:
    try:
        from agent_interface.telegram import send_message

        lines = ["📈 <b>feature usage verdict</b>"]
        for f in used:
            lines.append(f"✅ used ({f.get('uses', 0)}×): {f['title'][:55]}")
        for f in failed:
            lines.append(f"❌ unused (failure): {f['title'][:55]}")
        send_message("\n".join(lines))
    except Exception:  # noqa: BLE001
        pass
