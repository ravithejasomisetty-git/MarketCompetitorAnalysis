"""Send metadata-only workflow observations to Langfuse Cloud.

This adapter never receives prompts or page contents. It uses explicit parent IDs
instead of automatic instrumentation, which could capture private model inputs.
Tracing is best effort: a cloud failure must not stop market research.
"""
import logging
import os
import re
import threading
from logging.handlers import RotatingFileHandler
from langchain_core.callbacks import BaseCallbackHandler

_client = None
_client_lock = threading.Lock()
_operations_lock = threading.Lock()
_handlers = {}
_cloud_hosts = {"https://cloud.langfuse.com", "https://us.cloud.langfuse.com",
                "https://jp.cloud.langfuse.com", "https://hipaa.cloud.langfuse.com"}


def operational_warning(code, request_id=None):
    """Write fixed codes only; neither exception messages nor credentials are accepted."""
    from .trace_logging import log_directory
    codes = {"langfuse_configuration_missing", "langfuse_configuration_invalid",
             "langfuse_initialization_failed", "langfuse_delivery_failed",
             "offline_trace_write_failed"}
    if code not in codes:
        code = "langfuse_delivery_failed"
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        request_id = None
    try:
        path = log_directory() / "operations.log"
        with _operations_lock:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handler = _handlers.get(path)
            if handler is None:
                handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s WARNING %(message)s"))
                _handlers[path] = handler
            # IDs are generated or validated by RequestTrace, never taken from a prompt.
            message = code + (f" request_id={request_id}" if request_id else "")
            handler.emit(logging.LogRecord("market.operations", logging.WARNING, "", 0, message, (), None))
            handler.close()
    except Exception:
        # If the disk itself is unavailable, emit only the same safe fixed code.
        logging.getLogger("market.operations.fallback").warning(code)


class _PrivateSdkLogs(logging.Handler):
    """Replace SDK/exporter diagnostics, which may contain raw HTTP error bodies."""
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            operational_warning("langfuse_delivery_failed")


def get_cloud_client():
    """Create one client per server process; configuration changes require restart."""
    global _client
    if os.environ.get("LANGFUSE_TRACING_ENABLED", "true").lower() in {"false", "0", "no"}:
        return None
    with _client_lock:
        if _client is not None:
            return _client
        public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        host = os.environ.get("LANGFUSE_BASE_URL", "").strip().rstrip("/")
        if not public or not secret or not host:
            operational_warning("langfuse_configuration_missing")
            return None
        if host not in _cloud_hosts or "your-" in public or "your-" in secret:
            operational_warning("langfuse_configuration_invalid")
            return None
        try:
            from langfuse import Langfuse
            from opentelemetry.sdk.trace import TracerProvider
            from .trace_logging import _safe
            for name in ("langfuse", "opentelemetry.exporter.otlp.proto.http.trace_exporter",
                         "opentelemetry.exporter.http", "opentelemetry.sdk._shared_internal"):
                logger = logging.getLogger(name)
                logger.handlers = [_PrivateSdkLogs()]
                logger.propagate = False
            _client = Langfuse(public_key=public, secret_key=secret, base_url=host,
                timeout=3, debug=False, flush_interval=5,
                tracer_provider=TracerProvider(), mask=lambda *, data, **_: _safe(data))
            return _client
        except Exception:
            operational_warning("langfuse_initialization_failed")
            return None


class CloudTrace:
    """One request tree, with nested steps and events attached to the active step."""
    def __init__(self, request_id, resumed=False):
        self.request_id = request_id
        self.client = get_cloud_client()
        self.root = None
        self.steps = []
        self.failed = False
        self.models = {}
        self.lock = threading.RLock()
        if self.client:
            self.root = self._attempt(lambda: self.client.start_observation(
                trace_context={"trace_id": request_id},
                name="PowerPoint export" if resumed else "Market analysis",
                as_type="span", metadata={"id": request_id, "mode": "live"}))

    def _attempt(self, operation):
        if self.failed:
            return None
        try:
            return operation()
        except Exception:
            self.failed = True
            operational_warning("langfuse_delivery_failed", self.request_id)
            return None

    def _parent(self):
        return self.steps[-1][1] if self.steps else self.root

    def _context(self):
        parent = self._parent()
        return {"trace_id": self.request_id, "parent_span_id": parent.id}

    def event(self, name, details):
        """Details must already have passed the shared privacy filter."""
        if not self.root or self.failed:
            return
        with self.lock:
            stage = details.get("stage", "Research step")
            if name == "step_started":
                kind = "guardrail" if stage == "Request safety screening" else "span"
                span = self._attempt(lambda: self.client.start_observation(
                    trace_context=self._context(), name=stage, as_type=kind))
                if span:
                    self.steps.append((stage, span))
            elif name in {"step_completed", "step_failed"}:
                if self.steps and self.steps[-1][0] == stage:
                    _, span = self.steps.pop()
                    self._attempt(lambda: span.update(metadata=details,
                        level="ERROR" if name == "step_failed" else "DEFAULT"))
                    self._attempt(span.end)
            else:
                self._attempt(lambda: self._parent().create_event(name=name, metadata=details,
                    level="ERROR" if name == "request_failed" else
                          "WARNING" if name in {"request_rejected", "action_blocked"} else "DEFAULT"))

    def model_start(self, run_id, model, provider=None):
        if not self.root or self.failed:
            return
        with self.lock:
            span = self._attempt(lambda: self.client.start_observation(
                trace_context=self._context(), name="Model call", as_type="generation", model=model,
                metadata={"provider": provider} if provider else {}))
            if span:
                self.models[run_id] = span

    def model_end(self, run_id, usage=None, error=False):
        with self.lock:
            span = self.models.pop(run_id, None)
            if span:
                self._attempt(lambda: span.update(usage_details=usage or {},
                    level="ERROR" if error else "DEFAULT"))
                self._attempt(span.end)

    def close(self, outcome):
        if self.root:
            # End manually: passing exceptions through an SDK context manager could
            # record their private messages and traceback text automatically.
            for _, span in reversed(self.steps):
                self._attempt(span.end)
            for span in self.models.values():
                self._attempt(span.end)
            self._attempt(lambda: self.root.update(metadata={"outcome": outcome},
                level="ERROR" if outcome == "failed" else "WARNING" if outcome == "rejected" else "DEFAULT"))
            self._attempt(self.root.end)


class MetadataModelCallback(BaseCallbackHandler):
    """Observe token counts without recording LangChain messages or model output."""
    run_inline = True
    raise_error = False

    def __init__(self, trace, model, provider=None):
        self.trace = trace
        self.model = model
        self.provider = provider

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if self.trace:
            self.trace.model_start(run_id, self.model, provider=self.provider)

    def on_llm_end(self, response, *, run_id, **kwargs):
        usage = {}
        # ChatOpenAI supplies per-generation usage; never inspect message content.
        for group in response.generations:
            for generation in group:
                raw = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
                for source, target in (("input_tokens", "input"), ("output_tokens", "output"), ("total_tokens", "total")):
                    value = raw.get(source)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[target] = usage.get(target, 0) + value
        if not usage:
            raw = (response.llm_output or {}).get("token_usage", {})
            for source, target in (("prompt_tokens", "input"), ("completion_tokens", "output"), ("total_tokens", "total")):
                value = raw.get(source)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    usage[target] = value
        if self.trace:
            self.trace.model_end(run_id, usage)

    def on_llm_error(self, error, *, run_id, **kwargs):
        if self.trace:
            self.trace.model_end(run_id, error=True)
