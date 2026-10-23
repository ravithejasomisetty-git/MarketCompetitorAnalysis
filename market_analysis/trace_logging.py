"""Write a separate server-side JSONL execution trace for each submitted request.

JSONL is a text format with one complete JSON record per line. request_trace creates
the request ID and file; log_event lets nested services append events; resume_trace
connects a later export to the same file. ContextVar keeps the active request separate
between concurrent threads, and a process-local lock prevents interleaved writes.
Only selected event fields are logged; this is not a transcript of model reasoning."""
import json
import math
import os
import re
import threading
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

_active_trace = ContextVar("market_analysis_trace", default=None)
_write_lock = threading.Lock()

# Trace files describe execution, not the user's research content. These fields can
# contain private business information or text copied from untrusted pages/models.
_PRIVATE_FIELDS = {
    "brief", "assignment", "focus", "query", "product", "selected_product", "names",
    "products", "competitors", "candidates", "eligible_products", "limitations",
    "selection_basis", "opportunities", "url", "urls", "title", "excerpt", "content",
    "messages", "prompt", "response", "body", "price", "minimum", "maximum",
    "currency", "requested_currency", "founder_details", "exploration_context",
}
_TEXT_FIELDS = {"action", "tool", "agent", "kind", "mode", "outcome", "model",
                "id", "source_ids", "code", "error_type", "file", "function", "field", "type"}
_REASONS = {
    "screening_passed", "instruction_override", "secret_extraction", "tool_misuse",
    "screening_uncertain", "screening_unavailable", "prompt_injection_detected",
    "specialist_call_limit", "discovery_required", "current_research_required",
    "current_assessment_required", "sourced_price_in_requested_budget_and_currency",
    "unknown_price", "wrong_currency", "outside_budget", "unknown_source", "unsourced_price",
    "search_allowance_reached", "fetch_allowance_reached", "unknown_assignment",
    "url_not_discovered_for_product", "citation_schema_validation_failed",
    "existing_product_name_required", "invalid_input", "missing_provider_credentials",
}
_FIELD_NAMES = _PRIVATE_FIELDS | _TEXT_FIELDS | {
    "stage", "reason", "known_state", "calls", "discover", "research", "assess", "turn",
    "finished", "candidate_count", "eligible_count", "assessment_ready", "source_count",
    "duration_ms", "token_usage", "input_tokens", "output_tokens", "total_tokens",
    "model_timeout_seconds", "model_max_retries", "search_provider", "diagnostics", "errors",
    "frames", "line", "invalid_fields", "bytes", "redacted", "count",
}


def _known_secrets():
    """Read credential values for masking without returning them to the UI or console."""
    sensitive = re.compile(r"key|secret|password|token|authorization|cookie|credential|connection", re.I)
    values = [v for k, v in os.environ.items() if sensitive.search(k) and v]
    # Offline audit commands may not have loaded .env through uv yet. Read only
    # credential-shaped assignments; never execute shell text from configuration.
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and not key.lstrip().startswith("#") and sensitive.search(key):
                value = value.strip().strip("\"'")
                if value:
                    values.append(value)
    return sorted(set(values), key=len, reverse=True)


def log_directory():
    """Use a stable project-relative default, independent of the terminal's folder."""
    default = Path(__file__).resolve().parents[1] / "logs"
    return Path(os.environ.get("MARKET_LOG_DIR", str(default))).expanduser().resolve()


def _safe(value):
    """Keep operational metadata; omit private content before applying credential masking.

    Arbitrary text is denied by default. Model-written explanations are omitted;
    only the application's fixed reason codes remain. Lists of private records retain
    their count rather than their contents. This same policy can scrub older traces.
    """
    secrets = _known_secrets()

    def sanitize(item, field=""):
        if re.search(r"api.?key|authorization|password|secret|token|cookie|credential|connection", field, re.I) and field not in {"token_usage", "input_tokens", "output_tokens", "total_tokens"}:
            return "[REDACTED]"
        if field in _PRIVATE_FIELDS:
            if isinstance(item, dict) and item.get("redacted") is True and set(item) == {"redacted", "count"} and isinstance(item["count"], int):
                return item
            return {"redacted": True, "count": len(item)} if isinstance(item, (list, tuple)) else "[REDACTED]"
        if isinstance(item, dict):
            # Unknown field names can themselves contain user data. Do not copy them.
            return {str(k) if str(k) in _FIELD_NAMES else "[REDACTED_FIELD]":
                    sanitize(v, str(k)) if str(k) in _FIELD_NAMES else "[REDACTED]" for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [sanitize(v, field) for v in item]
        if isinstance(item, str):
            if field == "reason":
                return item if item in _REASONS else "[REDACTED: free text]"
            if field == "stage":
                item = re.sub(r"Product research:.*?(?= /|$)", "Product research: [product]", item)
            elif field not in _TEXT_FIELDS:
                return "[REDACTED]"
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
            item = re.sub(r"\b(?:sk-|tvly-)[A-Za-z0-9_-]+", "[REDACTED]", item)
            item = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;]+", "[REDACTED]", item)
            item = re.sub(r'''(?i)\b(?:api[-_]?key|password|passwd|secret|token|authorization)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''', "[REDACTED]", item)
            item = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED]", item)
            item = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[REDACTED]", item)
            item = re.sub(r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)", "[REDACTED]", item)
            return item[:4000]
        if isinstance(item, float) and not math.isfinite(item):
            return "[NONFINITE]"
        if item is None or isinstance(item, (int, float, bool)):
            return item
        return "[REDACTED]"

    return sanitize(value)


