"""User lookup — resolve user_id -> username.

The submissions and grades endpoints carry `user_id` and `email` but NOT the
username. The username comes from GET /v2/users/{user_id}. This module resolves a
set of user ids to usernames with an in-memory cache, degrading gracefully (a user
that can't be fetched yields an empty username + a warning, never aborts the run).
"""

from __future__ import annotations

from .client import LearnWorldsClient
from .config import ExtractorError


def get_user(client: LearnWorldsClient, user_id: str) -> dict | None:
    """GET a single user record (or None on a non-fatal lookup failure)."""
    try:
        data = client.get(f"{client.api_url}/v2/users/{user_id}")
    except ExtractorError:
        return None
    return data if isinstance(data, dict) else None


def resolve_usernames(
    client: LearnWorldsClient, user_ids
) -> tuple[dict, list]:
    """Resolve distinct user ids to usernames.

    Returns (username_map, missing_ids):
        username_map : {user_id: username} ("" when not resolvable)
        missing_ids  : ids whose username could not be fetched
    """
    username_map: dict = {}
    missing: list = []
    distinct = [u for u in dict.fromkeys(user_ids) if u not in (None, "")]
    total = len(distinct)
    if total:
        print(f"Resolving usernames for {total} user(s)...")
    for uid in distinct:
        user = get_user(client, uid)
        username = ""
        if user is not None:
            username = (user.get("username") or "").strip()
        username_map[uid] = username
        if not username:
            missing.append(uid)
    return username_map, missing
