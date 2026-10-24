"""Exercise specialist search/fetch conversations with controlled model and provider responses.

Mocks supply predictable answers so these checks make no paid research calls.
"""
import json
import unittest
from unittest.mock import Mock, patch
from market_analysis.research_errors import ResearchFailure
from langchain_core.messages import AIMessage
from market_analysis.live_research_services import LiveServices
from market_analysis.react_specialist_agents import DISCOVERY_AGENT, PRODUCT_RESEARCH_AGENT
from market_analysis.offline_demo_research import DemoServices
from test_comparison import REQUEST


def tool_call(tool, args, identity):
    """Create the same action-request structure a real AI provider would return."""
    return {"name": tool, "args": args, "id": identity}

class AgentTests(unittest.TestCase):
    def test_shared_model_is_loaded_from_environment(self):
        with patch.dict("os.environ", {"OPENAI_MODEL": " configured-model ", "MODEL_ROUTING_ENABLED": "false"}), patch(
                "market_analysis.live_research_services.ChatOpenAI") as model, patch(
                "market_analysis.live_research_services.TavilyClient"), patch(
                "market_analysis.live_research_services.log_event") as log:
            service = LiveServices("test-openai", "test-tavily")
            self.assertEqual(model.call_args.kwargs["model"], "configured-model")
            self.assertIs(service.orchestrator_model().primary, model.return_value)
            self.assertEqual(log.call_args.kwargs["model"], "configured-model")

    def test_missing_model_is_rejected_before_provider_initialization(self):
        for value in ["", "   "]:
            with patch.dict("os.environ", {"OPENAI_MODEL": value}), patch(
                    "market_analysis.live_research_services.ChatOpenAI") as model:
                with self.assertRaises(ResearchFailure) as failure:
                    LiveServices("test-openai", "test-tavily")
                self.assertEqual(failure.exception.code, "missing_model")
                model.assert_not_called()

    def batch_fixture(self, fetch_fails=False):
        """Prepare four fictional products and controlled search/page responses for agent tests."""
        fixture, sources = DemoServices().fixture(REQUEST)
        products = [fixture.selected_product, *fixture.competitors]
        names = [p.name for p in products]
        model, backend = Mock(), Mock()
        backend.search.return_value = sources
        backend.extract.return_value = [sources[0] | {"id": "page", "retrieval_method": "product_page", "excerpt": "Full product details"}]
        if fetch_fails:
            backend.extract.side_effect = TimeoutError("secret provider payload")
        calls = []
        for i, name in enumerate(names):
            calls += [tool_call("search_product", {"name": name, "query": name + " official England"}, f"s{i}"),
                tool_call("fetch_product_page", {"name": name, "url": sources[0]["url"], "kind": "official"}, f"f{i}")]
        model.bind_tools.return_value.invoke.side_effect = [AIMessage(content="", tool_calls=calls), AIMessage(content="Done")]
        model.with_structured_output.return_value.invoke.return_value = {
            f"product_{i}": p.model_dump() for i, p in enumerate(products)}
        return model, backend, names

    def test_one_research_conversation_fetches_all_four_products(self):
        model, backend, names = self.batch_fixture()
        products, sources = PRODUCT_RESEARCH_AGENT.run(model, backend, REQUEST, names)
        model.bind_tools.assert_called_once()
        model.with_structured_output.return_value.invoke.assert_called_once()
        self.assertEqual([p.name for p in products], names)
        self.assertEqual(backend.search.call_count, 4)
        self.assertEqual(backend.extract.call_count, 4)
        self.assertTrue(all("Fetched product page" in p.website_notes for p in products))
        self.assertTrue(any(s["id"] == "page" for s in sources))
        messages = model.with_structured_output.return_value.invoke.call_args.args[0]
        self.assertTrue(any("Full product details" in m.content for m in messages if m.type == "tool"))

    def test_fetch_failures_are_explicit_without_cancelling_batch(self):
        model, backend, names = self.batch_fixture(fetch_fails=True)
        products, _ = PRODUCT_RESEARCH_AGENT.run(model, backend, REQUEST, names)
        self.assertTrue(all("Page fetch failed" in p.website_notes for p in products))
        self.assertNotIn("secret provider payload", json.dumps([p.model_dump() for p in products]))

    def test_citations_are_scoped_per_product(self):
        model, backend, names = self.batch_fixture()
        _, sources = DemoServices().fixture(REQUEST)
        backend.search.side_effect = [[sources[0] | {"id": f"s{i}"}] for i in range(4)]
        backend.extract.side_effect = [[sources[0] | {"id": f"p{i}"}] for i in range(4)]
        model.with_structured_output.return_value.invoke.return_value = {
            f"product_{i}": DemoServices().fixture(REQUEST)[0].selected_product.model_dump() | {"source_ids": [f"s{i}"]} for i in range(4)}
        PRODUCT_RESEARCH_AGENT.run(model, backend, REQUEST, names)
        schema = model.with_structured_output.call_args.args[0]
        facts = schema.model_fields["product_0"].annotation
        self.assertEqual(set(facts.model_json_schema()["properties"]["source_ids"]["items"]["enum"]), {"s0", "p0"})
        with self.assertRaises(ValueError):
            facts.model_validate(DemoServices().fixture(REQUEST)[0].selected_product.model_dump() | {"source_ids": ["s1"]})

    def test_unretrieved_page_is_never_fetched(self):
        model, backend, names = self.batch_fixture()
        model.bind_tools.return_value.invoke.side_effect = [AIMessage(content="", tool_calls=[
            tool_call("fetch_product_page", {"name": names[0], "url": "https://invented.example/product", "kind": "official"}, "bad")])] + [AIMessage(content="Done")] * 22
        PRODUCT_RESEARCH_AGENT.run(model, backend, REQUEST, names)
        backend.extract.assert_not_called()

    def test_extract_validates_public_url_and_records_raw_page(self):
        service = LiveServices.__new__(LiveServices)
        service.web = Mock()
        with self.assertRaises(ValueError):
            service.extract("http://127.0.0.1/private")
        service.web.extract.assert_not_called()
        service.web.extract.return_value = {"results": [{"url": "https://example.com/product", "raw_content": "Full page text"}]}
        evidence = service.extract("https://example.com/product")
        self.assertEqual(evidence[0]["excerpt"], "Full page text")
        self.assertEqual(evidence[0]["retrieval_method"], "product_page")

    def test_discovery_still_uses_react_search(self):
        _, sources = DemoServices().fixture(REQUEST)
        model, backend = Mock(), Mock()
        backend.search.return_value = sources
        model.bind_tools.return_value.invoke.side_effect = [AIMessage(content="", tool_calls=[tool_call("search_web", {"query": "bicycles"}, "one")]), AIMessage(content="Done")]
        model.with_structured_output.return_value.invoke.return_value = {"names": ["Example product A"]}
        shortlist, _ = DISCOVERY_AGENT.run(model, backend, {"request": REQUEST})
        self.assertEqual(shortlist.names, ["Example product A"])
