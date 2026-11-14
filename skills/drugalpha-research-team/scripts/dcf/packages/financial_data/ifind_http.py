"""Minimal iFinD HTTP client.

Tokens are read only from environment variables and are never logged.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_BASE_URL = "https://quantapi.51ifind.com/api/v1"
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0


class IFindError(RuntimeError):
    pass


@dataclass
class IFindHttpClient:
    refresh_token: str
    base_url: str = DEFAULT_BASE_URL
    timeout: int = 30
    _access_token: str | None = None

    @classmethod
    def from_env(cls) -> "IFindHttpClient":
        token = (
            os.environ.get("THS_QUANTAPI_REFRESH_TOKEN")
            or os.environ.get("THS_REFRESH_TOKEN")
            or os.environ.get("IFIND_REFRESH_TOKEN")
        )
        if not token:
            raise IFindError("Missing THS_QUANTAPI_REFRESH_TOKEN/THS_REFRESH_TOKEN/IFIND_REFRESH_TOKEN.")
        return cls(refresh_token=token, base_url=os.environ.get("THS_QUANTAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/"))

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    @staticmethod
    def _retry_attempts() -> int:
        try:
            return max(1, int(os.environ.get("IFIND_HTTP_RETRY_ATTEMPTS", DEFAULT_RETRY_ATTEMPTS)))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_ATTEMPTS

    @staticmethod
    def _retry_backoff_seconds() -> float:
        try:
            return max(0.0, float(os.environ.get("IFIND_HTTP_RETRY_BACKOFF_SECONDS", DEFAULT_RETRY_BACKOFF_SECONDS)))
        except (TypeError, ValueError):
            return DEFAULT_RETRY_BACKOFF_SECONDS

    def _request(
        self,
        endpoint: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> requests.Response:
        """Retry transient QuantAPI transport/server failures without retrying bad requests."""
        attempts = self._retry_attempts()
        backoff_seconds = self._retry_backoff_seconds()
        last_error: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                request_kwargs: dict[str, Any] = {
                    "headers": headers,
                    "timeout": self.timeout,
                }
                if payload is not None:
                    request_kwargs["json"] = payload
                response = requests.post(self._url(endpoint), **request_kwargs)
            except requests.RequestException as error:
                last_error = error
                if attempt + 1 >= attempts:
                    raise
                if backoff_seconds:
                    time.sleep(backoff_seconds * (attempt + 1))
                continue

            # A malformed request, unsupported indicator, or insufficient account
            # permission is deterministic and should be surfaced immediately.
            if 400 <= response.status_code < 500 and response.status_code != 429:
                response.raise_for_status()
                return response

            try:
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt + 1 >= attempts:
                    raise
                if backoff_seconds:
                    time.sleep(backoff_seconds * (attempt + 1))
        assert last_error is not None
        raise last_error

    def get_access_token(self) -> str:
        response = self._request(
            "get_access_token",
            headers={"Content-Type": "application/json", "refresh_token": self.refresh_token},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errorcode") not in (0, None) or payload.get("errcode") not in (0, None):
            raise IFindError(f"iFinD access token error: {payload.get('errmsg') or payload.get('message')}")
        token = (payload.get("data") or {}).get("access_token")
        if not token:
            raise IFindError("iFinD access token response did not include access_token.")
        return token

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self._access_token = self.get_access_token()
        return {"Content-Type": "application/json", "access_token": self._access_token}

    def basic_data(self, code: str, indicators: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"codes": code, "indipara": indicators}
        data = self._post_json("basic_data_service", payload)
        if data.get("errorcode") != 0:
            raise IFindError(f"iFinD basic_data error: {data.get('errmsg')}")
        return data

    def date_sequence(
        self,
        code: str,
        start_date: str,
        end_date: str,
        indicators: list[dict[str, Any]],
        fill: str = "Blank",
    ) -> dict[str, Any]:
        payload = {
            "codes": code,
            "startdate": start_date,
            "enddate": end_date,
            "functionpara": {"Fill": fill},
            "indipara": indicators,
        }
        data = self._post_json("date_sequence", payload)
        if data.get("errorcode") != 0:
            raise IFindError(f"iFinD date_sequence error: {data.get('errmsg')}")
        return data

    def data_pool(self, reportname: str, functionpara: dict[str, Any], outputpara: str) -> dict[str, Any]:
        payload = {
            "reportname": reportname,
            "functionpara": functionpara,
            "outputpara": outputpara,
        }
        data = self._post_json("data_pool", payload)
        if data.get("errorcode") != 0:
            raise IFindError(f"iFinD data_pool error: {data.get('errmsg')}")
        return data

    def smart_stock_picking(self, query: str, search_type: str = "stock") -> dict[str, Any]:
        response = requests.post(
            self._url("smart_stock_picking"),
            json={"searchstring": query, "searchtype": search_type},
            headers=self._headers(),
            timeout=max(self.timeout, 60),
        )
        response.raise_for_status()
        return response.json()

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request(endpoint, headers=self._headers(), payload=payload)
        data = response.json()
        if _is_token_error(data):
            self._access_token = None
            response = self._request(endpoint, headers=self._headers(), payload=payload)
            data = response.json()
        return data


def _is_token_error(payload: dict[str, Any]) -> bool:
    message = str(payload.get("errmsg") or payload.get("message") or "").lower()
    code = str(payload.get("errorcode") or payload.get("errcode") or "")
    return code in {"-403", "401", "403"} or "token" in message
