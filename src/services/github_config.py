# src/services/github_config.py
"""Normalize GitHub Provider rows: Personal Access Token + owner/repository."""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

_REPO_SLUG = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _split_owner_repo(owner: str, repo_name: str) -> tuple[str, str]:
    owner = owner.strip()
    repo_name = repo_name.strip().replace(".git", "")
    if not owner or not repo_name:
        raise ValueError("GitHub: owner and repository name must not be empty.")
    if not _REPO_SLUG.match(owner) or not _REPO_SLUG.match(repo_name):
        raise ValueError("GitHub: invalid owner or repository name.")
    return owner, repo_name


def parse_github_repository_input(raw: str) -> str:
    """
    Accepts `owner/repo` or a browser/repo URL. Returns normalized `owner/repo`.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("GitHub: repository is required (owner/repository).")

    if "github.com" in text.lower():
        parsed = urlparse(text if text.lower().startswith("http") else f"https://{text}")
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= 2:
            return "/".join(_split_owner_repo(segments[0], segments[1]))
        raise ValueError("GitHub: could not parse owner/repo from the GitHub URL.")

    if "/" not in text:
        raise ValueError("GitHub: use format owner/repository (e.g. octocat/Hello-World).")

    owner, _, rest = text.partition("/")
    repo_name = rest.split("/")[0]
    owner, repo_name = _split_owner_repo(owner, repo_name)
    return f"{owner}/{repo_name}"


def normalize_github_provider(
    provider_api_key: Optional[str],
    provider_config: Any,
) -> tuple[Optional[str], dict[str, Any]]:
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

    repo_raw = (cfg.get("repo") or cfg.get("repository") or "").strip()
    if not repo_raw:
        raise ValueError("GitHub: repository (owner/name or repo URL) is required.")

    normalized_repo = parse_github_repository_input(repo_raw)
    token = (provider_api_key or "").strip() or None

    return token, {"repo": normalized_repo}
