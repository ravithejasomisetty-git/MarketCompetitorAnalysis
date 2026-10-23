"""Label failures consistently and produce safe troubleshooting information.

run_step measures an operation and logs its success or failure. ResearchFailure
carries the short application-authored message suitable for the user. failure_details
follows linked exception causes and extracts types, file locations and invalid field
names; it deliberately leaves out raw provider messages and submitted field values."""
import traceback
from time import perf_counter
from pathlib import Path
from pydantic import ValidationError
from .trace_logging import log_event


# An application error carries a safe display message plus a code and failing stage.
class ResearchFailure(ValueError):
    def __init__(self, code, message, stage=""):
        super().__init__(message)
        self.code = code
        self.public_message = message
        self.stage = stage


def run_step(stage, operation):
    """Measure an operation, log its outcome, and attach its stage to any failure.

    operation is a callable, often a small lambda, executed exactly once here. An
    existing ResearchFailure is preserved; other exceptions are wrapped with a safe
    display message and retain their original cause for file-based diagnostics.
    Token totals are recorded only if the returned object exposes usage_metadata.
    """
    started = perf_counter()
    log_event("step_started", stage=stage)
    try:
        result = operation()
    except ResearchFailure as exc:
        exc.stage = exc.stage or stage
        log_event("step_failed", stage=stage, duration_ms=round((perf_counter() - started) * 1000, 2), diagnostics=failure_details(exc))
        raise
    # Keep the original cause for diagnostics while showing only an authored public message.
    except Exception as exc:
        log_event("step_failed", stage=stage, duration_ms=round((perf_counter() - started) * 1000, 2), diagnostics=failure_details(exc))
        raise ResearchFailure("step_failed", "This research step could not complete. Details were recorded in the server logs.", stage) from exc
    # Providers may supply usage counts; record numeric totals without the model's response text.
    usage = getattr(result, "usage_metadata", None)
    tokens = {k: usage[k] for k in ("input_tokens", "output_tokens", "total_tokens")
              if isinstance(usage.get(k), int)} if isinstance(usage, dict) else {}
    log_event("step_completed", stage=stage, duration_ms=round((perf_counter() - started) * 1000, 2), token_usage=tokens)
    return result


def failure_details(exc):
    """Return troubleshooting metadata from up to five explicitly linked exceptions.

    The result contains the workflow stage, error code and locations in the code.
    Provider response bodies and invalid submitted values are excluded. A generic
    exception uses the setup-stage fallback until a caller assigns a more specific
    stage through ResearchFailure or its surrounding log event.
    """
    chain = []
    current = exc
    # Follow at most five linked causes; never include their raw messages or input values.
    while current is not None and len(chain) < 5:
        entry = {"error_type": type(current).__name__, "frames": [
            {"file": Path(f.filename).name, "line": f.lineno, "function": f.name}
            for f in traceback.extract_tb(current.__traceback__)]}
        if isinstance(current, ValidationError):
            entry["invalid_fields"] = [{"field": ".".join(map(str, e["loc"])), "type": e["type"]}
                                       for e in current.errors(include_input=False, include_context=False)]
        chain.append(entry)
        current = current.__cause__
    return {"stage": exc.stage if isinstance(exc, ResearchFailure) else "Research setup",
            "code": exc.code if isinstance(exc, ResearchFailure) else "setup_failed", "errors": chain}
