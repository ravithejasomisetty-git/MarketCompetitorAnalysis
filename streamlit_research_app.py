"""Browser entry point: collect a market brief, run research, and show saved slides.

Read the form first, then the `if submitted` block to follow a new query. Streamlit
runs this file again after widget interactions; session_state keeps the previous
result and download alive. Only clicking Explore market starts research. Sources
are hidden until enabled in the sidebar, and detailed execution traces stay on disk."""
import os
import streamlit as st
from pydantic import ValidationError
from market_analysis.research_schemas import ResearchRequest
from market_analysis.workflow_orchestrator import compare
from market_analysis.research_errors import ResearchFailure, failure_details
from market_analysis.offline_demo_research import DemoServices
from market_analysis.presentation_export import build_slides, slide_html, powerpoint_export, PRESENTATION_VERSION
from market_analysis.trace_logging import request_trace, resume_trace, log_event

st.set_page_config(page_title="Market explorer", page_icon=":material/travel_explore:", layout="wide")
st.title("Explore your market")
st.write("Discover three relevant products, understand their strengths and gaps, and explore what you could improve.")
# Streamlit reruns this file after interactions. setdefault initializes saved session
# values only once, keeping research results and prepared downloads across those reruns.
for key, value in {"result": None, "research_error": None, "pptx": None, "export_error": None, "trace_id": None}.items():
    st.session_state.setdefault(key, value)
DEFAULT_INPUTS = {"f_category": "Electric bicycle", "f_market": "India", "f_min": 30000.0,
                  "f_max": 50000.0, "f_currency": "INR", "f_context": "", "f_product": "",
                  "f_url": "", "f_details": "", "has_product": False}
for key, value in DEFAULT_INPUTS.items():
    st.session_state.setdefault(key, value)


def example():
    """Fill only the market brief; a product is optional even in the sample."""
    st.session_state.update(DEFAULT_INPUTS)


def clear_result():
    """Discard the previous research and its download together so they cannot become mismatched."""
    for key in ["result", "research_error", "pptx", "export_error", "trace_id"]:
        st.session_state[key] = None


with st.sidebar:
    demo = st.toggle("Offline demonstration", key="demo_mode")
    st.caption("Fictional sample data and a scripted coordinator; no live AI calls." if demo
               else "Live research uses an AI orchestrator, OpenAI and Tavily.")
    show_sources = st.toggle("Show sources and research details", value=False, key="show_sources")
    st.button("Clear analysis", on_click=clear_result)

st.button("Use electric bicycle example", on_click=example)
with st.container(border=True):
    st.subheader("Define the market")
    a, b = st.columns([2, 1])
    category = a.text_input("Product category", key="f_category")
    market = b.text_input("Region", key="f_market")
    a, b, c = st.columns([2, 2, 1])
    low = a.number_input("Minimum price", min_value=1.0, key="f_min")
    high = b.number_input("Maximum price", min_value=1.0, key="f_max")
    currency = c.text_input("Currency", max_chars=3, key="f_currency")
    context = st.text_area("Product idea or intended use (optional)", key="f_context", max_chars=6000,
                           placeholder="For example: an electric bicycle for daily commuting, with easy servicing.")
    # Keep the checkbox outside a form: ticking it immediately reveals the fields.
    has_product = st.checkbox("I have an existing product", key="has_product")
    product = url = details = None
    if has_product:
        st.caption("Enter the product name. Its website and other details are optional; the agents can research them.")
        product = st.text_input("Existing product name", key="f_product", persist_state="session")
        url = st.text_input("Product website (optional)", key="f_url", persist_state="session")
        details = st.text_area("Existing product details (optional)", key="f_details", max_chars=6000, persist_state="session")
    st.caption("We select up to three relevant products for your region and budget. This is not a best-selling ranking.")
    submitted = st.button("Explore market", type="primary")

