"""Check editable six-slide summaries, source exclusion and safe previews."""
import io
import copy
import unittest
import zipfile
from xml.etree import ElementTree
from market_analysis.workflow_orchestrator import compare
from support.fake_research_services import DemoServices
from market_analysis.presentation_export import build_slides, slide_html, powerpoint_export
from market_analysis.research_schemas import ProductAssessment
from pydantic import ValidationError


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build one fictional research result shared by the presentation checks."""
        cls.result = compare(dict(category="Electric bicycle", market="India", price_min=30000,
                                  price_max=50000, currency="INR"), DemoServices())

    def test_editable_pptx_has_product_summaries_without_source_evidence(self):
        result = copy.deepcopy(self.result)
        # Distinct third points expose accidental truncation in the downloaded deck.
        assessment = result["comparison"]["assessments"][0]
        for field in ("positives", "negatives", "improvements"):
            assessment[field] = [f"{field} priority {i}" for i in range(1, 4)]
        data = powerpoint_export(result)
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            paths = [n for n in package.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
            self.assertEqual(len(paths), len(build_slides(self.result)))
            self.assertEqual(len(paths), 5)
            texts = []
            for name in paths:
                tree = ElementTree.fromstring(package.read(name))
                texts.extend(n.text or "" for n in tree.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
                fonts = {n.attrib["typeface"] for n in tree.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}latin")}
                self.assertEqual(fonts, {"Arial"})
            content = " ".join(texts)
            for field in ("positives", "negatives", "improvements"):
                self.assertIn(f"{field} priority 3", content)
            for expected in ["FICTIONAL DEMONSTRATION", "Example product A", "Positives", "Negatives", "hypotheses", "Comparison and opportunities"]:
                self.assertIn(expected, content)
            # Editable text shapes are present, rather than screenshots of slides.
            self.assertGreater(len(texts), len(paths) * 3)
            # Inspect slide text AND notes/relationships so evidence cannot leak into hidden parts.
            xml = b" ".join(package.read(n) for n in package.namelist() if n.endswith((".xml", ".rels")))
            self.assertNotIn(b"fictional-comparison", xml)
            self.assertNotIn(b"Source evidence", xml)
            self.assertNotIn(b"Fictional demonstration data", xml)

    def test_three_priorities_reach_the_preview_without_adding_slides(self):
        """The preview must retain each third point and handle older four-point results."""
        result = copy.deepcopy(self.result)
        assessment = result["comparison"]["assessments"][0]
        for field in ("positives", "negatives", "improvements"):
            assessment[field] = [f"{field} priority {i}" for i in range(1, 5)]
        slides = build_slides(result)
        self.assertEqual(len(slides), 5)
        html = slide_html(slides[2], 3, 5)
        for field in ("positives", "negatives", "improvements"):
            self.assertIn(f"{field} priority 3", html)
            self.assertNotIn(f"{field} priority 4", html)

    def test_assessments_allow_limited_evidence_but_reject_four_priorities(self):
        """Enforce the limit without forcing the model to invent three findings."""
        data = dict(name="Example", source_ids=["demo"], positives=["Known strength"],
                    negatives=["Service coverage unknown"], improvements=["Validate service coverage"])
        self.assertEqual(len(ProductAssessment(**data).positives), 1)
        for field in ("positives", "negatives", "improvements"):
            with self.subTest(field=field):
                ProductAssessment(**{**data, field: ["First", "Second", "Third"]})
                with self.assertRaises(ValidationError):
                    ProductAssessment(**{**data, field: ["First", "Second", "Third", "Fourth"]})

    def test_long_content_and_many_sources_never_expand_the_deck(self):
        result = {**self.result, "comparison": dict(self.result["comparison"])}
        original = " ".join(f"finding-{i}" for i in range(500))
        result["comparison"]["verdict"] = original
        result["sources"] = self.result["sources"] * 100
        result["comparison"]["opportunities"] = self.result["comparison"]["opportunities"] * 6
        slides = build_slides(result)
        self.assertEqual(len(slides), 5)
        self.assertEqual(result["comparison"]["verdict"], original)
        self.assertEqual([s["title"] for s in slides[:2]], ["Introduction", "Comparison and opportunities"])
        self.assertEqual([s["title"] for s in slides[2:]], [p["name"] for p in result["comparison"]["competitors"]])
        self.assertTrue(all(len(s["sections"]) <= 4 for s in slides))
        self.assertTrue(all(len(section["paragraphs"]) <= 3 for s in slides for section in s["sections"]))
        self.assertTrue(all(len(p) <= 280 for s in slides for section in s["sections"] for p in section["paragraphs"]))

    def test_selected_product_and_three_competitors_use_exactly_six_slides(self):
        result = compare(dict(category="Electric bicycle", market="India", price_min=30000,
                              price_max=50000, currency="INR", product="My bicycle"), DemoServices())
        slides = build_slides(result)
        self.assertEqual(len(slides), 6)
        self.assertEqual(slides[2]["title"], "My bicycle")
        self.assertEqual([s["title"] for s in slides[3:]], [p["name"] for p in result["comparison"]["competitors"]])

    def test_inline_urls_and_citation_markers_are_not_exported(self):
        result = {**self.result, "comparison": {**self.result["comparison"],
                  "verdict": "A finding [demo] https://example.com/fictional-comparison"}}
        text = str(build_slides(result))
        self.assertNotIn("https://", text)
        self.assertNotIn("[demo]", text)
        self.assertNotIn("notes", build_slides(result)[0])

    def test_preview_escapes_untrusted_text(self):
        html = slide_html({"title": "<script>alert(1)</script>", "label": "Demo", "sections": []}, 1, 1)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_empty_market_report_can_still_be_presented(self):
        result = {**self.result, "comparison": {**self.result["comparison"], "competitors": [], "assessments": [], "opportunities": []}}
        slides = build_slides(result)
        self.assertEqual(len(slides), 2)
        self.assertEqual(slides[1]["title"], "Comparison and opportunities")
