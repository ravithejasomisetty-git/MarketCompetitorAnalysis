"""Check request validation, qualifying prices and evidence rejection without live providers.

Mocks supply predictable answers so these checks make no paid research calls.
"""
import unittest
from unittest.mock import Mock
from pydantic import ValidationError
from market_analysis.workflow_orchestrator import compare
from market_analysis.research_errors import ResearchFailure
from support.fake_research_services import DemoServices

REQUEST = dict(company_url="https://example.com", category="Bicycles", product="My bike",
               market="England", price_min=800, price_max=1000, currency="GBP")

class ComparisonTests(unittest.TestCase):
    def test_invalid_input_never_calls_service(self):
        service = Mock()
        for changes in [dict(category=" "), dict(price_max=700), dict(company_url="http://127.0.0.1")]:
            with self.assertRaises(ValidationError):
                compare(REQUEST | changes, service)
        service.discover.assert_not_called()

    # An unknown selected-product price must never be replaced with the user's budget.
    def test_four_products_preserve_unknown_selected_price(self):
        result = compare(REQUEST, DemoServices())
        self.assertEqual(len(result["comparison"]["competitors"]), 3)
        self.assertIsNone(result["comparison"]["selected_product"]["price"])
        self.assertEqual(len(result["comparison"]["assessments"]), 4)
        self.assertTrue(result["demo"])

    def test_out_of_budget_or_wrong_currency_excluded_with_gap(self):
        comparison, sources = DemoServices().fixture(REQUEST)
        comparison.competitors[0].price = 2000
        comparison.competitors[1].currency = "USD"
        service = DemoServices()
        service.research = Mock(return_value=([comparison.selected_product, *comparison.competitors], sources))
        result = compare(REQUEST, service)
        self.assertEqual(len(result["comparison"]["competitors"]), 1)
        self.assertTrue(any("Only 1 of 3" in x for x in result["comparison"]["limitations"]))

    def test_invalid_citations_quarantine_product_without_stopping_comparison(self):
        comparison, sources = DemoServices().fixture(REQUEST)
        comparison.competitors[0].source_ids = ["fabricated"]
        service = DemoServices()
        service.research = Mock(return_value=([comparison.selected_product, *comparison.competitors], sources))
        result = compare(REQUEST, service)
        self.assertEqual(len(result["comparison"]["competitors"]), 2)
        self.assertTrue(any("evidence references could not be validated" in x for x in result["comparison"]["limitations"]))
        self.assertFalse(any("fabricated" in p["source_ids"] for p in result["comparison"]["competitors"]))

    def test_service_failure_propagates_without_research_restart(self):
        service = DemoServices()
        service.discover = Mock(side_effect=TimeoutError())
        with self.assertRaises(ResearchFailure) as error:
            compare(REQUEST, service)
        self.assertEqual(error.exception.stage, "Competitor discovery")
        self.assertIsInstance(error.exception.__cause__, TimeoutError)
        self.assertEqual(service.discover.call_count, 1)

if __name__ == "__main__":
    unittest.main()
