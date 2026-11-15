"""AI models the customer connects with their own key: Anthropic Claude through
the Anthropic SDK; OpenAI, Azure OpenAI, Gemini, Mistral, Ollama and any
OpenAI-compatible endpoint through the OpenAI SDK. Keys never leave the server."""
from __future__ import annotations

import time

import anthropic
import openai

PROVIDERS: dict[str, dict] = {
    "anthropic": {"label": "Anthropic Claude", "key": True,
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"]},
    "openai":    {"label": "OpenAI", "key": True, "models": []},
    "azure":     {"label": "Azure OpenAI", "key": True, "models": [], "base_url": "required",
                  "note": "Base URL = your Azure endpoint; Model = the deployment name."},
    "gemini":    {"label": "Google Gemini", "key": True, "models": [],
                  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "mistral":   {"label": "Mistral", "key": True, "models": [], "base_url": "https://api.mistral.ai/v1"},
    "ollama":    {"label": "Ollama (self-hosted)", "key": False, "models": [], "base_url": "http://ollama:11434/v1"},
    "compat":    {"label": "OpenAI-compatible endpoint", "key": True, "models": [], "base_url": "required"},
}
# Claude models that accept the server-side refusal fallback.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5"}
TIMEOUT = 600.0


class LLMError(Exception):
    pass


def _anthropic(cfg: dict, system: str, messages: list[dict], max_tokens: int) -> str:
    client = anthropic.Anthropic(api_key=cfg.get("api_key") or None, base_url=cfg.get("base_url") or None,
                                 timeout=TIMEOUT, max_retries=2)
    params = {"model": cfg["model"], "max_tokens": max_tokens, "system": system, "messages": messages}
    try:
        msg = None
        if cfg["model"] in _FALLBACK_MODELS:
            try:
                # If the model declines on a safety classifier, the API re-runs the
                # request on a fallback model inside the same call.
                with client.beta.messages.stream(**params, betas=["server-side-fallback-2026-07-01"],
                                                 extra_body={"fallbacks": "default"}) as stream:
                    msg = stream.get_final_message()
            except anthropic.BadRequestError:
                msg = None                    # e.g. a gateway without the beta: plain request below
        if msg is None:
            with client.messages.stream(**params) as stream:
                msg = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise LLMError("The API key was rejected.") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("This API key may not use that model.") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Model '{cfg['model']}' was not found.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("The provider is rate-limiting this key. Try again shortly.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {getattr(e, 'message', e)}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API.") from e
    if msg.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if msg.stop_reason == "max_tokens" and not text:
        raise LLMError("The model ran out of output space. Try a narrower request.")
    return text


def _openai(cfg: dict, system: str, messages: list[dict], max_tokens: int) -> str:
    provider = cfg["provider"]
    base = cfg.get("base_url") or PROVIDERS[provider].get("base_url")
    if base == "required":
        raise LLMError("Enter the base URL / endpoint for this provider.")
    try:
        if provider == "azure":
            client = openai.AzureOpenAI(api_key=cfg.get("api_key"), azure_endpoint=base,
                                        api_version=cfg.get("api_version") or "2024-10-21", timeout=TIMEOUT)
        else:
            client = openai.OpenAI(api_key=cfg.get("api_key") or "not-needed", base_url=base or None, timeout=TIMEOUT)
        limit = {"max_completion_tokens": max_tokens} if provider in ("openai", "azure") else {"max_tokens": max_tokens}
        r = client.chat.completions.create(model=cfg["model"],
                                           messages=[{"role": "system", "content": system}, *messages], **limit)
    except openai.AuthenticationError as e:
        raise LLMError("The API key was rejected.") from e
    except openai.NotFoundError as e:
        raise LLMError(f"Model '{cfg['model']}' was not found at this endpoint.") from e
    except openai.RateLimitError as e:
        raise LLMError("The provider is rate-limiting this key. Try again shortly.") from e
    except openai.APIStatusError as e:
        raise LLMError(f"API error {e.status_code}: {getattr(e, 'message', e)}") from e
    except openai.APIConnectionError as e:
        raise LLMError(f"Could not reach {base or 'the provider'}.") from e
    return (r.choices[0].message.content or "") if r.choices else ""


def chat(cfg: dict, system: str, messages: list[dict], max_tokens: int = 16000) -> str:
    if not cfg.get("model"):
        raise LLMError("No model name is set.")
    if PROVIDERS.get(cfg.get("provider"), {}).get("key") and not cfg.get("api_key"):
        raise LLMError("No API key is stored for this model.")
    if cfg["provider"] == "anthropic":
        return _anthropic(cfg, system, messages, max_tokens)
    if cfg["provider"] in PROVIDERS:
        return _openai(cfg, system, messages, max_tokens)
    raise LLMError(f"Unknown provider: {cfg['provider']}")


def test(cfg: dict) -> dict:
    started = time.perf_counter()
    reply = chat(cfg, "You are a connection test.", [{"role": "user", "content": "Reply with the single word OK."}],
                 max_tokens=1024)
    return {"ok": True, "ms": round((time.perf_counter() - started) * 1000), "reply": reply.strip()[:80]}
