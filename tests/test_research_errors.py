"""Ensure failures identify the responsible step without exposing provider messages or input values.

Mocks supply predictable answers so these checks make no paid research calls.
"""
import json
import unittest
from market_analysis.research_errors import ResearchFailure, failure_details, run_step
from market_analysis.research_schemas import Shortlist

class ResearchErrorTests(unittest.TestCase):
    def test_provider_payloads_never_enter_diagnostics(self):
        def fail():
            raise ValueError("secret-provider-payload")
        with self.assertRaises(ResearchFailure) as captured:
            run_step("Competitor discovery / structured response", fail)
        details = failure_details(captured.exception)
        self.assertEqual(details["stage"], "Competitor discovery / structured response")
        self.assertEqual(details["errors"][1]["error_type"], "ValueError")
        self.assertNotIn("secret-provider-payload", json.dumps(details))
        self.assertTrue(details["errors"][1]["frames"])

    def test_schema_diagnostics_show_fields_without_values(self):
        with self.assertRaises(ResearchFailure) as captured:
            run_step("Discovery response", lambda: Shortlist.model_validate({"names": "secret-payload"}))
        details = failure_details(captured.exception)
        self.assertEqual(details["errors"][1]["invalid_fields"][0]["field"], "names")
        self.assertNotIn("secret-payload", json.dumps(details))
