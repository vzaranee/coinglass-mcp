"""CoinGlass HTTP client with retry logic."""

from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from coinglass_mcp.config import BASE_URL, TIMEOUT


class RateLimitError(Exception):
    """Rate limit exceeded."""

    pass


class PlanLimitError(Exception):
    """Feature requires higher plan."""

    pass


class APIError(Exception):
    """CoinGlass API error."""

    pass


def _is_retryable(exc: BaseException) -> bool:
    """Check if exception is retryable (5xx or network errors)."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.ConnectError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


@dataclass
class CoinGlassClient:
    """HTTP client for CoinGlass API with retry logic."""

    http: httpx.AsyncClient
    api_key: str
    base_url: str = BASE_URL

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable),
    )
    async def request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make authenticated GET request to CoinGlass API.

        Args:
            endpoint: API endpoint path (e.g., "/api/futures/supported-coins")
            params: Optional query parameters

        Returns:
            Response data from API

        Raises:
            RateLimitError: When rate limit is exceeded (429)
            PlanLimitError: When feature requires higher plan (403)
            APIError: When API returns error response
            httpx.HTTPStatusError: For other HTTP errors
        """
        # Filter out None values from params
        filtered_params = {k: v for k, v in (params or {}).items() if v is not None}

        response = await self.http.get(
            f"{self.base_url}{endpoint}",
            params=filtered_params if filtered_params else None,
            headers={"CG-API-KEY": self.api_key},
            timeout=TIMEOUT,
        )

        # Handle specific error codes without retry
        if response.status_code == 429:
            raise RateLimitError(
                "Rate limit exceeded. Please wait before making more requests."
            )
        if response.status_code == 403:
            raise PlanLimitError(
                "This feature requires a higher plan. Check your CoinGlass subscription."
            )
        if response.status_code == 401:
            raise APIError("Invalid API key. Please check your COINGLASS_API_KEY.")

        response.raise_for_status()
        data = response.json()

        if not isinstance(data, dict):
            raise APIError("Unexpected API response shape: expected JSON object")

        # CoinGlass API returns code "0" for success
        if data.get("code") not in (0, "0"):
            raise APIError(f"API error: {data.get('msg', 'Unknown error')}")

        return data.get("data")
