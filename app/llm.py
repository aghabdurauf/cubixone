"""AI models the customer connects with their own key. Any model can be added:

* kind "anthropic"  Anthropic Claude, or any Anthropic-compatible endpoint (custom base URL),
                    through the Anthropic SDK.
* kind "openai"     OpenAI and every OpenAI-compatible API (Grok, GLM, DeepSeek, Qwen, Kimi,
                    Groq, OpenRouter, Ollama, LM Studio, vLLM ...), through the OpenAI SDK.
* kind "azure"      Azure OpenAI deployments.

The entries below are presets: they only pre-fill the base URL. The base URL,
the model name and the key are always editable, so a provider that is not
listed works through "Custom". Keys never leave the server."""
from __future__ import annotations

import time

import anthropic
import openai

# key: True = an API key is required; False = optional (self-hosted, keyless gateways).
PROVIDERS: dict[str, dict] = {
    "anthropic":  {"label": "Anthropic Claude", "kind": "anthropic", "key": True, "base_url": "",
                   "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"],
                   "note": "Leave the base URL empty for Anthropic itself, or enter an Anthropic-compatible endpoint."},
    "openai":     {"label": "OpenAI", "kind": "openai", "key": True, "base_url": "https://api.openai.com/v1"},
    "azure":      {"label": "Azure OpenAI", "kind": "azure", "key": True, "base_url": "required",
                   "note": "Base URL = your Azure endpoint; Model = the deployment name."},
    "gemini":     {"label": "Google Gemini", "kind": "openai", "key": True,
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "xai":        {"label": "xAI Grok", "kind": "openai", "key": True, "base_url": "https://api.x.ai/v1"},
    "zhipu":      {"label": "Zhipu GLM (BigModel, China)", "kind": "openai", "key": True,
                   "base_url": "https://open.bigmodel.cn/api/paas/v4/"},
    "zai":        {"label": "Z.ai GLM (international)", "kind": "openai", "key": True,
                   "base_url": "https://api.z.ai/api/paas/v4/"},
    "deepseek":   {"label": "DeepSeek", "kind": "openai", "key": True, "base_url": "https://api.deepseek.com"},
    "qwen":       {"label": "Alibaba Qwen (DashScope)", "kind": "openai", "key": True,
                   "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
    "moonshot":   {"label": "Moonshot Kimi", "kind": "openai", "key": True, "base_url": "https://api.moonshot.ai/v1"},
    "mistral":    {"label": "Mistral", "kind": "openai", "key": True, "base_url": "https://api.mistral.ai/v1"},
    "cohere":     {"label": "Cohere", "kind": "openai", "key": True, "base_url": "https://api.cohere.ai/compatibility/v1"},
    "perplexity": {"label": "Perplexity", "kind": "openai", "key": True, "base_url": "https://api.perplexity.ai"},
    "groq":       {"label": "Groq", "kind": "openai", "key": True, "base_url": "https://api.groq.com/openai/v1"},
    "together":   {"label": "Together AI", "kind": "openai", "key": True, "base_url": "https://api.together.xyz/v1"},
    "fireworks":  {"label": "Fireworks AI", "kind": "openai", "key": True,
                   "base_url": "https://api.fireworks.ai/inference/v1"},
    "openrouter": {"label": "OpenRouter (hundreds of models)", "kind": "openai", "key": True,
                   "base_url": "https://openrouter.ai/api/v1"},
    "ollama":     {"label": "Ollama (self-hosted)", "kind": "openai", "key": False, "base_url": "http://ollama:11434/v1"},
    "lmstudio":   {"label": "LM Studio / vLLM (self-hosted)", "kind": "openai", "key": False,
                   "base_url": "http://host.docker.internal:1234/v1"},
    "compat":     {"label": "Custom: any OpenAI-compatible API", "kind": "openai", "key": False, "base_url": "required",
                   "note": "For any provider not listed: enter its base URL (usually ending in /v1), model name and key."},
}
for _p in PROVIDERS.values():
    _p.setdefault("models", [])
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


def _detail(e: Exception) -> str:
    """The provider's own error message, whatever shape its error body has."""
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        msg = body.get("error", body)
        if isinstance(msg, dict):
            msg = msg.get("message") or msg.get("msg") or msg.get("detail")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:300]
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return str(getattr(e, "message", e))[:300]


def _openai(cfg: dict, system: str, messages: list[dict], max_tokens: int) -> str:
    provider = cfg["provider"]
    preset = PROVIDERS.get(provider, PROVIDERS["compat"])
    base = (cfg.get("base_url") or "").strip() or preset.get("base_url")
    if not base or base == "required":
        raise LLMError("Enter the base URL / endpoint for this provider.")
    try:
        if preset["kind"] == "azure":
            client = openai.AzureOpenAI(api_key=cfg.get("api_key"), azure_endpoint=base,
                                        api_version=cfg.get("api_version") or "2024-10-21", timeout=TIMEOUT)
        else:
            client = openai.OpenAI(api_key=cfg.get("api_key") or "not-needed", base_url=base, timeout=TIMEOUT)
        msgs = [{"role": "system", "content": system}, *messages]
        name = "max_completion_tokens" if provider in ("openai", "azure") else "max_tokens"
        # Providers cap output differently; step down if one rejects the size.
        sizes = [max_tokens] + [s for s in (8192, 4096) if s < max_tokens]
        for i, limit in enumerate(sizes):
            try:
                r = client.chat.completions.create(model=cfg["model"], messages=msgs, **{name: limit})
                break
            except openai.BadRequestError as e:
                if "token" not in str(e).lower() or i == len(sizes) - 1:
                    raise
    except openai.AuthenticationError as e:
        raise LLMError("The API key was rejected.") from e
    except openai.NotFoundError as e:
        raise LLMError(f"Model '{cfg['model']}' was not found at this endpoint.") from e
    except openai.RateLimitError as e:
        raise LLMError("The provider is rate-limiting this key. Try again shortly.") from e
    except openai.APIStatusError as e:
        detail = _detail(e)
        if e.status_code in (400, 403) and "key" in detail.lower():
            raise LLMError(f"The API key was rejected: {detail}") from e
        raise LLMError(f"API error {e.status_code}: {detail}") from e
    except openai.APIConnectionError as e:
        raise LLMError(f"Could not reach {base or 'the provider'}.") from e
    return (r.choices[0].message.content or "") if r.choices else ""


def chat(cfg: dict, system: str, messages: list[dict], max_tokens: int = 16000) -> str:
    if not cfg.get("model"):
        raise LLMError("No model name is set.")
    preset = PROVIDERS.get(cfg.get("provider"), PROVIDERS["compat"])
    if preset["key"] and not cfg.get("api_key"):
        raise LLMError("No API key is stored for this model.")
    if preset["kind"] == "anthropic":
        return _anthropic(cfg, system, messages, max_tokens)
    return _openai(cfg, system, messages, max_tokens)


def test(cfg: dict) -> dict:
    started = time.perf_counter()
    reply = chat(cfg, "You are a connection test.", [{"role": "user", "content": "Reply with the single word OK."}],
                 max_tokens=1024)
    return {"ok": True, "ms": round((time.perf_counter() - started) * 1000), "reply": reply.strip()[:80]}
