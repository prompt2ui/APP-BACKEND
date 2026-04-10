# src/services/clickup_config.py
"""Parse ClickUp list URL / ID and normalize stored provider_config + API token."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# e.g. https://app.clickup.com/90182539799/v/li/901816867059
_CLICKUP_LI_RE = re.compile(r"/li/(\d+)", re.IGNORECASE)


def extract_clickup_list_id(url_or_id: Any) -> str:
    """
    Accepts a full ClickUp list URL or a bare numeric list ID.
    Returns the list id digits only, or "" if not found.
    """
    if url_or_id is None:
        return ""
    s = str(url_or_id).strip()
    if not s:
        return ""
    if s.lower().startswith("http"):
        m = _CLICKUP_LI_RE.search(s)
        if m:
            return m.group(1)
        # fallback: last path segment if all digits
        parts = [p for p in s.split("/") if p]
        if parts and parts[-1].isdigit():
            return parts[-1]
        return ""
    # bare ID (digits / spaces)
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits


def resolve_clickup_list_id(cfg: dict[str, Any]) -> str:
    """Use saved list_id, or parse from list_url (for older rows)."""
    lid = str(cfg.get("list_id") or "").strip()
    if lid:
        return lid
    return extract_clickup_list_id(str(cfg.get("list_url") or ""))


def _first_nonempty_str(*values: Any) -> str:
    """Skip None and blank strings; coerce numbers to str."""
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _as_config_dict(provider_config: Any) -> dict:
    if provider_config is None:
        return {}
    if isinstance(provider_config, dict):
        return dict(provider_config)
    if isinstance(provider_config, str):
        try:
            parsed = json.loads(provider_config)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def normalize_clickup_provider(
    provider_api_key: Optional[str],
    provider_config: Any,
) -> tuple[Optional[str], dict]:
    """
    - Strip API token whitespace.
    - From provider_config, accept list_url and/or list_id (frontend may paste URL in either field).
    - Persist list_url when input was a URL; always persist list_id for API calls.
    """
    cfg = _as_config_dict(provider_config)
    token = (provider_api_key or "").strip() or None

    # Prefer non-empty list_url, else list_id (JSON may send numeric id without list_url)
    raw = _first_nonempty_str(cfg.get("list_url"), cfg.get("list_id"))
    list_id = extract_clickup_list_id(raw)
    if not list_id:
        raise ValueError(
            "ClickUp: provide a list URL (e.g. https://app.clickup.com/.../v/li/901816867059) or list ID."
        )

    out: dict = {"list_id": list_id}
    if raw.lower().startswith("http"):
        out["list_url"] = raw.split("?")[0].strip().rstrip("/")

    return token, out
