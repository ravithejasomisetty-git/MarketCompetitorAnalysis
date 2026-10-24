"""Retry unavailable OpenAI model calls, then use an explicitly configured Nebius model.

Only model inference is repeated. This adapter never executes tools, restarts an
agent, or repeats a Tavily search. Invalid data, refusals, credentials and billing
errors stop normally rather than being sent to another provider.
"""
import os
from dataclasses import dataclass, replace
from time import sleep
from urllib.parse import urlsplit
from openai import APIConnectionError, APITimeoutError, APIStatusError
from langchain_core.messages import SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from .research_errors import ResearchFailure
from .trace_logging import log_event, model_callbacks


def availability_reason(error):
    """Classify typed SDK errors using codes, never raw provider error messages."""
    if isinstance(error, APITimeoutError):
        return "model_timeout"
    if isinstance(error, APIConnectionError):
        return "model_connection_failed"
    if not isinstance(error, APIStatusError):
        return None
    code = str(getattr(error, "code", "") or "").lower()
    error_type = str(getattr(error, "type", "") or "").lower()
    if any(word in code or word in error_type for word in ("quota", "billing", "credit", "budget", "content_filter", "safety", "policy", "refusal")):
        return None
    if error.status_code == 429:
        return "model_rate_limited"
    if error.status_code == 408:
        return "model_timeout"
    if error.status_code in {500, 502, 503, 504, 529}:
        return "model_service_unavailable"
    return None


def _nebius_url(value):
    """Allow only Nebius HTTPS endpoints; never send its key to an arbitrary URL."""
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in {
            "api.tokenfactory.nebius.com", "api.tokenfactory.us-central1.nebius.com"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in {None, 443} or parsed.path.rstrip("/") != "/v1"):
        raise ResearchFailure("invalid_nebius_configuration",
            "Set NEBIUS_BASE_URL to your Nebius HTTPS API endpoint ending in /v1.", "Provider configuration")
    return value.rstrip("/")


@dataclass(frozen=True)
class ModelRouter:
    """Expose the three operations used by our agents: bind, structure and invoke.

    Derived routers retain both provider bindings. Every invocation starts with
    OpenAI; temporary provider failures do not change other requests' preferences.
    """
    primary: object
    primary_name: str
    fallback: object = None
    fallback_name: str = ""
    tool_names: tuple = ()
    primary_retries: int = 1

    def bind_tools(self, tools, **kwargs):
        tools = list(tools)
        names = tuple(convert_to_openai_tool(item)["function"]["name"] for item in tools)
        # Nebius model support for parallel_tool_calls varies. The host already
        # executes tool requests sequentially and enforces per-tool allowances.
        fallback_options = {k: v for k, v in kwargs.items() if k != "parallel_tool_calls"}
        return replace(self, primary=self.primary.bind_tools(tools, **kwargs),
            fallback=self.fallback.bind_tools(tools, **fallback_options) if self.fallback else None,
            tool_names=names)

    def with_structured_output(self, schema, **kwargs):
        options = {"method": "json_schema", "strict": False} | kwargs
        return replace(self, primary=self.primary.with_structured_output(schema, **kwargs),
            fallback=self.fallback.with_structured_output(schema, **options) if self.fallback else None,
            tool_names=())

    def invoke(self, messages, config=None, **kwargs):
        """Preserve the conversation, retry at most once, then attempt Nebius once."""
        last_error = None
        reason = None
        for attempt in range(1, self.primary_retries + 2):
            log_event("model_attempt_started", provider="openai", model=self.primary_name, attempt=attempt)
            try:
                result = self.primary.invoke(messages, config=config, **kwargs)
            except Exception as exc:
                reason = availability_reason(exc)
                log_event("model_attempt_failed", provider="openai", model=self.primary_name,
                    attempt=attempt, reason=reason or "model_error_not_retryable")
                if reason is None:
                    raise
                last_error = exc
                if attempt <= self.primary_retries:
                    sleep(0.5)
            else:
                log_event("model_attempt_completed", provider="openai", model=self.primary_name, attempt=attempt)
                return result
        if self.fallback is None:
            raise last_error
        log_event("model_fallback", provider="nebius", model=self.fallback_name, reason=reason)
        fallback_messages = messages
        fallback_options = dict(kwargs)
        require_tool = kwargs.get("tool_choice") == "required" and bool(self.tool_names)
        if require_tool:
            if len(self.tool_names) == 1:
                fallback_options["tool_choice"] = {"type": "function", "function": {"name": self.tool_names[0]}}
            else:
                # Some Nebius endpoints support auto or a named tool but not required.
                # Enforce the required action in the host after the response as well.
                fallback_options["tool_choice"] = "auto"
                fallback_messages = [*messages, SystemMessage(content=
                    "For this turn, select a permitted tool and provide its arguments. A text-only answer cannot complete this turn.")]
        log_event("model_attempt_started", provider="nebius", model=self.fallback_name, attempt=1)
        try:
            result = self.fallback.invoke(fallback_messages, config=config, **fallback_options)
            if require_tool and not getattr(result, "tool_calls", None):
                raise ResearchFailure("model_tool_selection_missing",
                    "The fallback model did not select a required research action. Please retry.")
        except Exception as exc:
            log_event("model_attempt_failed", provider="nebius", model=self.fallback_name, attempt=1,
                reason=availability_reason(exc) or "model_error_not_retryable")
            raise ResearchFailure("fallback_model_failed",
                "OpenAI was temporarily unavailable and the Nebius fallback could not complete the call. "
                "Check NEBIUS_MODEL, endpoint access and model compatibility, then retry.") from exc
        log_event("model_attempt_completed", provider="nebius", model=self.fallback_name, attempt=1)
        return result


