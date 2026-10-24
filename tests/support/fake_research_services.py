"""Test-only replacements for live providers, using fictional research data.

DemoServices implements the same operations as LiveServices. Its coordinator returns
a fixed sequence of action requests, while its research methods build synthetic
products and findings from the requested budget. This exercises the application's
real validation and presentation path without performing web research or AI calls."""
from datetime import datetime, timezone
from langchain_core.messages import AIMessage
from market_analysis.research_schemas import Comparison, Product, Source, Shortlist, Summary, ProductAssessment, Opportunity


class DemoOrchestratorModel:
    """Scripted tool requests for offline testing; explicitly not a live AI coordinator."""
    def bind_tools(self, tools, **kwargs):
        """Match the real model's interface; this offline substitute needs no tool registration."""
        return self

    def invoke(self, messages, **kwargs):
        # Each completed tool result advances this fixed demonstration by one step.
        # AIMessage imitates a provider response so the same tool host can execute it.
        step = sum(message.type == "tool" for message in messages)
        name = ["discover_market", "research_products", "assess_market", "finish_report"][min(step, 3)]
        args = {} if name == "finish_report" else {"focus": "Demonstrate " + name.replace("_", " ") + " with fictional data"}
        args["reason"] = ["Identify relevant fictional candidates for the market brief.",
                          "Candidate names are available; collect their fictional profiles.",
                          "Validated profiles are available; assess strengths, gaps and opportunities.",
                          "All demonstration findings have been assessed; finalize the report."][min(step, 3)]
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"demo-{step}"}])

# Match the live-service interface without calling the internet or spending API credits.
class DemoServices:
    demo = True

    def screen_request(self, request):
        """Simulate approval for the fictional demo; this is not a security classifier."""
        return {"outcome": "allow", "reason": "screening_passed"}

    def orchestrator_model(self):
        """Start a fresh scripted coordinator for this demonstration request."""
        return DemoOrchestratorModel()

    def fixture(self, request):
        """Build fresh fictional products and one labelled source for this request.
        
        The three sample prices use the budget's low, middle and high points. An optional
        selected product keeps an unknown price. Fresh objects prevent one test or request
        from modifying another request's fixture."""
        source = Source(id="demo", url="https://example.com/fictional-comparison",
                        title="Fictional demonstration data", excerpt="All products, prices and features in this demo are synthetic.",
                        observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        # Local helper: fill the same sample fields for each named demonstration product.
        def product(name, price):
            return Product(name=name, price=price, currency=request["currency"],
                price_notes="Fictional demo price; not a real offer." if price else "Published price unknown. Budget is a user input.",
                availability="Fictional regional availability", performance="Illustrative performance; not verified",
                features="Sample feature set", warranty_support="Unknown", drawbacks="No real reviews collected",
                source_ids=["demo"])
        # Place sample competitors at the bottom, middle and top of the entered budget.
        low, high = request["price_min"], request["price_max"]
        return Comparison(selected_product=product(request["product"], None) if request.get("product") else None,
            competitors=[product(f"Example product {letter}", round(low + (high-low)*fraction, 2))
                         for letter, fraction in zip("ABC", [0, .5, 1])],
            verdict="This fictional example demonstrates the comparison and exports. It cannot establish the selected product's competitive position.",
            limitations=["Synthetic demonstration: no live research has occurred."]), [source.model_dump(mode="json")]

    # Return candidate names and evidence in the same shape as the live discovery stage.
    def discover(self, request):
        comparison, sources = self.fixture(request)
        return Shortlist(names=[p.name for p in comparison.competitors]), sources

    # Look up the exact assigned names in the fictional data, preserving assignment order.
    def research(self, request, names, seed_sources):
        comparison, sources = self.fixture(request)
        profiles = {p.name: p for p in [comparison.selected_product, *comparison.competitors] if p is not None}
        return [profiles[name] for name in names], sources

    def summarize(self, request, selected_product, competitors, sources):
        """Return fictional assessments only for the profiles the coordinator supplied.
        
        This respects earlier filtering, so excluded competitors are not reintroduced by
        the demonstration's comparison step."""
        comparison, _ = self.fixture(request)
        products = [p for p in [selected_product, *competitors] if p is not None]
        return Summary(verdict="Fictional demonstration of the market landscape; these findings do not describe real products or prove demand.",
            limitations=comparison.limitations,
            assessments=[ProductAssessment(name=p.name,
                positives=["Illustrative feature set within the entered market scope (fictional)."],
                negatives=["Real-world reliability and service coverage are not verified."],
                improvements=["Hypothesis: investigate service access and validate buyer priorities before building."],
                source_ids=p.source_ids) for p in products],
            opportunities=[Opportunity(dimension="Service and support", observation="The fictional profiles leave warranty and support unknown.",
                suggestion="Hypothesis: test whether transparent warranty terms and accessible servicing would influence buyers.",
                source_ids=["demo"])] if products else [])
