import os
import time
import requests
from src.utils.logger import get_logger

logger = get_logger("llm_client")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "")
        if value:
            return value
    return default


def _redact_sensitive(text: str) -> str:
    """Keep LLM URL/key out of logs, artifacts, and Telegram failure cards."""
    redacted = text or ""
    sensitive_values = [
        os.getenv("CODEX_API_KEY", ""),
        os.getenv("OPENAI_API_KEY", ""),
        os.getenv("CODEX_BASE_URL", ""),
        os.getenv("OPENAI_BASE_URL", ""),
    ]
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "<redacted>")
    # Hide arbitrary upstream URLs in exception bodies, including proxy error text.
    import re
    redacted = re.sub(r"https?://[^\s\"'<>]+", "<llm-url-redacted>", redacted)
    redacted = re.sub(r"Bearer\s+[^\s\"'<>]+", "Bearer <redacted>", redacted, flags=re.I)
    return redacted


TIMEOUT = _env_int("CODEX_TIMEOUT", _env_int("OPENAI_TIMEOUT", 60))
MAX_RETRIES = _env_int("CODEX_MAX_RETRIES", _env_int("OPENAI_MAX_RETRIES", 1))
RETRY_DELAY = _env_int("CODEX_RETRY_DELAY", _env_int("OPENAI_RETRY_DELAY", 5))
ERROR_BODY_LIMIT = _env_int("CODEX_ERROR_BODY_LIMIT", _env_int("OPENAI_ERROR_BODY_LIMIT", 500))


def _raise_for_status_with_body(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    body = _redact_sensitive((resp.text or "").strip().replace("\n", " ")[:ERROR_BODY_LIMIT])
    message = f"{resp.status_code} Client Error for LLM endpoint"
    if body:
        message = f"{message} | body: {body}"
    raise requests.HTTPError(message, response=resp)


def _is_retryable_error(error: requests.exceptions.RequestException) -> bool:
    if isinstance(error, requests.HTTPError):
        status = getattr(error.response, "status_code", None)
        return status == 429 or (status is not None and 500 <= status < 600)
    return isinstance(error, (requests.Timeout, requests.ConnectionError))


def _provider_configs(api_key: str | None, base_url: str | None, model: str) -> list[dict]:
    providers = []
    seen = set()
    codex_key = os.getenv("CODEX_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    def add(name: str, key: str | None, url: str | None, provider_model: str | None):
        if not key:
            return
        resolved_url = (url or "https://api.openai.com/v1").rstrip("/")
        resolved_model = provider_model or model
        signature = (key, resolved_url, resolved_model)
        if signature in seen:
            return
        seen.add(signature)
        providers.append({
            "name": name,
            "key": key,
            "url": resolved_url,
            "model": resolved_model,
        })

    add("codex", codex_key, os.getenv("CODEX_BASE_URL", ""), os.getenv("CODEX_MODEL", "") or model)
    if api_key and api_key not in {codex_key, openai_key}:
        add("explicit", api_key, base_url, model)
    add("openai", openai_key, os.getenv("OPENAI_BASE_URL", "") or "https://api.openai.com/v1", os.getenv("OPENAI_MODEL", "") or model)
    return providers


def chat_completion(
    messages: list[dict],
    model: str = "gpt-5.5",
    api_key: str = None,
    base_url: str = None,
    temperature: float = 0.3,
    max_tokens: int = 1000,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> str:
    providers = _provider_configs(api_key, base_url, model)
    if not providers:
        raise ValueError("CODEX_API_KEY/OPENAI_API_KEY 未设置")

    request_timeout = timeout or TIMEOUT
    retries = MAX_RETRIES if max_retries is None else max_retries
    provider_errors = []

    for provider_index, provider in enumerate(providers):
        provider_retries = 0 if provider_index < len(providers) - 1 else retries
        endpoint = f"{provider['url']}/chat/completions"
        headers = {
            "Authorization": f"Bearer {provider['key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": provider["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        for attempt in range(provider_retries + 1):
            try:
                resp = requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)
                _raise_for_status_with_body(resp)
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except requests.exceptions.RequestException as e:
                if attempt < provider_retries and _is_retryable_error(e):
                    logger.warning(
                        f"LLM 请求失败，准备重试 ({attempt + 1}/{provider_retries})：provider={provider['name']} {type(e).__name__}: {_redact_sensitive(str(e))}"
                    )
                    time.sleep(RETRY_DELAY)
                    continue

                provider_errors.append(f"{provider['name']}: {type(e).__name__}: {_redact_sensitive(str(e))}")
                if provider_index < len(providers) - 1:
                    logger.warning(
                        f"LLM provider {provider['name']} failed; trying next provider: {type(e).__name__}: {_redact_sensitive(str(e))}"
                    )
                    break
                if len(providers) == 1:
                    raise
                raise RuntimeError("LLM 请求失败；providers exhausted: " + " | ".join(provider_errors)) from e

    raise RuntimeError("LLM 请求失败；providers exhausted: " + " | ".join(provider_errors))
