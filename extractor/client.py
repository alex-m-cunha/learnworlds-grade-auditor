"""Base HTTP client for the LearnWorlds API.

Responsibilities ONLY:
- build the base URL + auth headers (Bearer token + Lw-Client);
- perform GET requests with retries/backoff;
- follow `meta`-based pagination generically;
- raise clean, typed errors.

This module contains NO assessment-specific knowledge. It never logs the token.
"""

from __future__ import annotations

import time

from .config import ExtractorError

MAX_PAGES = 500
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LearnWorldsClient:
    def __init__(
        self,
        api_url: str,
        school_id: str,
        access_token: str,
        timeout: int = 60,
        max_retries: int = 3,
    ):
        self.api_url = api_url.rstrip("/")
        self._school_id = school_id
        self._access_token = access_token
        self.timeout = timeout
        self.max_retries = max_retries

        try:
            import requests
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ExtractorError(
                "Missing dependency 'requests'. Install requirements first:\n"
                "    pip install -r requirements.txt"
            ) from exc
        self._requests = requests
        self._session = requests.Session()

    # -- internals ---------------------------------------------------------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Lw-Client": self._school_id,
            "Accept": "application/json",
        }

    def _safe_url(self, url: str) -> str:
        """URL for logging — never contains the token (it's a header, not a query)."""
        return url

    def get(self, url: str) -> dict:
        """GET a single URL with retries; return parsed JSON.

        Raises ExtractorError on auth failure, persistent HTTP errors, network
        errors, or invalid JSON. The token is never included in messages.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._session.get(
                    url, headers=self._headers(), timeout=self.timeout
                )
            except self._requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(2 ** (attempt - 1))
                    continue
                raise ExtractorError(
                    f"Network error contacting LearnWorlds API "
                    f"({self._safe_url(url)}):\n    {exc}"
                ) from exc

            if response.status_code in (401, 403):
                raise ExtractorError(
                    f"Authentication failed (HTTP {response.status_code}). The "
                    "LearnWorlds Access Token is missing, expired, or invalid.\n"
                    "Add a VALID token to .env. This tool never creates or "
                    "refreshes tokens."
                )

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                time.sleep(2 ** (attempt - 1))
                continue

            if response.status_code >= 400:
                snippet = (response.text or "")[:500] or "(no body)"
                raise ExtractorError(
                    f"LearnWorlds API returned HTTP {response.status_code} for "
                    f"{self._safe_url(url)}.\nResponse: {snippet}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise ExtractorError(
                    "LearnWorlds API did not return valid JSON.\n"
                    f"First 500 chars: {(response.text or '')[:500]}"
                ) from exc

        # Exhausted retries on a retryable status / network error.
        raise ExtractorError(
            f"LearnWorlds API request failed after {self.max_retries} attempts "
            f"({self._safe_url(url)}): {last_exc}"
        )

    def get_paginated(self, path: str) -> tuple[list, list]:
        """GET a collection endpoint, following `meta` pagination.

        `path` is appended to the API base (e.g. "/v2/assessments/<id>/responses").
        Returns (combined_data, raw_pages):
            combined_data : every item from each page's `data` list, concatenated
            raw_pages     : the full list of raw page payloads (for raw saving)
        """
        endpoint = f"{self.api_url}{path}"
        raw_pages: list = []
        combined_data: list = []
        url = endpoint

        for page_number in range(1, MAX_PAGES + 1):
            page = self.get(url)
            raw_pages.append(page)
            if isinstance(page, dict) and isinstance(page.get("data"), list):
                combined_data.extend(page["data"])
            elif isinstance(page, list):
                combined_data.extend(page)

            next_url = _next_page_url(page, endpoint)
            if not next_url:
                break
            if page_number == MAX_PAGES:
                # Safety guard; surfaced by the caller via the report.
                break
            url = next_url

        return combined_data, raw_pages


def _next_page_url(page, base_url: str):
    """Return the URL for the next page, or None if this is the last page.

    Primary signal (confirmed for LearnWorlds): meta.page < meta.totalPages.
    Defensive fallbacks: links.next / next style keys.
    """
    if not isinstance(page, dict):
        return None

    meta = page.get("meta")
    if isinstance(meta, dict):
        current = _as_int(meta.get("page"))
        total = _as_int(meta.get("totalPages"))
        if current is not None and total is not None and current < total:
            sep = "&" if "?" in base_url else "?"
            return f"{base_url}{sep}page={current + 1}"

    for key in ("next", "nextPage", "next_page"):
        value = page.get(key)
        if isinstance(value, str) and value:
            return value
    for container in ("links", "_links"):
        node = page.get(container)
        if isinstance(node, dict):
            nxt = node.get("next")
            if isinstance(nxt, str) and nxt:
                return nxt
            if isinstance(nxt, dict) and isinstance(nxt.get("href"), str):
                return nxt["href"]
    return None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
