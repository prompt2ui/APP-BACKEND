# src/services/provider_store.py
"""DB access for Provider rows (credentials stored in Postgres / Supabase)."""
from __future__ import annotations

import json
from typing import Any, Optional

from psycopg2.extras import Json

from ..database import execute_query

VALID_PROVIDER_TYPES = frozenset({"github", "clickup", "jira"})



def get_provider_key(user_id: int, provider_type: str) -> Optional[dict[str, Any]]:
    if provider_type not in VALID_PROVIDER_TYPES:
        return None
    row = execute_query(
        """
        SELECT provider_id, user_id, provider_type::text AS provider_type, provider_api_key, provider_config, created_at, updated_at
        FROM "Provider"
        WHERE user_id = %s AND provider_type::text = %s
        """,
        (user_id, provider_type),
        fetch="one",
    )
    return row


def list_providers_for_user(user_id: int) -> list[dict[str, Any]]:
    """List integrations without exposing full API keys."""
    rows = execute_query(
        """
        SELECT provider_id, provider_type::text AS provider_type, provider_config, created_at, updated_at,
               (provider_api_key IS NOT NULL AND length(trim(provider_api_key)) > 0) AS has_secret
        FROM "Provider"
        WHERE user_id = %s
        ORDER BY provider_type::text
        """,
        (user_id,),
        fetch="all",
    )
    out = []
    for r in rows or []:
        cfg = r.get("provider_config")
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except Exception:
                cfg = {}
        # Mask any token-like fields in config for list view
        safe_cfg = dict(cfg) if isinstance(cfg, dict) else {}
        if "api_token" in safe_cfg:
            safe_cfg["api_token"] = "***"
        if isinstance(safe_cfg.get("email"), str) and "@" in safe_cfg["email"]:
            em = safe_cfg["email"]
            safe_cfg["email"] = em[:2] + "***@" + em.split("@", 1)[-1]
        out.append(
            {
                "provider_id": r.get("provider_id"),
                "provider_type": r.get("provider_type"),
                "has_secret": bool(r.get("has_secret")),
                "provider_config": safe_cfg,
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
            }
        )
    return out


def upsert_provider(
    user_id: int,
    provider_type: str,
    provider_api_key: Optional[str],
    provider_config: Any,
) -> dict:
    ptype = (provider_type or "").strip().lower()
    if ptype not in VALID_PROVIDER_TYPES:
        raise ValueError(f"Invalid provider type: {provider_type}")

    api_key = provider_api_key
    cfg: Any = provider_config
    if ptype == "clickup":
        from .clickup_config import normalize_clickup_provider

        api_key, cfg = normalize_clickup_provider(api_key, cfg)
    if ptype == "jira":
        from .jira_config import normalize_jira_provider

        api_key, cfg = normalize_jira_provider(api_key, cfg)
    if ptype == "github":
        from .github_config import normalize_github_provider

        api_key, cfg = normalize_github_provider(api_key, cfg)

    if cfg is not None and not isinstance(cfg, dict):
        cfg = {}
    # None → NULL; dict (incl. {}) → jsonb via psycopg2 Json
    cfg_param = None if cfg is None else Json(cfg)

    row = execute_query(
        """
        INSERT INTO "Provider" (
            user_id, provider_type, provider_api_key, provider_config,
            created_at, updated_at
        )
        VALUES (%s, %s::"ProviderType", %s, %s, NOW(), NOW())
        ON CONFLICT (user_id, provider_type) DO UPDATE SET
            provider_api_key = COALESCE(NULLIF(trim(EXCLUDED.provider_api_key), ''), "Provider".provider_api_key),
            provider_config = COALESCE(EXCLUDED.provider_config, "Provider".provider_config),
            updated_at = NOW()
        RETURNING provider_id, user_id, provider_type::text AS provider_type, provider_config, created_at, updated_at
        """,
        (user_id, ptype, api_key, cfg_param),
        fetch="one",
    )
    return row
