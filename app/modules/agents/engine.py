# ==============================================================================
# FILE: app/modules/agents/engine.py
# OVERVIEW: Agents logic (no Qt): talk to a model provider - Claude (Anthropic API), Gemini (Google AI API) or
#           Ollama (local) - with streamed answers, list the models a provider offers, and keep API keys in
#           the system keyring.
# PURPOSE:
#   1. stream_chat(config, messages, on_text, should_stop): one assistant turn, text arrives piece by piece;
#      errors become AgentError with a message a user can act on (missing key, Ollama not running ...).
#   2. list_models(provider, base_url, key): what the provider offers (Ollama: the models pulled locally).
#   3. API keys: keyring first, then the provider's usual environment variable.
# CONTRACT:
#   - Requires: standard library urllib / json, app.data.store.secrets. No Qt, no extra packages.
#   - Exposes: PROVIDERS, KNOWN_MODELS, DEFAULT_MODELS, DEFAULT_ENDPOINTS, KEY_ENVIRONMENT, AgentError,
#              ChatMessage, ChatConfig, ChatResult, provider_of(values), model_of(values, provider),
#              chat_config(title, values, base_urls), stream_chat(), list_models(),
#              api_key(provider) -> (key, source), set_api_key(provider, key), forget_api_key(provider)
# NOTES:
#   - [2026-11-17] Created: agents can now be chatted with (they were only defined before). Tools and
#                  permissions are not executed in chat yet; the instructions are the system prompt.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# - provider (str): Who runs the model: "Claude", "Gemini" or "Ollama".
# - turn: One user message and the assistant's streamed answer.
# - stop reason (str): Why the answer ended, as the provider says it ("end_turn", "max_tokens", "STOP" ...).
# - KEYRING_SERVICE (str): Keyring service the API keys are stored under ("devos-agents").
# - MAX_TOKENS_DEFAULT (int): Output limit when the agent has none (the Anthropic API requires one).
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. PROVIDERS
#    1.1 imports_and_constants
#    1.2 types
#    1.3 api_keys
# 2. HTTP
#    2.1 requests
#    2.2 stream_chat
#    2.3 list_models
# ==============================================================================

# ==============================================================================
# [1.1] imports_and_constants
# ==============================================================================
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterator

from app.data.store.secrets import forget_secret, get_secret, set_secret

PROVIDERS = ("Claude", "Gemini", "Ollama")
KNOWN_MODELS = {
    "Claude": ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2"),
    "Gemini": ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"),
    "Ollama": ("llama3.2", "qwen3", "mistral", "gemma3"),
}
DEFAULT_MODELS = {"Claude": "claude-opus-5-5", "Gemini": "gemini-2.5-pro", "Ollama": "llama3.2"}
DEFAULT_ENDPOINTS = {"Claude": "https://api.anthropic.com", "Gemini": "https://generativelanguage.googleapis.com",
                     "Ollama": "http://localhost:11434"}
