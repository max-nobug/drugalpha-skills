"""Minimal OpenAI-compatible DeepSeek HTTP client.

The client reads all credentials and model settings from environment variables
and never logs API keys or embeds them in generated artifacts.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


class DeepSeekConfigError(RuntimeError):
    """Raised when the local DeepSeek environment is incomplete."""


def load_local_env(path: str | Path = ".env") -> None:
    _load_shared_env()
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _load_shared_env() -> None:
    configured = os.getenv('DRUGALPHA_LLM_ENV_FILE')
    if configured == 'none':
        return
    target = Path(configured) if configured else (Path.home()/'.config/DrugAlpha/llm.env' if os.name == 'nt' else Path('/srv/innovest/shared/runtime/llm.env'))
    if not target.exists():
        if configured:
            raise DeepSeekConfigError('shared_llm_file_missing')
        return
    values = {}
    for line in target.read_text(encoding='utf-8-sig').splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() in {'LLM_PROVIDER','LLM_API_KEY','LLM_BASE_URL','LLM_MODEL'}:
            values[key.strip()] = value.strip().strip('"\'')
    if len(values) != 4 or not all(values.values()):
        raise DeepSeekConfigError('shared_llm_configuration_incomplete')
    os.environ.update(values)
    os.environ.update(DEEPSEEK_API_KEY=values['LLM_API_KEY'], DEEPSEEK_API_TOKEN=values['LLM_API_KEY'], DEEPSEEK_BASE_URL=values['LLM_BASE_URL'], DEEPSEEK_MODEL=values['LLM_MODEL'])


def chat_completion(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 1800,
) -> dict[str, Any]:
    load_local_env()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model = os.getenv("LLM_MODEL") or "deepseek-chat"
    provider = (os.getenv("LLM_PROVIDER") or "deepseek").lower()
    timeout_seconds = max(15, min(300, int(os.getenv("LLM_TIMEOUT_SECONDS") or "120")))
    if provider and "deepseek" not in provider and provider != "opencode":
        raise DeepSeekConfigError(f"LLM_PROVIDER {provider!r} is not supported by DrugAlpha AI.")
    if not api_key:
        raise DeepSeekConfigError("Missing LLM_API_KEY or DEEPSEEK_API_KEY.")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    errors: list[str] = []
    for url in _candidate_urls(base_url):
        request = urllib.request.Request(
            url,
            data=body,
            headers=_model_headers(base_url, api_key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            errors.append(f"{exc.code} {url}: {detail}")
            if exc.code not in {404, 405}:
                break
        except urllib.error.URLError as exc:
            errors.append(f"{url}: {exc.reason}")
            break
        except (TimeoutError, socket.timeout) as exc:
            errors.append(f"{url}: response timeout after {timeout_seconds}s ({exc})")
            break
    raise RuntimeError("DrugAlpha AI request failed: " + " | ".join(errors))


def extract_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _model_headers(base_url: str, api_key: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = urllib.parse.urlsplit(base_url)
    if url.scheme == 'https' and url.netloc == 'opencode.ai' and url.path.startswith('/inference/go/'):
        headers.update({'User-Agent': 'DrugAlpha-dcf/1.0', 'x-opencode-session': 'ses_' + uuid.uuid4().hex})
    return headers


def _candidate_urls(base_url: str) -> list[str]:
    normalized = base_url.strip().rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        normalized = "https://" + normalized
    parsed = urllib.parse.urlparse(normalized)
    if parsed.path.endswith("/chat/completions"):
        return [normalized]
    if parsed.path.endswith("/v1"):
        return [normalized + "/chat/completions"]
    return [normalized + "/chat/completions", normalized + "/v1/chat/completions"]
