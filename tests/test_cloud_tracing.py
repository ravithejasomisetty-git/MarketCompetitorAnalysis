"""Check Cloud routing, failure isolation and privacy without paid research calls."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage
from market_analysis import cloud_tracing
from market_analysis.cloud_tracing import MetadataModelCallback, operational_warning
from market_analysis.trace_logging import request_trace, resume_trace, log_event, model_callbacks
from market_analysis.research_errors import run_step, ResearchFailure
from market_analysis.offline_demo_research import DemoServices
from market_analysis.workflow_orchestrator import compare

BRIEF = dict(category="Electric bicycle", market="India", price_min=30000, price_max=50000, currency="INR")


class FakeSpan:
    def __init__(self, client, arguments):
        self.client, self.arguments = client, arguments
        self.id = f"{len(client.spans) + 1:016x}"
        self.updates, self.events, self.ended = [], [], False

    def update(self, **kwargs):
        self.updates.append(kwargs)

    def create_event(self, **kwargs):
        self.events.append(kwargs)

    def end(self):
        self.ended = True


class FakeClient:
    def __init__(self):
        self.spans = []

    def start_observation(self, **kwargs):
        span = FakeSpan(self, kwargs)
        self.spans.append(span)
        return span


class CloudTracingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="market-cloud-tests-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"MARKET_LOG_DIR": folder.name})
        env.start(); self.addCleanup(env.stop)
        self.client = FakeClient()
        cloud = patch("market_analysis.cloud_tracing.get_cloud_client", return_value=self.client)
        cloud.start(); self.addCleanup(cloud.stop)

    def test_live_tree_has_nested_steps_safe_events_and_no_jsonl(self):
        with request_trace(BRIEF) as trace:
            def outer():
                run_step("Product research: Private product / search", lambda: log_event(
                    "web_search_result", query="private-query", url="https://private.example.com", source_count=3))
            run_step("Product research agent", outer)
        spans = self.client.spans
        self.assertEqual(spans[0].arguments["trace_context"]["trace_id"], trace.request_id)
        self.assertEqual(spans[2].arguments["trace_context"]["parent_span_id"], spans[1].id)
        self.assertTrue(all(s.ended for s in spans))
        data = json.dumps([dict(arguments=s.arguments, events=s.events, updates=s.updates) for s in spans])
        for private in ("Private product", "private-query", "private.example.com", "Electric bicycle"):
            self.assertNotIn(private, data)
        self.assertFalse(list(self.root.glob("*.jsonl")))

    def test_rejected_screening_never_starts_coordinator(self):
        services = DemoServices()
        services.screen_request = Mock(return_value={"outcome": "decline", "reason": "instruction_override"})
        services.orchestrator_model = Mock()
        with request_trace(BRIEF) as trace:
            try:
                compare(BRIEF, services)
            except ResearchFailure:
                trace.reject("prompt_injection_detected")
        services.orchestrator_model.assert_not_called()
        self.assertEqual(self.client.spans[1].arguments["as_type"], "guardrail")
        self.assertEqual(self.client.spans[0].updates[-1]["metadata"]["outcome"], "rejected")

    def test_export_reuses_trace_id_without_reading_local_file(self):
        with request_trace(BRIEF) as trace:
            pass
        with resume_trace(trace.request_id):
            run_step("PowerPoint export", lambda: log_event("export_completed", bytes=200))
        roots = [s for s in self.client.spans if "parent_span_id" not in s.arguments["trace_context"]]
        self.assertEqual(len(roots), 2)
        self.assertEqual(roots[0].arguments["trace_context"], roots[1].arguments["trace_context"])
        self.assertFalse(list(self.root.glob("*.jsonl")))

    def test_offline_demo_never_contacts_cloud(self):
        with patch("market_analysis.cloud_tracing.get_cloud_client") as cloud:
            with request_trace(BRIEF, demo=True) as trace:
                log_event("test", count=1)
            with resume_trace(trace.request_id, demo=True):
                log_event("export_completed", bytes=200)
            cloud.assert_not_called()
        self.assertTrue(trace.path.exists())

    def test_delivery_failure_and_raw_sdk_errors_do_not_break_research(self):
        self.client.start_observation = Mock(side_effect=RuntimeError("private-provider-body sk-lf-secret"))
        with request_trace(BRIEF):
            self.assertEqual(run_step("Research", lambda: 42), 42)
        content = (self.root / "operations.log").read_text(encoding="utf-8")
        self.assertIn("langfuse_delivery_failed", content)
        self.assertNotIn("private-provider-body", content)
        self.assertNotIn("sk-lf-secret", content)
        self.assertFalse(list(self.root.glob("*.jsonl")))

    def test_model_usage_has_no_message_or_response_content(self):
        with request_trace(BRIEF):
            callback = model_callbacks("gpt-4o-mini")[0]
            callback.on_chat_model_start({}, ["private prompt"], run_id="model-1")
            message = AIMessage(content="private answer", usage_metadata={"input_tokens": 11,"output_tokens": 7,"total_tokens": 18})
            response = SimpleNamespace(generations=[[SimpleNamespace(message=message)]], llm_output=None)
            callback.on_llm_end(response, run_id="model-1")
        generation = next(s for s in self.client.spans if s.arguments["as_type"] == "generation")
        self.assertEqual(generation.updates[0]["usage_details"], {"input": 11,"output": 7,"total": 18})
        self.assertNotIn("private", json.dumps(generation.arguments | generation.updates[0]))

    def test_failed_model_and_missing_usage_are_explicit(self):
        with request_trace(BRIEF):
            callback = model_callbacks("configured-model")[0]
            callback.on_chat_model_start({}, [], run_id="model-error")
            callback.on_llm_error(ValueError("sensitive"), run_id="model-error")
        generation = self.client.spans[-1]
        self.assertEqual(generation.updates[-1], {"usage_details": {}, "level": "ERROR"})

    def test_fallback_provider_and_reason_are_visible_without_prompt_data(self):
        from openai import APIConnectionError
        import httpx
        from market_analysis.model_router import ModelRouter
        primary, backup = Mock(), Mock()
        primary.invoke.side_effect = APIConnectionError(request=httpx.Request('POST','https://example.com'))
        backup.invoke.return_value = AIMessage(content='private output')
        with request_trace(BRIEF), patch('market_analysis.model_router.sleep'):
            callback = model_callbacks('Qwen/configured-model', provider='nebius')[0]
            callback.on_chat_model_start({}, ['private prompt'], run_id='backup-call')
            callback.on_llm_error(ValueError('private error'), run_id='backup-call')
            ModelRouter(primary,'primary-model',backup,'Qwen/configured-model').invoke(['private prompt'])
        generation = next(s for s in self.client.spans if s.arguments['as_type']=='generation')
        self.assertEqual(generation.arguments['metadata']['provider'],'nebius')
        events = [e for s in self.client.spans for e in s.events]
        switched = next(e for e in events if e['name']=='model_fallback')
        self.assertEqual(switched['metadata']['provider'],'nebius')
        self.assertEqual(switched['metadata']['reason'],'model_connection_failed')
        self.assertNotIn('private',json.dumps(events))

    def test_concurrent_requests_have_distinct_parent_trees(self):
        def run(_):
            with request_trace(BRIEF) as trace:
                run_step("Research", lambda: log_event("test", count=1))
                return trace.request_id
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(run, range(2)))
        self.assertEqual(len(set(ids)), 2)
        by_id = {s.id: s.arguments["trace_context"]["trace_id"] for s in self.client.spans}
        for span in self.client.spans:
            context = span.arguments["trace_context"]
            if "parent_span_id" in context:
                self.assertEqual(by_id[context["parent_span_id"]], context["trace_id"])

    def test_rotation_and_disk_failure_are_nonblocking(self):
        operational_warning("langfuse_delivery_failed")
        handler = cloud_tracing._handlers[self.root / "operations.log"]
        handler.maxBytes = 80
        for _ in range(12):
            operational_warning("langfuse_delivery_failed")
        self.assertTrue((self.root / "operations.log.1").exists())
        self.assertLessEqual(len(list(self.root.glob("operations.log*"))), 4)
        with patch("market_analysis.trace_logging.log_directory", side_effect=PermissionError()):
            # Misconfigured tracing must not affect the operation being traced.
            with request_trace(BRIEF):
                self.assertEqual(1 + 1, 2)

    def test_sdk_error_messages_and_unvalidated_ids_are_not_logged(self):
        import logging
        handler = cloud_tracing._PrivateSdkLogs()
        handler.emit(logging.LogRecord("langfuse", logging.ERROR, "", 0,
            "private-response-body sk-lf-secret", (), None))
        operational_warning("private-warning-text", "private-request-id")
        content = (self.root / "operations.log").read_text(encoding="utf-8")
        for private in ("private-response-body", "sk-lf-secret", "private-warning-text", "private-request-id"):
            self.assertNotIn(private, content)
        self.assertIn("langfuse_delivery_failed", content)


class ConfigurationTests(unittest.TestCase):
    def test_disabled_and_missing_configuration_do_not_create_sdk(self):
        with patch.object(cloud_tracing, "_client", None), patch.dict(os.environ,
                {"LANGFUSE_TRACING_ENABLED": "false"}), patch("langfuse.Langfuse") as sdk:
            self.assertIsNone(cloud_tracing.get_cloud_client())
            sdk.assert_not_called()
        with patch.object(cloud_tracing, "_client", None), patch.dict(os.environ,
                {"LANGFUSE_TRACING_ENABLED": "true", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}), \
                patch("market_analysis.cloud_tracing.operational_warning") as warning:
            self.assertIsNone(cloud_tracing.get_cloud_client())
            warning.assert_called_once_with("langfuse_configuration_missing")

    def test_real_sdk_exports_only_explicit_metadata(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from market_analysis.trace_logging import _safe
        from uuid import uuid4
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        # A local in-memory exporter exercises the actual SDK with no network calls.
        client = Langfuse(public_key="pk-lf-" + uuid4().hex, secret_key="sk-lf-test",
            tracer_provider=provider, span_exporter=exporter, mask=lambda *, data, **_: _safe(data))
        with patch("market_analysis.cloud_tracing.get_cloud_client", return_value=client):
            with request_trace(BRIEF) as trace:
                run_step("Research", lambda: log_event("request_screened", outcome="allow", reason="screening_passed", prompt="private prompt"))
        spans = exporter.get_finished_spans()
        self.assertTrue(spans)
        self.assertTrue(all(f"{s.context.trace_id:032x}" == trace.request_id for s in spans))
        self.assertNotIn("private prompt", str([dict(s.attributes) for s in spans]))
        client.shutdown()