KEY_ENVIRONMENT = {"Claude": ("ANTHROPIC_API_KEY",), "Gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
KEYRING_SERVICE = "devos-agents"
MAX_TOKENS_DEFAULT = 8192
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT_S = 120
# --- END [1.1] ---


# ==============================================================================
# [1.2] types
# ==============================================================================
class AgentError(Exception):
    """A provider call failed; the message says what to do about it."""


@dataclass(frozen=True)
class ChatMessage:
    role: str                                   # "user" or "assistant"
    content: str


@dataclass(frozen=True)
class ChatConfig:
    provider: str
    model: str
    system: str = ""
    max_tokens: int | None = None               # None: provider default (Claude: MAX_TOKENS_DEFAULT)
    base_url: str = ""                          # "" = DEFAULT_ENDPOINTS[provider]
    api_key: str = ""


@dataclass
class ChatResult:
    text: str = ""
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    was_stopped: bool = False


def provider_of(values: dict) -> str:
    """An agent's provider: its Provider property, else guessed from the model name (older agents)."""
    provider = values.get("provider")
    if provider in PROVIDERS:
        return provider
    model = str(values.get("model") or "")
    if model.startswith("gemini"):
        return "Gemini"
    if model and not model.startswith("claude") and model != "inherit":
        return "Ollama"
    return "Claude"


def model_of(values: dict, provider: str) -> str:
    """The agent's model for its provider; the provider's default when it has none (or another provider's)."""
    model = str(values.get("model") or "").strip()
    foreign = (model.startswith("claude") and provider != "Claude") or (model.startswith("gemini") and provider != "Gemini")
    return DEFAULT_MODELS[provider] if not model or model == "inherit" or foreign else model


def chat_config(title: str, values: dict, base_urls: dict[str, str] | None = None) -> ChatConfig:
    """An agent record (title + values) as a chat: provider, model, instructions as the system prompt."""
    provider = provider_of(values)
    role = str(values.get("role") or "").strip()
    system = str(values.get("instructions") or "").strip() or f"You are {title}." + (f" Your role: {role}." if role else "")
    max_tokens = int(values["max_tokens"]) if values.get("max_tokens") else None
    return ChatConfig(provider, model_of(values, provider), system, max_tokens, (base_urls or {}).get(provider, ""),
                      api_key(provider)[0])
# --- END [1.2] ---


# ==============================================================================
# [1.3] api_keys
# ==============================================================================
def api_key(provider: str) -> tuple[str, str]:
    """(key, where it came from); ("", "") when there is none. Ollama needs no key."""
    key = get_secret(KEYRING_SERVICE, provider)
    if key:
        return key, "keyring"
    for name in KEY_ENVIRONMENT.get(provider, ()):
        if os.environ.get(name):
            return os.environ[name], f"environment ({name})"
    return "", ""


def set_api_key(provider: str, key: str) -> None:
    set_secret(KEYRING_SERVICE, provider, key.strip())


def forget_api_key(provider: str) -> None:
    forget_secret(KEYRING_SERVICE, provider)
# --- END [1.3] ---


# ==============================================================================
# [2.1] requests
# ==============================================================================
def _base(config_url: str, provider: str) -> str:
    return (config_url or DEFAULT_ENDPOINTS[provider]).rstrip("/")


def _headers(provider: str, key: str) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if provider == "Claude":
        headers |= {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    elif provider == "Gemini":
        headers["x-goog-api-key"] = key
    return headers


def _error_text(provider: str, error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8", "replace") or "{}")
        detail = body.get("error")
        detail = detail.get("message", "") if isinstance(detail, dict) else str(detail or "")
    except (ValueError, OSError):
        detail = ""
    text = f"{provider}: {detail or error.reason} (HTTP {error.code})"
    if error.code in (401, 403) and provider != "Ollama":
        text += " — check the API key in the Agents settings (⚙)"
    return text


def _open(provider: str, url: str, key: str, body: dict | None = None):
    if provider != "Ollama" and not key:
        names = " or ".join(KEY_ENVIRONMENT[provider])
        raise AgentError(f"No {provider} API key: add one in the Agents settings (⚙) or set {names}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=_headers(provider, key), method="POST" if data else "GET")
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT_S)
    except urllib.error.HTTPError as error:
        raise AgentError(_error_text(provider, error)) from None
    except (urllib.error.URLError, OSError) as error:
        host = urllib.parse.urlsplit(url).netloc
        hint = " — is Ollama running? Start it with `ollama serve`" if provider == "Ollama" else ""
        raise AgentError(f"Can't reach {provider} at {host}: {getattr(error, 'reason', error)}{hint}") from None


def _json_lines(response, prefix: str = "") -> Iterator[dict]:
    """Objects of an NDJSON stream (prefix "") or the data: lines of a server-sent event stream."""
    for raw in response:
        line = raw.decode("utf-8", "replace").strip()
        if prefix:
            if not line.startswith(prefix):
                continue
            line = line[len(prefix):].strip()
        if line and line != "[DONE]":
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _alternating(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Merge neighbours with the same role (a stopped, empty answer leaves two user messages in a row)."""
    merged: list[ChatMessage] = []
    for message in messages:
        if not message.content:
            continue
        if merged and merged[-1].role == message.role:
            merged[-1] = ChatMessage(message.role, merged[-1].content + "\n\n" + message.content)
        else:
            merged.append(message)
    return merged
# --- END [2.1] ---


# ==============================================================================
# [2.2] stream_chat
# ==============================================================================
def stream_chat(config: ChatConfig, messages: list[ChatMessage], on_text: Callable[[str], None],
                should_stop: Callable[[], bool] = lambda: False) -> ChatResult:
    """Run one turn; on_text(piece) for every piece of the answer as it arrives. Blocking: call it from a
    worker thread. should_stop() is checked between pieces (the answer so far is kept)."""
    provider, history = config.provider, _alternating(messages)
    if provider not in PROVIDERS:
        raise AgentError(f"Unknown provider “{provider}”")
    if not history or history[-1].role != "user":
        raise AgentError("Nothing to answer: the last message must be yours")
    base, result = _base(config.base_url, provider), ChatResult()
    if provider == "Claude":
        body = {"model": config.model, "max_tokens": config.max_tokens or MAX_TOKENS_DEFAULT, "stream": True,
                "messages": [{"role": m.role, "content": m.content} for m in history]}
        if config.system:
            body["system"] = config.system
        response, prefix = _open(provider, f"{base}/v1/messages", config.api_key, body), "data:"
    elif provider == "Gemini":
        body = {"contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                             for m in history]}
        if config.system:
            body["systemInstruction"] = {"parts": [{"text": config.system}]}
        if config.max_tokens:
            body["generationConfig"] = {"maxOutputTokens": config.max_tokens}
        model = urllib.parse.quote(config.model, safe="")
        response = _open(provider, f"{base}/v1beta/models/{model}:streamGenerateContent?alt=sse", config.api_key, body)
        prefix = "data:"
    else:
        chat = [{"role": "system", "content": config.system}] if config.system else []
        body = {"model": config.model, "stream": True, "messages": chat + [{"role": m.role, "content": m.content} for m in history]}
        if config.max_tokens:
            body["options"] = {"num_predict": config.max_tokens}
        response, prefix = _open(provider, f"{base}/api/chat", "", body), ""

    def emit(piece: str) -> None:
        if piece:
            result.text += piece
            on_text(piece)

    with response:
        for event in _json_lines(response, prefix):
            if should_stop():
                result.was_stopped = True
                break
            if provider == "Claude":
                kind = event.get("type")
                if kind == "error":
                    raise AgentError(f"Claude: {event.get('error', {}).get('message', 'error')}")
                if kind == "message_start":
                    result.input_tokens = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                elif kind == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                    emit(event["delta"].get("text", ""))
                elif kind == "message_delta":
                    result.stop_reason = event.get("delta", {}).get("stop_reason") or result.stop_reason
                    result.output_tokens = event.get("usage", {}).get("output_tokens", result.output_tokens)
            elif provider == "Gemini":
                if "error" in event:
                    raise AgentError(f"Gemini: {event['error'].get('message', 'error')}")
                blocked = event.get("promptFeedback", {}).get("blockReason")
                if blocked:
                    raise AgentError(f"Gemini blocked the prompt ({blocked})")
                for candidate in event.get("candidates", [])[:1]:
                    for part in candidate.get("content", {}).get("parts", []):
                        if not part.get("thought"):
                            emit(part.get("text", ""))
                    result.stop_reason = candidate.get("finishReason") or result.stop_reason
                usage = event.get("usageMetadata", {})
                result.input_tokens = usage.get("promptTokenCount", result.input_tokens)
                result.output_tokens = usage.get("candidatesTokenCount", result.output_tokens)
            else:
                if "error" in event:
                    raise AgentError(f"Ollama: {event['error']}")
                emit(event.get("message", {}).get("content", ""))
                if event.get("done"):
                    result.stop_reason = event.get("done_reason", "stop")
                    result.input_tokens = event.get("prompt_eval_count", 0)
                    result.output_tokens = event.get("eval_count", 0)
    return result
# --- END [2.2] ---


# ==============================================================================
# [2.3] list_models
# ==============================================================================
def list_models(provider: str, base_url: str = "", key: str = "") -> list[str]:
    """Model ids the provider offers for chat (Ollama: the models pulled on this machine)."""
    base = _base(base_url, provider)
    if provider == "Claude":
        with _open(provider, f"{base}/v1/models?limit=100", key) as response:
            return [item["id"] for item in json.load(response).get("data", [])]
    if provider == "Gemini":
        with _open(provider, f"{base}/v1beta/models?pageSize=200", key) as response:
            return [item["name"].removeprefix("models/") for item in json.load(response).get("models", [])
                    if "generateContent" in item.get("supportedGenerationMethods", [])]
    with _open(provider, f"{base}/api/tags", "") as response:
        return [item["name"] for item in json.load(response).get("models", [])]
# --- END [2.3] ---
