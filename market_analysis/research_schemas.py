"""Data contracts shared by the form, research agents, coordinator and exporter.

Pydantic checks the structure, types and declared constraints of these records.
ResearchRequest describes user input; Source describes retrieved text; Product
describes researched facts; Summary and Comparison hold the assessment and final
report. Passing these contracts checks data shape, not whether a claim is true."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


def public_url(value: str) -> str:
    """Reject non-web and obvious local/private targets before retrieval."""
    parsed = urlsplit(str(value))
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise ValueError("Use a public HTTP(S) URL without credentials.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise ValueError("Use a public company website.")
    # A numeric IP address needs an extra check; ordinary domain names are not resolved here.
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Private and local addresses are not supported.")
    return str(value)


# The input contract: Pydantic checks these rules before any paid research begins.
class ResearchRequest(BaseModel):
    """Required market brief plus optional existing-product identity and user assumptions.

    Field sets validation rules. 'str | None' allows text or no value; list[str]
    means a list of text entries. These annotations describe accepted data types.
    """
    company_url: HttpUrl | None = None
    category: str = Field(min_length=2, max_length=160)
    product: str | None = Field(default=None, min_length=2, max_length=120)
    market: str = Field(min_length=2, max_length=120)
    price_min: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)
    price_max: float = Field(gt=0, le=10_000_000, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    founder_details: str = Field(default="", max_length=6000)
    exploration_context: str = Field(default="", max_length=6000)

    @field_validator("company_url", "product", mode="before")
    @classmethod
    def optional_text(cls, value):
        # Empty optional fields mean exploration, never an invented selected product.
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator("category", "product", "market", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("company_url")
    @classmethod
    def website(cls, value):
        if value is not None:
            public_url(str(value))
        return value

    @model_validator(mode="after")
    def price_order(self):
        if self.price_max < self.price_min:
            raise ValueError("Maximum price must be at least the minimum price.")
        return self


# One retrieved piece of evidence. Products refer to it by its id, like a record key.
class Source(BaseModel):
    id: str
    url: HttpUrl
    title: str
    excerpt: str
    observed_at: str
    retrieval_method: str = "search"


# Facts shared by all products. None means a price is unknown, not zero or the budget.
class ProductFacts(BaseModel):
    price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_notes: str
    availability: str
    performance: str
    features: str
    warranty_support: str
    drawbacks: str
    source_ids: list[str]
    product_page_url: str = ""
    retailer_page_url: str = ""
    website_notes: str = "Product-page retrieval not attempted."


# Product research attaches the assigned name in code; the coordinator rejects
# missing or substituted names when accepting the batch.
class Product(ProductFacts):
    name: str = Field(min_length=1)


# The report contains an optional selected product, up to three rivals and their assessment.
class Comparison(BaseModel):
    selected_product: Product | None = None
    competitors: list[Product] = Field(max_length=3)
    verdict: str
    limitations: list[str]
    assessments: list["ProductAssessment"] = Field(default_factory=list)
    opportunities: list["Opportunity"] = Field(default_factory=list)
    selection_basis: str = "Most relevant products for the requested category, region and budget; not a sales ranking."

    @model_validator(mode="after")
    def distinct_products(self):
        names = [p.name.strip().casefold() for p in [self.selected_product, *self.competitors] if p is not None]
        if len(names) != len(set(names)):
            raise ValueError("Each product must be distinct.")
        return self


# Discovery's structured answer contains candidate names. Its retrieved evidence is
# returned separately by the service rather than embedded in this record.
class Shortlist(BaseModel):
    names: list[str] = Field(max_length=3)


# Keep each product's assessment separate from the raw factual profile.
class ProductAssessment(BaseModel):
    """Evidence-based positives/negatives, plus explicitly suggested improvements."""
    name: str
    positives: list[str] = Field(min_length=1, max_length=4)
    negatives: list[str] = Field(min_length=1, max_length=4)
    improvements: list[str] = Field(min_length=1, max_length=4)
    source_ids: list[str]


class Opportunity(BaseModel):
    """Separate a researched observation from a proposed product opportunity."""
    dimension: str
    observation: str
    suggestion: str
    source_ids: list[str]


class Summary(BaseModel):
    """The writing specialist's response; the host checks names and evidence before accepting it."""
    verdict: str
    limitations: list[str]
    assessments: list[ProductAssessment] = Field(default_factory=list)
    opportunities: list[Opportunity] = Field(default_factory=list, max_length=6)
    selection_basis: str = "Most relevant products for the requested category, region and budget; not a sales ranking."


# Resolve the quoted class names in Comparison now that those classes have been defined.
Comparison.model_rebuild()

