"""Connect application-level research operations to OpenAI and Tavily.

The coordinator calls discover, research and summarize through this common service
interface. Specialists call search and extract to retrieve evidence. The same
configured model client is reused, but each agent constructs its own conversation.
Provider response shapes are converted here into the Source records used elsewhere."""
import hashlib
import os
from datetime import datetime, timezone
from langchain_openai import ChatOpenAI
from tavily import TavilyClient
from .research_schemas import Source, public_url
from .react_specialist_agents import DISCOVERY_AGENT, PRODUCT_RESEARCH_AGENT, ComparisonAgent
from .trace_logging import log_event, safe_url
from .research_errors import ResearchFailure
from .request_screening import classify_request

class LiveServices:
    demo = False

    def __init__(self, openai_key, tavily_key):
        """Create reusable OpenAI and Tavily clients for one submitted live analysis.
        
        Keys are supplied by the UI; OPENAI_MODEL selects the shared model from the environment.
        Client timeout and retry settings
        apply to provider calls; they are not an overall deadline for the full workflow."""
        # Require an explicit project-wide setting rather than silently using a code default.
        model_name = os.environ.get("OPENAI_MODEL", "").strip()
        if not model_name:
            raise ResearchFailure("missing_model", "Set OPENAI_MODEL in .env and restart the app.", "Provider configuration")
        self.llm = ChatOpenAI(model=model_name, api_key=openai_key,
                              temperature=0, timeout=60, max_retries=1)
        self.web = TavilyClient(api_key=tavily_key)
        log_event("providers_initialized", model=model_name, model_timeout_seconds=60, model_max_retries=1,
                  search_provider="Tavily")

    def screen_request(self, request):
        """Use the configured model in a separate conversation without tool access."""
        return classify_request(self.llm, request)

    def orchestrator_model(self):
        """Give the coordinator its own conversation with access to specialist tools."""
        return self.llm

    # Search returns evidence excerpts rather than treating search results as verified product facts.
    def search(self, query):
        result = self.web.search(query=query, max_results=5, search_depth="advanced", timeout=35)
        sources = self.sources(result.get("results", []), "search")
        log_event("web_search_result", query=query, source_count=len(sources),
                  sources=[{"id": s["id"], "url": safe_url(s["url"])} for s in sources])
        return sources

    # Read a discovered page after checking that its URL is a public web address.
    def extract(self, url):
        public_url(url)
        result = self.web.extract(urls=[url], extract_depth="advanced", format="markdown", timeout=30)
        return self.sources(result.get("results", []), "product_page")

    @staticmethod
    def sources(items, retrieval_method):
        """Convert usable provider results into evidence records.
        
        Skip non-public URLs and blank text. Hash the URL plus content to generate a stable
        source ID, retain a bounded excerpt, and timestamp retrieval in UTC. A successful
        retrieval records text availability; it does not certify factual accuracy."""
        sources = []
        for item in items:
            try:
                url = public_url(item.get("url", ""))
            except ValueError:
                continue
            content = item.get("raw_content") or item.get("content") or ""
            if content.strip():
                # Hash URL plus content for a repeatable evidence ID; cap excerpt size sent to the model.
                sources.append(Source(id="s-" + hashlib.sha256((url + content).encode()).hexdigest()[:12],
                    url=url, title=item.get("title") or url, excerpt=content[:8000 if retrieval_method == "product_page" else 2500], retrieval_method=retrieval_method,
                    observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds")).model_dump(mode="json"))
        return sources

    # The model chooses when to call these adapters; their order is not enforced here.
    def discover(self, request):
        return DISCOVERY_AGENT.run(self.llm, self, {"request": request})

    # names contains all competitors plus the selected product, if one was provided.
    def research(self, request, names, seed_sources):
        return PRODUCT_RESEARCH_AGENT.run(self.llm, self, request, names, seed_sources)

    # The assessment specialist receives facts and sources without search/fetch tools.
    def summarize(self, request, selected_product, competitors, sources):
        return ComparisonAgent().run(self.llm, {
            "request": request, "selected_product": selected_product.model_dump(mode="json") if selected_product else None,
            "competitors": [p.model_dump(mode="json") for p in competitors], "sources": sources})
