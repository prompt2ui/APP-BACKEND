# src/services/jira_config.py
"""Parse Jira Cloud project URLs and normalize stored Provider rows."""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

# e.g. .../projects/KAN/... or .../projects/KAN/boards/1
_PROJECT_KEY_IN_PATH = re.compile(r"/projects/([^/]+)/?", re.IGNORECASE)


def parse_jira_project_url(project_url: str) -> tuple[str, str]:
    """
    From a pasted browser URL, return (site_hostname, project_key).
    Example: https://thesisjiraproject.atlassian.net/jira/software/projects/KAN/list
    -> ("thesisjiraproject.atlassian.net", "KAN")
    """
    raw = (project_url or "").strip()
    if not raw.lower().startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Jira: could not read site from Project URL.")
    match = _PROJECT_KEY_IN_PATH.search(parsed.path or "")
    if not match:
        raise ValueError(
            "Jira: could not find project key in URL. Paste a project board URL "
            "(e.g. .../jira/software/projects/KAN/list) from the address bar."
        )
    project_key = match.group(1).strip().upper()
    if not project_key:
        raise ValueError("Jira: project key in URL is empty.")
    return host, project_key


def _site_hostname_from_config(cfg: dict[str, Any]) -> str:
    direct = str(cfg.get("site_hostname") or "").strip().lower()
    if direct:
        return direct
    site_url = str(cfg.get("site_url") or "").strip()
    if not site_url:
        return ""
    if not site_url.lower().startswith(("http://", "https://")):
        site_url = "https://" + site_url
    return (urlparse(site_url).hostname or "").strip().lower()


def normalize_jira_provider(
    provider_api_key: Optional[str],
    provider_config: Any,
) -> tuple[Optional[str], dict[str, Any]]:
    """
    - API token -> provider_api_key (same as ClickUp).
    - provider_config stores email, site_hostname, project_key, and optional project_url for display.
    """
    cfg: dict[str, Any]
    if provider_config is None:
        cfg = {}
    elif isinstance(provider_config, str):
        try:
            parsed = json.loads(provider_config)
            cfg = dict(parsed) if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    elif isinstance(provider_config, dict):
        cfg = dict(provider_config)
    else:
        cfg = {}

    token = (provider_api_key or "").strip() or None
    email = str(cfg.get("email") or "").strip()
    if not email:
        raise ValueError("Jira: Atlassian account email is required.")

    project_url = str(cfg.get("project_url") or "").strip()
    project_key_raw = str(cfg.get("project_key") or "").strip()

    if project_url:
        site_hostname, project_key = parse_jira_project_url(project_url)
        display_url = project_url.split("?")[0].strip().rstrip("/")
        if not display_url.lower().startswith("http"):
            display_url = "https://" + display_url.lstrip("/")
        out: dict[str, Any] = {
            "email": email,
            "site_hostname": site_hostname,
            "project_key": project_key,
            "project_url": display_url,
        }
        return token, out

    if project_key_raw:
        site_hostname = _site_hostname_from_config(cfg)
        if not site_hostname:
            raise ValueError("Jira: site URL or site_hostname is required when using project key alone.")
        out = {
            "email": email,
            "site_hostname": site_hostname,
            "project_key": project_key_raw.upper(),
        }
        if cfg.get("site_url"):
            out["site_url"] = str(cfg["site_url"]).strip().rstrip("/")
        return token, out

    raise ValueError("Jira: paste a Project URL from the browser, or set project_key with site URL.")
