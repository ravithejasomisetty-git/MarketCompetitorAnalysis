"""Check AI-selected action sequences, prerequisite checks, filtering and specialist call allowances.

Mocks supply predictable answers so these checks make no paid research calls.
"""
import unittest
from unittest.mock import Mock
from langchain_core.messages import AIMessage
from market_analysis.offline_demo_research import DemoServices
from market_analysis.research_schemas import Shortlist
from market_analysis.workflow_orchestrator import compare
from market_analysis.research_errors import ResearchFailure
from test_comparison import REQUEST

class OrchestratorTests(unittest.TestCase):
    def scripted_model(self, actions):
        """Replace an AI with predetermined tool requests to test the host without API calls."""
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [AIMessage(content="", tool_calls=[{
            "name": name, "args": {"reason": "Test the next permitted action"} | ({} if name == "finish_report" else {"focus": f"Assignment {index}"}),
            "id": str(index)}]) for index, name in enumerate(actions)]
        return model

    def test_exploration_has_no_invented_selected_product(self):
        service = DemoServices()
        service.research = Mock(wraps=service.research)
        result = compare({k: v for k, v in REQUEST.items() if k not in {"product", "company_url"}}, service)
        self.assertIsNone(result["comparison"]["selected_product"])
        self.assertEqual(len(service.research.call_args.args[1]), 3)
        self.assertEqual(len(result["comparison"]["assessments"]), 3)

    def test_ai_decides_to_research_again_before_assessment(self):
        service = DemoServices()
        model = self.scripted_model(["discover_market", "research_products", "research_products", "assess_market", "finish_report"])
        service.orchestrator_model = Mock(return_value=model)
        service.research = Mock(wraps=service.research)
        result = compare(REQUEST, service)
        self.assertEqual(service.research.call_count, 2)
        self.assertEqual(service.research.call_args.args[0]["agent_assignment"], "Assignment 2")
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        self.assertTrue(any(m.type == "tool" and "competitors" in m.content for m in messages))
        self.assertTrue(result["comparison"]["opportunities"])

    def test_premature_finish_cannot_bypass_research(self):
        service = DemoServices()
        model = self.scripted_model(["finish_report"] * 10)
        service.orchestrator_model = Mock(return_value=model)
        service.summarize = Mock(wraps=service.summarize)
        with self.assertRaises(ResearchFailure) as captured:
            compare(REQUEST, service)
        self.assertEqual(captured.exception.code, "orchestrator_limit")
        service.summarize.assert_not_called()

    def test_research_allowance_blocks_extra_spend(self):
        service = DemoServices()
        model = self.scripted_model(["discover_market", "research_products", "research_products", "research_products", "assess_market", "finish_report"])
        service.orchestrator_model = Mock(return_value=model)
        service.research = Mock(wraps=service.research)
        compare(REQUEST, service)
        self.assertEqual(service.research.call_count, 2)

    def test_no_candidates_still_produces_an_honest_gap_report(self):
        service = DemoServices()
        service.discover = Mock(return_value=(Shortlist(names=[]), []))
        result = compare({k: v for k, v in REQUEST.items() if k not in {"product", "company_url"}}, service)
        self.assertEqual(result["comparison"]["competitors"], [])
        self.assertEqual(result["comparison"]["opportunities"], [])
        self.assertIn("incomplete", result["comparison"]["verdict"])

    def test_assessment_cannot_invent_product_or_source(self):
        for field in ["name", "source_ids"]:
            service = DemoServices()
            original = service.summarize
            def invalid(*args, field=field):
                summary = original(*args)
                setattr(summary.assessments[0], field, "invented" if field == "name" else ["invented"])
                return summary
            service.summarize = invalid
            with self.assertRaises(ResearchFailure):
                compare(REQUEST, service)

    # The normal demo calls each specialist once; live orchestration may request a second pass.
    def test_exactly_three_agent_delegations(self):
        service = DemoServices()
        calls = []
        for method in ["discover", "research", "summarize"]:
            original = getattr(service, method)
            def tracked(*args, method=method, original=original):
                calls.append(method)
                return original(*args)
            setattr(service, method, Mock(side_effect=tracked))
        result = compare(REQUEST, service)
        self.assertEqual(calls, ["discover", "research", "summarize"])
        self.assertEqual(len(service.research.call_args.args[1]), 4)
        self.assertEqual(len(result["comparison"]["competitors"]), 3)
        self.assertEqual(len([e for e in result["activity"] if e["agent"] == "Product research agent"]), 1)

    def test_filters_before_comparison_agent(self):
        service = DemoServices()
        original = service.research
        def research(*args):
            products, sources = original(*args)
            products[1].price = 2000
            return products, sources
        service.research = research
        service.summarize = Mock(wraps=service.summarize)
        compare(REQUEST, service)
        self.assertEqual(len(service.summarize.call_args.args[2]), 2)

    def test_shortlist_deduplicates_and_excludes_selected_product(self):
        service = DemoServices()
        _, sources = service.fixture(REQUEST)
        service.discover = Mock(return_value=(Shortlist(names=[REQUEST["product"], "Example product A", "example product a"]), sources))
        service.research = Mock(wraps=service.research)
        result = compare(REQUEST, service)
        service.research.assert_called_once()
        self.assertEqual(len(service.research.call_args.args[1]), 2)
        self.assertEqual(len(result["comparison"]["competitors"]), 1)

    def test_missing_product_rejected_before_comparison(self):
        service = DemoServices()
        original = service.research
        service.research = lambda *args: (original(*args)[0][1:], original(*args)[1])
        service.summarize = Mock()
        with self.assertRaises(ResearchFailure):
            compare(REQUEST, service)
        service.summarize.assert_not_called()