if submitted:
    # Input edits alone do not start paid research; only this button-triggered branch can.
    clear_result()
    try:
        # Log only the brief's identifying fields, not free-text concepts or product notes.
        with request_trace({"category": category, "market": market, "price_min": low,
                            "price_max": high, "currency": currency, "has_product": has_product,
                            "product": product if has_product else None,
                            "has_concept": bool(context.strip())}, demo=demo) as trace:
            st.session_state.trace_id = trace.request_id
            try:
                if has_product and not (product or "").strip():
                    trace.reject("existing_product_name_required")
                    st.error("Enter an existing product name, or uncheck 'I have an existing product' to explore the market.")
                else:
                    # Hidden fields never leak a previous product into exploration mode.
                    request = ResearchRequest(category=category, market=market, price_min=low, price_max=high,
                        currency=currency.strip().upper(), exploration_context=context,
                        product=product if has_product else None, company_url=url if has_product else None,
                        founder_details=(details or "") if has_product else "")
                    if not demo and not all(os.environ.get(key, "").strip() for key in ["OPENAI_API_KEY", "TAVILY_API_KEY"]):
                        trace.reject("missing_provider_credentials")
                        st.error("Add OPENAI_API_KEY and TAVILY_API_KEY to .env and restart, or use offline demonstration.")
                    else:
                        if demo:
                            services = DemoServices()
                        else:
                            from market_analysis.live_research_services import LiveServices
                            services = LiveServices(os.environ["OPENAI_API_KEY"], os.environ["TAVILY_API_KEY"])
                        # Show a normal progress label; detailed activity belongs only in server logs.
                        with st.status("Exploring the market...", expanded=False) as status:
                            result = compare(request.model_dump(mode="json"), services)
                            status.update(label="Market analysis ready", state="complete", expanded=False)
                        st.session_state.result = result
                        st.session_state.slide_index = 0
            except ValidationError as exc:
                trace.reject("invalid_input", diagnostics=failure_details(exc))
                for error in exc.errors(include_input=False):
                    st.error(f"{'.'.join(map(str, error['loc'])) or 'Budget'}: {error['msg']}")
            except Exception as exc:
                declined = isinstance(exc, ResearchFailure) and exc.code == "prompt_injection_detected"
                if declined:
                    trace.reject("prompt_injection_detected")
                else:
                    trace.fail(exc)
                message = exc.public_message if isinstance(exc, ResearchFailure) else "Research setup failed. Details were recorded in the server logs."
                st.session_state.research_error = {"message": message, "stage": failure_details(exc)["stage"], "declined": declined}
    except OSError:
        # Do not start unlogged research when the server cannot write its trace directory.
        st.error("Server logging is unavailable. Check write permissions for the configured log folder before retrying.")

if st.session_state.research_error:
    error = st.session_state.research_error
    if error.get("declined"):
        st.warning(error["message"])
    else:
        st.error(f"{error['stage']}: {error['message']}")

# Read saved results: changing slides and downloading never repeats research.
result = st.session_state.result
if result:
    if st.session_state.get("presentation_version") != PRESENTATION_VERSION:
        st.session_state.pptx = None
        st.session_state.slide_index = 0
        st.session_state.presentation_version = PRESENTATION_VERSION
    if result["demo"]:
        st.warning("FICTIONAL DEMONSTRATION — products, prices and findings are illustrative; no live research occurred.")
    st.subheader("Your market presentation")
    slides = build_slides(result)
    # Reset a stale selection after an application update reduces an existing deck's size.
    if st.session_state.get("slide_index", 0) >= len(slides):
        st.session_state.slide_index = 0
    index = st.selectbox("Slide", range(len(slides)), key="slide_index",
                         format_func=lambda i: f"{i + 1}. {slides[i]['title']}")
    st.html(slide_html(slides[index], index + 1, len(slides)))
    st.caption("Up to six summary slides: introduction, comparison and opportunities, then one per product. Use the sidebar toggle for full research; PowerPoint excludes source evidence.")
    if st.session_state.pptx is None:
        if st.button("Prepare PowerPoint", icon=":material/slideshow:"):
            try:
                # An export appends events to the original request file, even after UI reruns.
                with resume_trace(st.session_state.trace_id):
                    from market_analysis.research_errors import run_step
                    with st.spinner("Preparing editable slides..."):
                        st.session_state.pptx = run_step("PowerPoint export", lambda: powerpoint_export(result))
                    log_event("export_completed", bytes=len(st.session_state.pptx))
                st.session_state.export_error = None
            except Exception:
                st.session_state.export_error = "PowerPoint could not be prepared. Check the presentation runtime setup in README and try again. Your research is still available."
    if st.session_state.pptx:
        st.download_button("Download PowerPoint", st.session_state.pptx, "market-analysis.pptx",
                           "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    if st.session_state.export_error:
        st.error(st.session_state.export_error)
    if show_sources:
        st.subheader("Sources and research details")
        st.json(result["request"])
        st.json(result["comparison"])
        for source in result["sources"]:
            st.link_button(f"{source['id']} — {source['title']}", source["url"])
            st.caption(source["observed_at"])
            st.text(source["excerpt"])