def safe_url(value):
    """Keep the page location useful without logging credentials, query parameters or fragments."""
    parsed = urlsplit(str(value))
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


class RequestTrace:
    """One request's trace file. Reopening by ID appends later export events to it."""
    def __init__(self, request_id=None):
        self.request_id = request_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.request_id):
            raise ValueError("Invalid trace ID.")
        self.path = log_directory() / f"{self.request_id}.jsonl"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.outcome = "completed"

    def event(self, event, **details):
        """Append one sanitized JSON event and close the file immediately afterward.
        
        Closing flushes Python's file buffer so completed events remain readable after an
        application failure. This is not an fsync guarantee against power loss. Write errors
        are allowed to propagate instead of silently continuing without a trace."""
        record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "request_id": self.request_id, "event": event, "details": _safe(details)}
        # A single write under a lock prevents threads from interleaving JSON lines.
        with _write_lock, os.fdopen(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    def reject(self, reason, **details):
        """Record validation/configuration failures even when no specialist was called."""
        self.outcome = "rejected"
        self.event("request_rejected", reason=reason, **details)

    def fail(self, exc):
        """Record only safe error locations/types, never raw provider exception messages."""
        from .research_errors import failure_details
        self.outcome = "failed"
        self.event("request_failed", diagnostics=failure_details(exc))


def log_event(event, **details):
    """Nested services can log without importing Streamlit or passing IDs everywhere."""
    trace = _active_trace.get()
    if trace is not None:
        trace.event(event, **details)


@contextmanager
def request_trace(brief, demo=False):
    """Create a request file, attach its context and always record a final outcome.
    
    The UI opens this context before validation so rejected submissions are recorded
    too. Unhandled exceptions are logged and re-raised. The finally block restores the
    previous context, preventing later work from attaching to the wrong request."""
    trace = RequestTrace()
    token = _active_trace.set(trace)
    started = perf_counter()
    try:
        trace.event("request_started", brief=brief, mode="offline_demo" if demo else "live")
        yield trace
    except Exception as exc:
        trace.fail(exc)
        raise
    finally:
        try:
            trace.event("request_finished", outcome=trace.outcome, duration_ms=round((perf_counter() - started) * 1000, 2))
        finally:
            _active_trace.reset(token)


@contextmanager
def resume_trace(request_id):
    """Reconnect a later PowerPoint preparation to its original analysis trace."""
    trace = RequestTrace(request_id)
    token = _active_trace.set(trace)
    try:
        yield trace
    finally:
        _active_trace.reset(token)


def sanitize_existing_logs():
    """Apply the current privacy policy to older project traces, without printing values.

    Replace each file atomically, retaining event order, timestamps and request IDs.
    If a running request changed a file during the audit, leave it untouched rather
    than losing events. No unsanitized backup is created. Stop active queries before
    running this maintenance operation.
    """
    root = log_directory()
    count = 0
    if not root.is_dir():
        return count
    for path in root.glob("*.jsonl"):
        if path.is_symlink() or path.resolve().parent != root:
            raise ValueError("Refusing to sanitize a linked log file.")
        original = path.stat()
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Unexpected trace record.")
                records.append({"timestamp": record.get("timestamp", ""),
                    "request_id": record.get("request_id", path.stem), "event": record.get("event", "legacy_event"),
                    "details": _safe(record.get("details", {}))})
            except (ValueError, TypeError):
                records.append({"event": "legacy_line_redacted", "details": {}})
        descriptor, name = tempfile.mkstemp(prefix=".sanitized-", dir=root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            current = path.stat()
            if (current.st_mtime_ns, current.st_size) != (original.st_mtime_ns, original.st_size):
                raise RuntimeError("A trace changed during sanitization; retry when queries are idle.")
            os.replace(temporary, path)
            count += 1
        finally:
            temporary.unlink(missing_ok=True)
    return count
