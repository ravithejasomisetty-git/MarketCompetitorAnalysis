"""Convert an already completed analysis into browser slides and editable PowerPoint.

build_slides creates the shared content: introduction, comparison/opportunities,
then one slide per product, with a maximum of six. Long text is shortened for display;
the full analysis remains in the saved result. slide_html escapes text for the browser.
powerpoint_export passes temporary JSON to the JavaScript renderer and returns file
bytes for download. Source evidence is excluded from both slide content and PPT notes."""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import re
from html import escape
from pathlib import Path

# Change this when slide content/layout changes so open sessions discard older prepared decks.
PRESENTATION_VERSION = "executive-six-v1"


def build_slides(result):
    """Return the compact slide-content dictionaries shared by preview and export.
    
    The first two slides explain the brief and comparison/opportunities. Up to four
    product slides follow: the optional existing product plus three competitors. Each
    section selects a small number of points and shortens long text with an ellipsis.
    This is excerpting, not a new AI-generated summary; the input result is not changed."""
    request, comparison = result["request"], result["comparison"]
    label = "FICTIONAL DEMONSTRATION" if result["demo"] else "MARKET ANALYSIS"

    def concise(value, limit=160):
        """Shorten displayed text at word boundaries; full findings stay in the saved result."""
        # Generated URLs and citation markers also belong outside the presentation.
        value = re.sub(r"https?://\S+", "", str(value))
        value = re.sub(r"\[[^\]]*\]", "", value)
        for evidence in result["sources"]:
            value = re.sub(r"(?<!\w)" + re.escape(evidence["id"]) + r"(?!\w)", "", value)
        return textwrap.shorten(value, width=limit, placeholder="…") or "Unknown"

    def section(heading, values, count=2, limit=160):
        return {"heading": heading, "paragraphs": [concise(v, limit) for v in values[:count]] or ["Unknown"]}

    def slide(title, sections):
        return {"title": concise(title, 100), "label": label, "sections": sections}

    competitors = comparison["competitors"]
    slides = [slide("Introduction", [
        section("What this analysis is about", [f"{request['category']} in {request['market']}",
            request.get("exploration_context") or request.get("founder_details") or "Explore relevant competitors, strengths, gaps and improvement opportunities."]),
        section("Market and product scope", [f"Budget: {request['currency']} {request['price_min']:,.2f} – {request['price_max']:,.2f}",
            f"Existing product: {request.get('product') or 'None — market exploration'}", comparison["selection_basis"]], count=3, limit=150),
    ])]
    opportunities = [f"{o['dimension']}: {o['suggestion']}" for o in comparison["opportunities"]]
    caveats = comparison["limitations"][:1] + ["Improvement ideas are hypotheses to validate with buyers; this is not a sales ranking."]
    slides.append(slide("Comparison and opportunities", [
        section("Comparison", [comparison["verdict"]], limit=280),
        section("Relevant competitors", [f"{p['name']} — {p['currency']} {p['price']:,.2f}" for p in competitors]
                or ["No products verified in the requested budget."], count=3, limit=90),
        section("Improvement opportunities", opportunities or ["Insufficient evidence to propose an opportunity."], limit=140),
        section("Key limitations", caveats, limit=140),
    ]))
    assessments = {a["name"]: a for a in comparison["assessments"]}
    products = ([comparison["selected_product"]] if comparison["selected_product"] else []) + competitors
    # A valid report has one optional selected product and no more than three rivals.
    if len(products) > 4:
        raise ValueError("A six-slide presentation supports at most four products.")
    for product in products:
        price = f"{product['currency']} {product['price']:,.2f}" if product["price"] is not None else "Price unknown"
        assessment = assessments.get(product["name"], {})
        slides.append(slide(product["name"], [
            section("Product overview", [price + ". " + product["availability"], product["performance"] + ". " + product["features"]], limit=140),
            section("Positives", assessment.get("positives", ["Unknown"]), limit=140),
            section("Negatives / gaps", assessment.get("negatives", [product["drawbacks"]]), limit=140),
            section("Suggested improvements", assessment.get("improvements", ["More research needed."]), limit=140),
        ]))
    return slides


def slide_html(slide, number, total):
    """Escape all researched text before placing it into the slide preview."""
    sections = "".join('<section><h3>' + escape(s["heading"]) + '</h3>'
                       + ''.join('<p>' + escape(p) + '</p>' for p in s["paragraphs"]) + '</section>' for s in slide["sections"])
    return f'''<div class="market-slide" role="region" aria-label="Slide {number} of {total}">
    <style>.market-slide{{background:#f8fafc;color:#14283e;border:1px solid #d8e2ec;border-radius:12px;padding:clamp(20px,4vw,48px);min-height:420px;box-sizing:border-box;font-family:Arial,sans-serif}}
    .market-slide h2{{font-size:clamp(24px,3vw,36px);line-height:1.2;margin:14px 0 28px;color:#14283e}}
    .market-slide .slide-columns{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px 36px}}
    @media(max-width:600px){{.market-slide .slide-columns{{grid-template-columns:1fr}}}}
    .market-slide h3{{font-size:19px;color:#087e8b;margin:0 0 14px}}.market-slide p{{font-size:18px;line-height:1.5;overflow-wrap:anywhere}}
    .market-slide .slide-label,.market-slide footer{{font-size:12px;letter-spacing:1px;color:#52677c}}.market-slide footer{{margin-top:28px}}</style>
    <div class="slide-label">{escape(slide['label'])}</div><h2>{escape(slide['title'])}</h2>
    <div class="slide-columns">{sections}</div><footer>{number:02d} / {total:02d}</footer></div>'''


def powerpoint_export(result):
    """Return editable PPTX bytes generated locally from an existing analysis.
    
    Locate Node.js and the provisioned Artifact Tool module, write slide JSON into a
    temporary directory, and run powerpoint_renderer.mjs with separate path arguments.
    Read the resulting file before the temporary directory is cleaned up. Missing
    runtime dependencies, a failed process or a timeout raise an exception; the UI keeps
    the completed research available for another export attempt."""
    runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    # Allow other installations to supply runtime paths without changing application code.
    module = Path(os.environ.get("ARTIFACT_TOOL_MODULE", str(runtime / "node_modules/@oai/artifact-tool/dist/artifact_tool.mjs")))
    node = os.environ.get("MARKET_NODE_EXECUTABLE") or shutil.which("node")
    if not node or not module.is_file():
        raise RuntimeError("PowerPoint export needs Node.js and the Artifact Tool runtime. See README setup instructions.")
    with tempfile.TemporaryDirectory(prefix="market-pptx-") as folder:
        # The Python app passes slide data to the JavaScript renderer through a temporary JSON file.
        # Leaving this block deletes the temporary data and presentation, including on failure.
        directory = Path(folder)
        payload = directory / "slides.json"
        output = directory / "market-analysis.pptx"
        payload.write_text(json.dumps(build_slides(result), ensure_ascii=False), encoding="utf-8")
        completed = subprocess.run([node, str(Path(__file__).with_name("powerpoint_renderer.mjs")),
                                    str(module), str(payload), str(output)], capture_output=True, timeout=180)
        if completed.returncode or not output.is_file():
            raise RuntimeError("PowerPoint generation failed. Check the Node.js / Artifact Tool installation and try again.")
        # Return the file's bytes so Streamlit can offer a download without saving a report locally.
        return output.read_bytes()
