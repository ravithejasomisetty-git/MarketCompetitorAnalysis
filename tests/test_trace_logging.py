"""Verify durable request traces, safe diagnostics and isolation between concurrent users."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch
from langchain_core.messages import AIMessage
from market_analysis.offline_demo_research import DemoServices
from market_analysis.research_errors import run_step, ResearchFailure
from market_analysis.trace_logging import request_trace, resume_trace, log_event, safe_url, sanitize_existing_logs
from market_analysis.workflow_orchestrator import compare

BRIEF = dict(category="Electric bicycle", market="India", price_min=30000, price_max=50000, currency="INR")


class TraceLoggingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="market-trace-tests-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        override = patch.dict(os.environ, {"MARKET_LOG_DIR": folder.name})
        override.start()
        self.addCleanup(override.stop)

    def read(self, trace):
        return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]

    def test_trace_explains_decisions_and_filtering_and_resumes_for_export(self):
        services = DemoServices()
        original = services.research
        def research(*args):
            products, sources = original(*args)
            products[0].currency = "USD"
            return products, sources
        services.research = research
        with request_trace(BRIEF, demo=True) as trace:
            compare(BRIEF, services)
        with resume_trace(trace.request_id):
            log_event("export_completed", bytes=123)
        records = self.read(trace)
        self.assertTrue(all(e["request_id"] == trace.request_id for e in records))
        self.assertEqual(records[0]["event"], "request_started")
        self.assertEqual(records[-1]["event"], "export_completed")
        rejected = next(e for e in records if e["event"] == "competitor_excluded")
        self.assertEqual(rejected["details"]["reason"], "wrong_currency")
        decisions = [e for e in records if e["event"] == "orchestrator_decision"]
        self.assertEqual(decisions[2]["details"]["known_state"]["eligible_products"], {"redacted": True, "count": 2})
        self.assertTrue(decisions[2]["details"]["reason"])
        self.assertTrue(any(e["event"] == "step_completed" and e["details"]["duration_ms"] >= 0 for e in records))

    def test_raw_provider_errors_and_secrets_are_excluded(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-value"}):
            with request_trace(BRIEF) as trace:
                log_event("test", focus="use test-key-value and sk-unknown-secret", api_key="other-secret")
                try:
                    run_step("Search", lambda: (_ for _ in ()).throw(ValueError("private-provider-body")))
                except ResearchFailure as exc:
                    trace.fail(exc)
        content = trace.path.read_text(encoding="utf-8")
        for forbidden in ["test-key-value", "sk-unknown-secret", "other-secret", "private-provider-body"]:
            self.assertNotIn(forbidden, content)
        self.assertIn("ValueError", content)
        self.assertEqual(self.read(trace)[-1]["details"]["outcome"], "failed")
        self.assertEqual(safe_url("https://user:pass@example.com/page?token=secret#fragment"), "https://example.com/page")

    def test_concurrent_traces_do_not_mix_and_context_is_reset_after_errors(self):
        def work(marker):
            with request_trace({"marker": marker}) as trace:
                log_event("marker", count=marker)
            return trace
        with ThreadPoolExecutor(max_workers=2) as pool:
            traces = list(pool.map(work, [1, 2]))
        self.assertNotEqual(traces[0].request_id, traces[1].request_id)
        for trace, marker in zip(traces, [1, 2]):
            self.assertEqual([e["details"]["count"] for e in self.read(trace) if e["event"] == "marker"], [marker])
        with self.assertRaises(ValueError):
            with request_trace(BRIEF) as failed:
                raise ValueError("not logged")
        before = failed.path.read_text(encoding="utf-8")
        log_event("outside_any_request")
        self.assertEqual(failed.path.read_text(encoding="utf-8"), before)

    def test_step_logs_usage_without_model_content(self):
        with request_trace(BRIEF) as trace:
            run_step("Model", lambda: AIMessage(content="private-model-content", usage_metadata={
                "input_tokens": 12, "output_tokens": 3, "total_tokens": 15}))
        record = next(e for e in self.read(trace) if e["event"] == "step_completed")
        self.assertEqual(record["details"]["token_usage"]["total_tokens"], 15)
        self.assertNotIn("private-model-content", trace.path.read_text(encoding="utf-8"))

    def test_missing_prerequisite_and_retry_are_visible(self):
        service = DemoServices()
        actions = ["finish_report", "discover_market", "research_products", "research_products", "assess_market", "finish_report"]
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [AIMessage(content="", tool_calls=[{
            "name": name, "args": {"reason": "Verify current findings"} | ({} if name == "finish_report" else {"focus": "Inspect evidence"}),
            "id": str(i)}]) for i, name in enumerate(actions)]
        service.orchestrator_model = Mock(return_value=model)
        with request_trace(BRIEF) as trace:
            compare(BRIEF, service)
        records = self.read(trace)
        self.assertTrue(any(e["event"] == "action_blocked" and e["details"]["reason"] == "current_assessment_required" for e in records))
        self.assertEqual(sum(e["event"] == "orchestrator_decision" and e["details"]["action"] == "research_products" for e in records), 2)

    def test_private_inputs_and_model_text_never_enter_trace(self):
        private = "Confidential venture alice@example.com +91-9876543210"
        with request_trace({"category": private, "product": private, "price_min": 12345}) as trace:
            log_event("orchestrator_decision", action="research_products", reason=private,
                      assignment=private, query=private, known_state={"eligible_products": [private]},
                      stage=f"Product research: {private} / search", url="https://user:pass@example.com/private?secret=123",
                      diagnostics={"headers": {"Authorization": private}}, private_name=private)
        content = trace.path.read_text(encoding="utf-8")
        for value in ["Confidential", "alice@example.com", "9876543210", "12345", "user:pass", "private?secret", "private_name"]:
            self.assertNotIn(value, content)
        self.assertIn("research_products", content)
        self.assertIn("Product research: [product] / search", content)

    def test_known_credentials_are_redacted_even_in_metadata_fields(self):
        with patch.dict(os.environ, {"OTHER_SERVICE_PASSWORD": "provider-private-value"}):
            with request_trace(BRIEF) as trace:
                log_event("test", model="provider-private-value", code="sk-unknown-key")
        self.assertNotIn("provider-private-value", trace.path.read_text(encoding="utf-8"))
        self.assertNotIn("sk-unknown-key", trace.path.read_text(encoding="utf-8"))

    def test_legacy_logs_are_scrubbed_without_losing_event_identity(self):
        path = self.folder / ("a" * 32 + ".jsonl")
        path.write_text(json.dumps({"timestamp": "2026-10-23T10:58:50+00:00", "request_id": "a" * 32,
            "event": "orchestrator_decision", "details": {"assignment": "private-project", "reason": "private-project",
            "action": "research_products", "known_state": {"candidates": ["private-project"]}}}) + "\n", encoding="utf-8")
        self.assertEqual(sanitize_existing_logs(), 1)
        content = path.read_text(encoding="utf-8")
        record = json.loads(content)
        self.assertNotIn("private-project", content)
        self.assertEqual(record["request_id"], "a" * 32)
        self.assertEqual(record["details"]["action"], "research_products")
        self.assertFalse(list(self.folder.glob(".sanitized-*")))
