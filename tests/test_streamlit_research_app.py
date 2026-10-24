"""Exercise the actual interface, including optional fields and saved slide downloads."""
import unittest
import json
import tempfile
from pathlib import Path
from contextlib import contextmanager
from support.fake_research_services import DemoServices
from market_analysis.trace_logging import request_trace as original_trace, resume_trace as original_resume
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "streamlit_research_app.py")


def button(app, label):
    return next(b for b in app.button if b.label == label)


class AppTests(unittest.TestCase):
    def setUp(self):
        # Tests write real log files into a private temporary directory, not the project's logs.
        folder = tempfile.TemporaryDirectory(prefix="market-ui-logs-")
        self.addCleanup(folder.cleanup)
        self.log_dir = Path(folder.name)
        override = patch.dict("os.environ", {"MARKET_LOG_DIR": folder.name,
            "OPENAI_API_KEY": "test-key", "TAVILY_API_KEY": "test-key"})
        override.start()
        self.addCleanup(override.stop)

        # Production is live-only. Tests replace the provider boundary with fixtures,
        # and route test observations locally without introducing a hidden demo mode.
        service = DemoServices()
        service.demo = False
        backend = patch("market_analysis.live_research_services.LiveServices", return_value=service)
        backend.start(); self.addCleanup(backend.stop)
        @contextmanager
        def local_trace(brief, **kwargs):
            with original_trace(brief, demo=True) as trace:
                yield trace
        @contextmanager
        def local_resume(request_id, **kwargs):
            with original_resume(request_id, demo=True) as trace:
                yield trace
        for target, replacement in [("request_trace", local_trace), ("resume_trace", local_resume)]:
            boundary = patch("market_analysis.trace_logging." + target, replacement)
            boundary.start(); self.addCleanup(boundary.stop)

    def events(self):
        return [json.loads(line) for path in self.log_dir.glob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]

    def app(self):
        return AppTest.from_file(APP, default_timeout=30).run()

    def test_safety_decline_shows_polite_warning_without_research(self):
        app = self.app()
        with patch("support.fake_research_services.DemoServices.screen_request",
                   return_value={"outcome": "decline", "reason": "instruction_override"}), \
             patch("support.fake_research_services.DemoServices.orchestrator_model") as coordinator:
            button(app, "Explore market").click().run()
        coordinator.assert_not_called()
        self.assertFalse(app.exception)
        self.assertTrue(app.warning)
        self.assertIn("Please rephrase", app.warning[0].value)
        self.assertIsNone(app.session_state["result"])
        events = self.events()
        self.assertTrue(any(e["event"] == "request_rejected" for e in events))
        self.assertFalse(any(e["event"] == "orchestrator_decision" for e in events))

    def test_default_is_market_exploration_without_product_fields(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertFalse(app.checkbox(key="has_product").value)
        self.assertNotIn("f_product", [x.key for x in app.text_input])
        self.assertNotIn("f_url", [x.key for x in app.text_input])
        self.assertEqual(app.text_input(key="f_market").value, "India")
        self.assertEqual(app.text_input(key="f_currency").value, "INR")
        button(app, "Explore market").click().run()
        self.assertFalse(app.exception)
        self.assertIsNone(app.session_state["result"]["comparison"]["selected_product"])
        self.assertEqual(len(app.session_state["result"]["comparison"]["competitors"]), 3)
        self.assertTrue(any("Slide 1 of 5" in item.value for item in app.caption))
        self.assertFalse(app.toggle)
        self.assertFalse(app.json)
        self.assertFalse(app.get("link_button"))
        self.assertEqual(len(list(self.log_dir.glob("*.jsonl"))), 1)
        decisions = [e for e in self.events() if e["event"] == "orchestrator_decision"]
        self.assertEqual(len(decisions), 4)
        self.assertTrue(all(e["details"]["reason"] for e in decisions))
        self.assertFalse(app.table)  # No activity trace is rendered in the browser.
        self.assertNotIn("Diagnostic details", [e.label for e in app.expander])

    def test_checkbox_reveals_product_and_website_is_optional(self):
        app = self.app()
        app.checkbox(key="has_product").check().run()
        app.text_input(key="f_product").set_value("My commuter bicycle")
        self.assertEqual(app.text_input(key="f_url").value, "")
        button(app, "Explore market").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        result = app.session_state["result"]
        self.assertEqual(result["comparison"]["selected_product"]["name"], "My commuter bicycle")
        self.assertIsNone(result["request"]["company_url"])
        self.assertTrue(any("Slide 1 of 6" in item.value for item in app.caption))

    def test_arrow_navigation_stops_at_boundaries_without_research(self):
        app = self.app()
        button(app, "Explore market").click().run()
        self.assertTrue(app.button(key="previous_slide").disabled)
        with patch("market_analysis.workflow_orchestrator.compare") as research:
            for index in range(1, 5):
                app.button(key="next_slide").click().run()
                self.assertEqual(app.session_state["slide_index"], index)
                self.assertTrue(any(f"Slide {index + 1} of 5" in item.value for item in app.caption))
            self.assertTrue(app.button(key="next_slide").disabled)
            app.button(key="previous_slide").click().run()
            self.assertEqual(app.session_state["slide_index"], 3)
            self.assertFalse(app.button(key="next_slide").disabled)
            research.assert_not_called()
        self.assertFalse(app.exception)

    def test_launch_ui_has_no_demo_sources_or_diagnostics_controls(self):
        app = self.app()
        self.assertFalse(app.toggle)
        button(app, "Explore market").click().run()
        self.assertFalse(app.toggle)
        self.assertFalse(app.json)
        self.assertFalse(app.get("link_button"))
        self.assertFalse(app.exception)
        with patch("market_analysis.workflow_orchestrator.compare") as research:
            # Retired session flags cannot expose hidden controls or raw details.
            app.session_state["demo_mode"] = True
            app.session_state["show_sources"] = True
            app.run()
            self.assertFalse(app.json)
            self.assertFalse(app.toggle)
            research.assert_not_called()
        self.assertEqual(sum(e["event"] == "request_started" for e in self.events()), 1)

    def test_old_demo_report_and_download_are_discarded(self):
        app = self.app()
        button(app, "Explore market").click().run()
        old = dict(app.session_state["result"])
        old["demo"] = True
        app.session_state["result"] = old
        app.session_state["pptx"] = b"fictional-presentation"
        app.run()
        self.assertIsNone(app.session_state["result"])
        self.assertIsNone(app.session_state["pptx"])
        self.assertFalse(app.get("download_button"))

    def test_old_prepared_deck_and_out_of_range_selection_are_reset(self):
        app = self.app()
        button(app, "Explore market").click().run()
        # Seed an old session to verify a stale slide position is safely reset.
        migrated = AppTest.from_file(APP, default_timeout=30)
        migrated.session_state["result"] = app.session_state["result"]
        migrated.session_state["presentation_version"] = "old-many-slide-layout"
        migrated.session_state["pptx"] = b"old-deck"
        migrated.session_state["slide_index"] = 35
        migrated.run()
        self.assertFalse(migrated.exception)
        self.assertEqual(migrated.session_state["slide_index"], 0)
        self.assertIsNone(migrated.session_state["pptx"])

    def test_hidden_product_does_not_leak_and_fields_survive_toggle(self):
        app = self.app()
        app.checkbox(key="has_product").check().run()
        app.text_input(key="f_product").set_value("Private product").run()
        app.text_input(key="f_url").set_value("https://example.com").run()
        app.checkbox(key="has_product").uncheck().run()
        button(app, "Explore market").click().run()
        self.assertIsNone(app.session_state["result"]["request"]["product"])
        self.assertIsNone(app.session_state["result"]["request"]["company_url"])
        app.checkbox(key="has_product").check().run()
        self.assertEqual(app.text_input(key="f_product").value, "Private product")

    def test_missing_existing_product_name_does_not_research(self):
        app = self.app()
        app.checkbox(key="has_product").check().run()
        with patch("market_analysis.workflow_orchestrator.compare") as research:
            button(app, "Explore market").click().run()
            research.assert_not_called()
        self.assertTrue(app.error)
        self.assertTrue(any(e["event"] == "request_rejected" and e["details"]["reason"] == "existing_product_name_required" for e in self.events()))

    def test_missing_credentials_is_logged_before_any_provider_is_called(self):
        app = AppTest.from_file(APP, default_timeout=30).run()
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "TAVILY_API_KEY": ""}), patch(
                "market_analysis.workflow_orchestrator.compare") as research:
            button(app, "Explore market").click().run()
            research.assert_not_called()
        self.assertTrue(app.error)
        self.assertTrue(any(e["event"] == "request_rejected" and e["details"]["reason"] == "missing_provider_credentials" for e in self.events()))

    def test_local_logging_failure_does_not_block_research(self):
        app = self.app()
        with patch("market_analysis.trace_logging.RequestTrace._write_local", side_effect=PermissionError()):
            button(app, "Explore market").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertIsNotNone(app.session_state["result"])
        self.assertTrue((self.log_dir / "operations.log").exists())

    def test_invalid_category_does_not_research(self):
        app = self.app()
        app.text_input(key="f_category").set_value("")
        with patch("market_analysis.workflow_orchestrator.compare") as research:
            button(app, "Explore market").click().run()
            research.assert_not_called()
        self.assertTrue(app.error)
        self.assertEqual(self.events()[-1]["details"]["outcome"], "rejected")
        self.assertTrue(any(e["event"] == "request_rejected" for e in self.events()))

    def test_slides_and_pptx_download_survive_rerun_without_research(self):
        app = self.app()
        button(app, "Explore market").click().run()
        with patch("market_analysis.workflow_orchestrator.compare") as research, patch(
                "market_analysis.presentation_export.powerpoint_export", return_value=b"pptx-fixture") as export:
            app.button(key="next_slide").click().run()
            button(app, "Prepare PowerPoint").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("download_button")), 1)
            app.run()
            research.assert_not_called()
            export.assert_called_once()
        self.assertEqual(len(list(self.log_dir.glob("*.jsonl"))), 1)
        self.assertEqual(sum(e["event"] == "request_started" for e in self.events()), 1)
        self.assertTrue(any(e["event"] == "export_completed" for e in self.events()))
        button(app, "Clear analysis").click().run()
        self.assertIsNone(app.session_state["result"])
        self.assertIsNone(app.session_state["pptx"])

    def test_export_failure_keeps_research_and_can_retry(self):
        app = self.app()
        button(app, "Explore market").click().run()
        with patch("market_analysis.presentation_export.powerpoint_export", side_effect=RuntimeError("private")):
            button(app, "Prepare PowerPoint").click().run()
        self.assertIsNotNone(app.session_state["result"])
        self.assertNotIn("private", app.error[0].value)
        with patch("market_analysis.presentation_export.powerpoint_export", return_value=b"pptx-fixture"):
            button(app, "Prepare PowerPoint").click().run()
        self.assertFalse(app.error)

    def test_discovery_failure_is_safe_persistent_and_recoverable(self):
        app = self.app()
        with patch("support.fake_research_services.DemoServices.discover", side_effect=ValueError("secret-provider-payload")):
            button(app, "Explore market").click().run()
        self.assertFalse(app.exception)
        self.assertNotIn("Competitor discovery", app.error[0].value)
        self.assertIn("Please retry", app.error[0].value)
        self.assertNotIn("secret-provider-payload", app.error[0].value)
        self.assertEqual(self.events()[-1]["details"]["outcome"], "failed")
        self.assertNotIn("secret-provider-payload", json.dumps(self.events()))
        app.run()
        self.assertTrue(app.error)
        button(app, "Explore market").click().run()
        self.assertFalse(app.error)

    def test_new_analysis_discards_previous_powerpoint(self):
        app = self.app()
        button(app, "Explore market").click().run()
        with patch("market_analysis.presentation_export.powerpoint_export", return_value=b"pptx-fixture"):
            button(app, "Prepare PowerPoint").click().run()
        app.text_input(key="f_market").set_value("England")
        button(app, "Explore market").click().run()
        self.assertIsNone(app.session_state["pptx"])
        self.assertEqual(app.session_state["result"]["request"]["market"], "England")