def create_model_router(openai_key, model_factory):
    """Build provider clients from the environment without hardcoded model names."""
    primary_name = os.environ.get("OPENAI_MODEL", "").strip()
    if not primary_name:
        raise ResearchFailure("missing_model", "Set OPENAI_MODEL in .env and restart the app.", "Provider configuration")
    enabled = os.environ.get("MODEL_ROUTING_ENABLED", "false").strip().lower()
    if enabled not in {"true", "false"}:
        raise ResearchFailure("invalid_router_configuration", "Set MODEL_ROUTING_ENABLED to true or false.", "Provider configuration")
    fallback_config = None
    if enabled == "true":
        key = os.environ.get("NEBIUS_API_KEY", "").strip()
        name = os.environ.get("NEBIUS_MODEL", "").strip()
        url = os.environ.get("NEBIUS_BASE_URL", "").strip()
        if not all((key, name, url)) or name == "MODEL_ID":
            raise ResearchFailure("missing_nebius_configuration",
                "Set NEBIUS_API_KEY, NEBIUS_BASE_URL and the exact NEBIUS_MODEL API ID, or disable MODEL_ROUTING_ENABLED.",
                "Provider configuration")
        fallback_config = dict(model=name, api_key=key, base_url=_nebius_url(url))
    # SDK retries are disabled so the router owns a predictable two-primary/one-backup budget.
    primary = model_factory(model=primary_name, api_key=openai_key, temperature=0,
        timeout=60, max_retries=0, callbacks=model_callbacks(primary_name, provider="openai"))
    fallback = None
    if fallback_config:
        fallback = model_factory(**fallback_config, temperature=0, timeout=60, max_retries=0,
            use_responses_api=False, callbacks=model_callbacks(fallback_config["model"], provider="nebius"))
    log_event("model_routing_configured", provider="openai", model=primary_name, fallback_enabled=fallback is not None)
    return ModelRouter(primary=primary, primary_name=primary_name, fallback=fallback,
        fallback_name=fallback_config["model"] if fallback_config else "")
