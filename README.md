# Market explorer

Explore a product category in a region and price range, with no existing product
required. An AI orchestrator directs specialists to discover up to three relevant
products, research them, and assess positives, negatives and improvement opportunities.
Read the results as slides and export an editable PowerPoint presentation.

If you are learning the code, start with the
[guided walkthrough](#guided-walkthrough-follow-one-query-through-the-code), then
[read a request trace alongside it](#read-a-request-trace-alongside-the-code).

## Start

Requires Python 3.12 and uv. From this folder:

```powershell
uv sync --locked
Copy-Item .env.example .env  # Only if .env does not already exist
.\run.ps1
```

For live research, set `OPENAI_API_KEY` and `TAVILY_API_KEY` in `.env` and restart.
Set `OPENAI_MODEL=gpt-4o-mini` in that same file. It selects the primary model for
screening, orchestration and specialists. Optional Nebius fallback is configured
separately below; model names are never hardcoded in Python.
Changing it requires restarting the app. The browser interface runs live research only.
Existing shell environment variables take precedence. The app normally runs at
http://localhost:8501. Live research sends the submitted context to OpenAI and
search queries to Tavily and consumes API credits. When routing is enabled, failed
OpenAI model calls may send the same conversation to your configured Nebius model.

## OpenAI primary with Nebius fallback

Every live AI role uses the same router: request screening, coordination, discovery,
product research and comparison. It tries the model selected by `OPENAI_MODEL`
first. When OpenAI has an eligible availability failure, it retries once, then
attempts the configured Nebius model once. It does not execute or repeat Tavily
searches, tool actions, or an entire analysis. Each later model call starts with
OpenAI again; fallback is not a permanent provider switch.

```dotenv
OPENAI_MODEL=gpt-4o-mini
MODEL_ROUTING_ENABLED=true
NEBIUS_API_KEY=your-private-nebius-key
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
NEBIUS_MODEL=Qwen/Qwen3-30B-A3B-Instruct-2507
```

Use the exact case-sensitive API ID from your Nebius catalogue or code example,
not the display name. The Qwen ID above was verified for public tool calling and
structured output during integration. Changing accounts, access or models requires
rechecking compatibility. Model names and credentials remain environment settings.
`MODEL_ROUTING_ENABLED=false` keeps OpenAI-only behavior and does not require
Nebius settings. `.env.example` leaves routing disabled until keys are supplied.
Your existing OpenAI and Tavily keys remain required for live mode.

Eligible failures include connection errors, timeouts, temporary rate limits and
selected server/unavailability errors. Authentication, permission, billing/quota,
invalid requests, context/schema validation failures and safety refusals do not
trigger provider switching. A valid safety decline is final. If both providers
fail, the app reports a safe error; if screening cannot complete, research stays
blocked. The configured Nebius endpoint must be an approved HTTPS Token Factory
host ending in `/v1`, so its key is not sent to an arbitrary URL.

Provider SDK retries are disabled. The router owns a fixed budget of at most two
OpenAI attempts and one Nebius attempt, each with a 60-second request timeout, plus
a short retry delay. This is a per-model-call allowance, not a research deadline;
multiple failed calls can make a run slower and provider work may incur charges.
Never interpret an interrupted response as proof that the provider did no work.

The fallback preserves the conversation and binds the same permitted tools and
response schemas. Because endpoint features differ, Nebius receives JSON-schema
requests with local Pydantic validation, and required tool selection is adapted to
named-tool or automatic selection. The host rejects a text-only result where a
tool action was required. All normal tool, source-reference and budget checks still
apply. No memory layer or extra web-search provider has been added.

Langfuse records separate generation observations for the attempted providers,
along with `model_attempt_started`, `model_attempt_completed`,
`model_attempt_failed` and `model_fallback` events. Provider, configured model,
attempt number and fixed fallback reason codes are visible; prompts, raw errors,
credentials and research content stay redacted. Model cost estimates depend on
Langfuse recognising the selected model; unknown model pricing needs configuration
in Langfuse and should not be interpreted as zero cost.

Stop Streamlit with Ctrl+C, then restart `./run.ps1` after changing `.env` or code.
Refreshing the browser alone may retain an older imported workflow.

## Explore or compare

### Request safety check before research

The live execution path is now:

`Streamlit submission → input validation → AI safety decision → AI orchestrator → specialists → slides/PowerPoint`

`market_analysis/request_screening.py` is the gatekeeper. After the application
validates the form, a separate conversation with the model configured in `OPENAI_MODEL`
reads every submitted research field. It looks for instructions aimed at overriding
the application's rules, extracting private information, or misusing research tools.
The classifier receives fixed rules separately from the submitted text and has no
tools or credentials in its messages. Ordinary market preferences and research about
security products are not automatically attacks merely because of their vocabulary.

The answer must be a validated `allow` or `decline` decision with a predefined reason
code. Only explicit approval starts the orchestrator. Rejection or uncertainty shows
a polite request to rephrase, without starting research or generating a presentation.
A timeout, provider refusal, missing answer or malformed answer also stops research
and asks the user to retry. The gate is inside `compare`, so calling the workflow
directly uses the same check as the browser.

Live screening adds one logical model invocation per submitted valid request, subject
to the configured provider retry policy. Traces record the screening stage, outcome
and predefined reason code, not the submitted text or a model-generated explanation.
Offline demonstration simulates approval without an AI call; it does not test actual
attack detection. Automated tests use controlled decisions to verify that declined
and failed checks cannot reach the coordinator.

An AI classifier can miss attacks or reject legitimate requests. This check screens
the initial submission; it does not screen web pages retrieved later. Existing rules
for treating retrieved content as untrusted and validating tool actions still apply.

1. Enter a **category**, **region**, **minimum/maximum price** and **currency**.
2. Optionally describe a product idea, intended buyer or use case.
3. If comparing a product, check **I have an existing product**. This immediately
   opens the product name, website and detail fields. Only the name is required;
   website and product details are optional. Unchecking it excludes those fields
   from the request, even if previously filled in.
4. Click **Explore market**.
5. Navigate the presentation using the **Previous** and **Next** arrow buttons. The indicator shows your current slide and total. Click **Prepare PowerPoint**,
   then **Download PowerPoint**. Export is a local operation, with no AI calls.
6. Use **Clear analysis** to discard the current result and prepared download.

The launch interface has no demonstration toggle, raw research/source viewer,
trace viewer or log controls. Logs and traces remain operational services behind
the application. Streamlit's `client.showErrorDetails="none"` also hides unexpected
exception messages and stack traces from the browser. The electric bicycle example button fills ordinary input fields;
it does not select fictional research or start provider calls.

The three competitors are selected for relevance, not verified best-selling status.
Missing facts remain Unknown. Ideas and supplied product notes are user assumptions,
not verified product facts.

## AI architecture

```text
Streamlit market brief + optional existing product
  -> Validate input contracts
  -> AI request safety decision (decline or unavailable: stop here)
  -> AI orchestrator conversation
       -> discover_market: competitor-discovery specialist
       <- candidate names and retrieved evidence
       -> research_products: one research conversation for ALL assigned products
       <- validated profiles, price/currency filtering and explicit limitations
       -> optional second discovery / research pass, chosen by the AI
       -> assess_market: opportunity and comparison specialist
       <- positives, negatives, selection basis and proposed improvements
       -> finish_report, chosen by the AI after reviewing findings
  -> One saved result
       -> Slide preview
       -> Editable PPTX: at most six slides, with no source appendix or source notes
```

The coordinator is a tool-calling AI agent, not a fixed Python sequence. It receives
specialist observations, chooses the next action and supplies each assignment's
focus. `workflow_orchestrator.py` hosts its tools and enforces prerequisites,
validation and cost limits. Python remains necessary to execute tools and validate
results; it does not choose the next specialist action.

Discovery finds up to three rivals. Product research handles three profiles in
exploration mode or up to four when a selected product is supplied. Evidence and
retrieval counts are kept separately for each product. URLs must first be discovered
for the same product before a page can be fetched. Failed pages are recorded as gaps.
Research returns one combined structured response; it does not spawn per-product agents.

Competitors need a sourced price in the requested currency and budget to qualify.
The selected product, if present, can retain an unknown price. Missing/extra product
identities are rejected. Invalid source references remove unsupported facts. Assessments
must cover exactly the supplied profiles, and their references must belong to those
profiles. Opportunity references must point to retrieved evidence. These checks do
not independently prove every model-generated assertion.

The assessment distinguishes evidence-based observations from suggested improvements.
Opportunities are hypotheses for validation with customers, not proof of demand,
sales, market share or market-entry success. Fewer than three qualifying products
produces an explicit incomplete report, rather than fabricated competitors.

### Call limits

The orchestrator has at most **10 decisions**, and each specialist tool can execute
at most **twice**. Per discovery pass: at most 3 searches, 4 model decision turns and
1 structured response. Per product research pass: at most 3 searches and 2 page
fetches per product, up to 22 model decisions for four products and 1 structured
response. Assessment uses 1 structured model call per pass.

With an existing product and every allowance exhausted, the upper bound is
**30 searches, 16 page fetches and 69 model calls** (including screening), before provider retries
(at most two OpenAI attempts and one Nebius attempt per call when routing is enabled).
That gives at most 207 provider attempts under those research allowances. Agents can
finish much earlier. Export uses no AI calls.

## PowerPoint runtime

The application uses `@oai/artifact-tool` to create editable text shapes in a 16:9
PowerPoint deck. It requires **Node.js** and the **Artifact Tool runtime** in addition
to the Python dependencies. Both are already available in this Codex environment.
The exporter automatically checks the bundled runtime at:

```text
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/
  node_modules/@oai/artifact-tool/dist/artifact_tool.mjs
```

For another provisioned installation, set the absolute runtime module path and,
if Node is not on PATH, its executable path:

```powershell
$env:ARTIFACT_TOOL_MODULE = 'C:\path\to\artifact-tool\dist\artifact_tool.mjs'
$env:MARKET_NODE_EXECUTABLE = 'C:\path\to\node.exe'
```

Artifact Tool is a provisioned runtime package, not a dependency installed by
`uv sync`. Copying only this project to a computer without that runtime will allow
research but will not enable PowerPoint export until the runtime is provisioned.
An export failure leaves the completed research and slide preview intact and can
be retried without repeating research.

Presentations contain at most six slides in a fixed order:

1. Introduction: category, region, budget and optional existing product or idea.
2. Comparison and opportunities, including key limitations.
3. One slide per product: overview, top three positives, top three negatives or gaps, and corresponding suggested improvements. Points are ordered by relevance to the brief. If evidence supports fewer than three, the report provides fewer and identifies uncertainty instead of inventing findings.

Exploration with three competitors produces five slides. Comparing an existing
product against three competitors produces six. Longer findings are condensed to
brief excerpts, with ellipses where needed, rather than adding continuation pages.
Full findings remain in the saved session result for presentation building; raw
research records and source excerpts are not displayed in the launch interface.

Source evidence, URLs, source appendices and source speaker notes are excluded from
PowerPoint entirely. Research details and source excerpts are also absent from the
launch interface. The app discards previously prepared decks after a
presentation-layout update, so an open session cannot download the old long format.

Research results and prepared PPTX bytes remain in the current Streamlit session;
refreshing or restarting can lose them. No research reports are automatically saved.
Generated reports, previews and development validation files are excluded from
version control. PowerPoint is the supported report export.

## Tracing with Langfuse Cloud

Live requests use **Langfuse Cloud as the primary tracing destination**. They no
longer create a JSONL file for every query. Offline demonstrations still use local
`logs/<request-id>.jsonl` files and never contact Langfuse. Existing historical logs
are left untouched; they are not uploaded or migrated automatically.

Add your project configuration to `.env`, then stop and restart Streamlit:

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-your-project-key
LANGFUSE_SECRET_KEY=sk-lf-your-project-secret
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_TRACING_ENVIRONMENT=development
```

Use your project's regional base URL: EU `https://cloud.langfuse.com`, US
`https://us.cloud.langfuse.com`, or Japan `https://jp.cloud.langfuse.com`. These are
project credentials, not your OpenAI credentials. Keep real values in the ignored
`.env`; `.env.example` contains blank credential fields. The SDK dependency is pinned
in `pyproject.toml` and `uv.lock`. Run `uv sync --locked` after updating the code.
Set `LANGFUSE_TRACING_ENABLED=false` to disable live cloud tracing explicitly.
Missing or invalid configuration is recorded as an operational warning and does not
prevent analysis. Restart is required after changing credentials or tracing settings.

### What appears in Langfuse

Each live submission gets one request ID, reused as its Langfuse trace ID. Nested
observations show screening, coordinator decisions, discovery, product research,
search/page retrieval, assessment and completion. Validation rejections appear even
if no agent runs. Later PowerPoint preparation creates another operation with the
same trace ID, so research and export are correlated without reading a local file.

Model calls appear as generation observations, including safety classification and
structured responses. They carry the configured model name, actual observed latency,
and input/output/total token counts when supplied by the provider. Missing counts
remain absent. Langfuse can estimate model cost from its model-pricing catalogue;
this is not a provider invoice, and Tavily charges are not included. No fixed model
prices are hardcoded in this application.

| Observation or event | Meaning |
|---|---|
| `Request safety screening` / `request_screened` | Screening stage, allow/decline/blocked outcome and predefined reason code |
| `AI orchestrator / delegation` / `orchestrator_decision` | Model calls and selected actions with counts and prerequisite flags |
| Research, search and page-fetch steps | Nested durations, retrieval outcomes and source counts |
| `competitor_accepted` / `competitor_excluded` | Fixed price/currency/citation eligibility reason; private facts are redacted |
| `action_blocked` | A prerequisite or call allowance prevented an action |
| `step_failed` / `request_failed` | Safe error types, stages and code locations; no exception messages |
| `request_rejected` / `request_finished` | Final request outcome and elapsed time |
| `PowerPoint export` / `export_completed` | Export status and output byte count, connected to the original trace ID |

Open the project's Tracing view and locate the request by its trace ID or time.
Refresh after a few seconds: observations are exported in background batches. Request
text and diagnostic panels are not displayed in the Streamlit browser.

### Privacy and local operational logs

Only operational metadata leaves the application. Submitted briefs, product names,
prices, queries, URLs, page excerpts, prompts, model responses and model-written
rationales are redacted. Application-defined reason codes remain visible. The
integration uses a metadata-only LangChain callback rather than an automatic
callback that could collect full prompts and responses. It uses a separate tracing
provider and ends spans manually so raw exception messages are not captured by
SDK exception instrumentation. The shared privacy filter is applied before export,
with another SDK metadata mask as defense in depth.

Langfuse connectivity/configuration failures and offline log-write failures go to
`logs/operations.log`, rotated at approximately 1 MB with at most three backups.
Only fixed warning codes and generated request IDs are written, never raw SDK
error bodies. Set `MARKET_LOG_DIR` to change this directory. If even operational
logging cannot write, the application emits a fixed safe warning to the server
console. Logging failures do not block research or turn a successful run into an error.
The request safety gate still blocks rejected or unavailable safety decisions.

There is no local duplicate of live traces and no disk backlog for Cloud outages.
An outage, failed delivery, abrupt shutdown or disabled tracing can therefore leave
a gap in the detailed execution record. Background SDK retries are not a durable
storage guarantee. Historical JSONL files remain available for manual archive/deletion.
Demo JSONL files are retained until removed; only `operations.log` rotates automatically.

The privacy policy can still be applied to old JSONL files while queries are idle:

```powershell
.venv\Scripts\python.exe -B -c "from market_analysis.trace_logging import sanitize_existing_logs; print('Sanitized log files:', sanitize_existing_logs())"
```

Logs and credentials are ignored by Git. Restrict local folder access and Langfuse
project access appropriately; metadata still reveals operational activity and timing.

## Code guide

| File | Responsibility |
|---|---|
| `streamlit_research_app.py` | Market brief, conditional product fields, saved results, slide navigation and PowerPoint download |
| `market_analysis/workflow_orchestrator.py` | AI coordinator instructions, tool execution, evidence checks, filtering and call limits |
| `market_analysis/request_screening.py` | Checks the submitted request before the coordinator; stops on rejection or an unavailable safety decision |
| `market_analysis/react_specialist_agents.py` | Discovery, shared product research and opportunity/comparison agents |
| `market_analysis/live_research_services.py` | Routed model and Tavily search/page adapters |
| `market_analysis/model_router.py` | OpenAI-first availability retries, Nebius fallback and provider-specific bindings |
| `tests/support/fake_research_services.py` | Fictional fixtures and a clearly labelled scripted coordinator |
| `market_analysis/research_schemas.py` | Validated requests, optional selected product, facts, assessments and opportunities |
| `market_analysis/research_errors.py` | Safe error messages and diagnostics without raw provider payloads |
| `market_analysis/trace_logging.py` | Request IDs, private event routing, offline JSONL and historical sanitization |
| `market_analysis/cloud_tracing.py` | Langfuse observations, model usage and rotating operational warnings |
| `market_analysis/presentation_export.py` | Shared slide content, escaped HTML preview and the Node export bridge |
| `market_analysis/powerpoint_renderer.mjs` | Editable PowerPoint text shapes using Artifact Tool |
| `market_analysis/__init__.py` | Python package marker |

Comments explain the key steps for readers unfamiliar with Python.

### Reading the code without a Python background

Start with `streamlit_research_app.py`, then follow its call to `compare` in
`workflow_orchestrator.py`. The orchestrator exposes specialist actions implemented
in `react_specialist_agents.py`. `live_research_services.py` connects those agents
to providers; `tests/support/fake_research_services.py` supplies fictional replacements.
Finally, `presentation_export.py` turns the saved result into slides, and
`powerpoint_renderer.mjs` writes the PowerPoint file.

- A `def` defines a reusable operation; indentation identifies the lines it contains.
- A `class` groups related data or operations. Pydantic's `BaseModel` classes define
  data contracts and validate incoming values.
- A dictionary such as `{"name": "Example"}` stores named values; a list stores
  ordered items. A comprehension builds a list or dictionary by processing items.
- `None` means no value is known or supplied. It is different from zero or an empty list.
- `model_validate` checks data against a contract; `model_dump` converts a checked
  object into ordinary values that can be displayed or saved as JSON.
- `@tool` makes a Python function available as an action an AI agent can request.
  The host executes the action and returns its result to the AI conversation.
- `lambda` creates a small unnamed function, often to let `run_step` execute an
  operation with consistent error handling.
- `request | {...}` copies a dictionary and adds or replaces named values.
  `**values` passes a dictionary's entries as named function arguments.
- `try` / `except` handles failures; `with` manages a temporary context, such as a
  screen section or an automatically cleaned-up export folder.
- Tests use `Mock` and `patch` to replace outside services with controlled responses.
  Assertions describe the behavior that must remain true.

Local `.env` credentials and the `.venv` dependency environment are ignored by Git.
Keep them on your machine for running the app; share `.env.example` and `uv.lock`
instead. Do not manually edit generated dependency files such as `uv.lock`.

## Guided walkthrough: follow one query through the code

You do not need to understand every Python expression before following the program.
Start with the named functions and the records passed between them. The following
walkthrough uses a market-exploration request for electric bicycles in India with
an INR 30,000–50,000 budget. Those values are example inputs, not verified facts
about any product.

### 1. Start the application

Open `run.ps1`. It changes to the project directory, creates `.env` from the blank
example if necessary, and launches `streamlit_research_app.py` through uv. The
launcher does not perform research. `pyproject.toml` declares dependencies, while
`uv.lock` records their resolved versions so another installation can reproduce
the environment. `.streamlit/config.toml` controls the browser theme and local
server address; `.vscode/settings.json` supplies an optional editor preference.

The direct launch command performs the same application startup:

```powershell
uv run --locked --env-file .env python -m streamlit run streamlit_research_app.py
```

### 2. Read the screen code and identify the research boundary

Open `streamlit_research_app.py`. Its first section initializes session state and
builds the sidebar and form controls. Session state is a per-browser-session store:
it keeps the result available while Streamlit executes the screen code again after
a widget interaction. It is not a database or permanent report archive.

The important boundary is `if submitted:`. A click on **Explore market** makes that
condition true. The code clears the previous result, starts a request trace, checks
the entered values and creates `LiveServices`. Test helpers are not selected by the application. It then
calls `compare(...)`. Changing a slide does not enter this
branch, so those actions do not repeat paid research.

When the existing-product checkbox is off, product fields are deliberately omitted
from the request. Previously entered values may remain in session state so they
can be restored when the checkbox is re-enabled, but those hidden values are not
sent as the selected product in exploration mode.

### 3. Understand the shared data contracts

Open `research_schemas.py`. A schema is the agreed shape of a record passed from
one part of the program to another. Pydantic turns those definitions into checks
and gives the AI a structured response format.

| Record | What it carries | Where it is used |
|---|---|---|
| `ResearchRequest` | Category, market, budget, currency and optional product/context | Form validation and every specialist assignment |
| `Source` | Evidence ID, URL, title, excerpt, retrieval time and method | Internal research evidence and citation checks |
| `Shortlist` | Up to three candidate names | Discovery output |
| `ProductFacts` | Price, availability, performance, features, support, drawbacks and citations | Structured product-research output |
| `Product` | ProductFacts plus the assigned product name | Coordinator validation and report assembly |
| `ProductAssessment` | Positives, negatives, suggested improvements and citations for one name | Assessment specialist output |
| `Opportunity` | A dimension, observed gap, suggested change and citations | Market-level opportunities |
| `Summary` | Assessment specialist's verdict, assessments, opportunities and limitations | Checked before the report can finish |
| `Comparison` | Optional selected product, qualifying competitors and final assessment | Saved result and slide builder |

For example, `price=None` means that no price is known. It does not mean free, zero,
or the budget minimum. A source ID acts like a record reference: it connects a
product's statement to retrieved material without duplicating the full excerpt in
every record. A valid reference establishes that the evidence record exists; it
does not establish that the evidence supports every statement attributed to it.

### 4. Follow the coordinator's conversation

Open `workflow_orchestrator.py`. Read `ORCHESTRATOR_INSTRUCTIONS`, then `compare()`.
The instruction text describes the coordinator's job. `compare()` creates local
working state and four actions that the coordinator can request:

| Action | Work performed by the host | Observation returned to the AI |
|---|---|---|
| `discover_market` | Ask discovery for candidates, validate the response, remove duplicates and exclude the selected product | Candidate names and discovery evidence |
| `research_products` | Ask one researcher for all assigned profiles, validate identities/citations, filter competitor prices | Profiles that qualify and explicit limitations |
| `assess_market` | Ask for the verdict and opportunities, then check assessment names and permitted citation IDs | The validated assessment |
| `finish_report` | Require an assessment of the current research and mark the request finished | A completion acknowledgement |

The functions are declared inside `compare()` so they can share the state for this
one request. They are decorated with `@tool`, which exposes their names, parameters
and descriptions to the AI. Defining a function does not run it. Binding the tools
to a model also does not run them; the program first needs a model response asking
for a specific action.

Comments beginning with `#` are explanations for readers. Triple-quoted text at
the start of a module, class or function is called a docstring. Some docstrings
are also consumed by libraries: `@tool` uses a function's description in the AI's
tool definition, and schema descriptions can appear in structured-output contracts.
The large `SYSTEM` and `ORCHESTRATOR_INSTRUCTIONS` strings are direct model prompts.
Treat edits to AI-facing descriptions or prompts as behavior changes, rather than
assuming that every explanatory-looking string is harmless to change.

Find the `for turn in range(10)` loop. Each iteration follows the same pattern:

1. Send the current conversation and available actions to the model.
2. Read its selected action, assignment and short decision explanation.
3. Record that decision and the current findings in the request trace.
4. Execute the selected Python function, subject to its prerequisites and limits.
5. Wrap the returned observation in a `ToolMessage` and append it to the conversation.
6. Ask the model for its next action, or stop when it has successfully finished.

The model can request another research pass when it sees a gap. Python enforces
limits rather than automatically scheduling that pass. For example, an early
`finish_report` request is refused if the current assessment is missing. The model
receives that refusal as an observation and can choose another action.

`None` and an empty list have different meanings in the coordinator's state.
`eligible=None` means the current candidates have not been researched. `eligible=[]`
means research has completed but no competitor qualified. The second case can
produce an honest incomplete report instead of invented products.

### 5. Connect the coordinator to actual services

Open `live_research_services.py`. It creates the configured OpenAI client and Tavily
client. Its `discover`, `research` and `summarize` methods delegate to the specialist
implementations. Its `search` and `extract` methods perform the web operations that
specialists request. These adapters keep provider-specific details out of the UI.

All live roles use `model_router.py`. `OPENAI_MODEL` selects the primary client
and `NEBIUS_MODEL` selects the optional backup. Provider clients have separate keys
and callbacks. Agents do not share one conversation: each builds its own messages,
and the router preserves those messages when retrying that particular inference call.

The `sources()` helper discards unusable records, creates IDs from URL/content,
and bounds the retained text to 2,500 characters for searches or 8,000 characters
for extracted pages. These limits are in **characters**, not tokens. The same URL
may produce different IDs if the retrieved content differs.

### 6. Read the specialist implementations

Open `react_specialist_agents.py` in this order:

1. `SYSTEM`: shared research instructions sent to the model.
2. `ResearchAgent.run()` and `DISCOVERY_AGENT`: search for candidate names and submit
   a structured shortlist.
3. `ProductResearchAgent.run()`: research all assigned names in one conversation.
4. `ComparisonAgent.run()`: write the assessment from existing findings without
   receiving search or page-fetch tools.
5. `unknown_product()`: produce an explicit Unknown record when evidence references
   cannot be accepted.

The product researcher keeps three dictionaries indexed by product name: `pools`
for retrieved evidence, `counts` for search/fetch allowances, and `pages` for URLs
and retrieval outcomes. These structures separate each product's evidence even
though the AI reads the full assignment in one conversation.

Before fetching a page, the code requires its URL to be present in the evidence
already retrieved for that same product. After tool use, `create_model()` builds
an output contract with fields such as `product_0` and `product_1`. Each field allows
only that product's retrieved citation IDs. Python attaches the assigned name and
actual retrieval metadata to the returned facts.

If the combined response fails only citation validation, `submit()` returns `None`.
The current implementation then substitutes Unknown facts for the entire product
batch. Other response-validation failures stop the step. This distinction helps
explain a run that completes with fewer qualifying competitors than expected.

### 7. Follow the saved result into the presentation

After `compare()` returns, the UI saves a dictionary with these top-level entries:

| Entry | Meaning |
|---|---|
| `request` | The normalized submitted market brief |
| `comparison` | Products, verdict, assessments, opportunities and limitations |
| `sources` | Retrieved evidence records |
| `demo` | Whether this is a fictional demonstration |
| `activity` | Public assignments recorded during orchestration |
| `researched_at` | Report assembly time in UTC |

Open `presentation_export.py`. `build_slides()` reads that result without starting
another agent. It selects brief excerpts for five slides when three competitors are
present, or six when there is also an existing product. It can produce fewer slides
when fewer products qualify. `slide_html()` escapes the text before putting it into
the browser preview. Full findings and sources remain internal; the launch interface does not display
raw research records or evidence excerpts.

When **Prepare PowerPoint** is clicked, `powerpoint_export()` writes the shared
slide content into a temporary JSON file and launches `powerpoint_renderer.mjs`.
That JavaScript file positions editable text boxes and saves a PPTX. Python reads
the file's bytes before cleaning up the temporary directory. Streamlit stores those
bytes for the download button. Neither the renderer nor the exporter calls an AI.

### 8. Use developer fixtures without provider calls

The browser does not expose offline demonstration mode. Developer fixtures remain
available in `tests/support/fake_research_services.py` for automated tests and direct Python use.
`DemoOrchestratorModel.invoke()` returns scripted
requests for discovery, research, assessment and completion. `DemoServices` provides
fictional products through the same method signatures as the live adapter. This
makes it useful for stepping through validation, presentation and export locally.
It does not test how a live model chooses tools or how accurately it interprets
web evidence.

## Read a request trace alongside the code

Open `trace_logging.py` to connect log events with functions. The UI starts a
`request_trace(...)` context, which creates a request ID and selects Cloud for live
runs or a JSONL file for demos. Calls to `log_event(...)` look up that active context.
After live research, `resume_trace(request_id)` attaches export work to the same
Cloud trace ID. Developer fixture callers can explicitly pass `demo=True` for
local tracing and export correlation. Direct `compare()` callers should open a request
trace context first if they need observations. `cloud_tracing.py` converts stage
boundaries into nested observations and records model usage without message content.

For the historical log excerpt shared before metadata-only redaction was enabled
from request `baab5fe69d8b4cddae16edc81b90cfa0`, the
recorded execution can be read as follows:

| Event or phase | What happened in that recorded request |
|---|---|
| `request_started` | A live electric-bicycle market query was submitted without a selected product |
| Decision 1: `discover_market` | The coordinator requested candidate identification |
| `candidates_discovered` | Three names were returned; five discovery evidence records were available |
| Decision 2: `research_products` | The coordinator requested profiles for those names |
| Product tool calls | The researcher searched once and fetched one page for each of the three products |
| `competitor_accepted` | All three returned price values passed the implemented price/currency/reference gate |
| Decision 3: `assess_market` | The coordinator requested strengths, weaknesses and opportunities |
| Decision 4: `finish_report` | The current assessment was available, so completion was permitted |
| `request_finished` | The workflow reported completion after approximately 64.6 seconds |

Older excerpts showed the model's short explanation in `details.reason`. The
current logger redacts that free text. `known_state` now retains counts and flags
instead of names and research narratives. Those operational records make the action
sequence inspectable without copying the user's private context into the trace.

Several log details are easy to misinterpret:

- `step_started` and `step_completed` can be nested. A specialist's duration already
  includes its searches and model calls. Adding every duration together double-counts
  time. Use `request_finished.duration_ms` for the overall research duration.
- `source_count` counts evidence records, not necessarily unique websites or pages.
  Immediately after discovery, the aggregate report source count can still be zero:
  discovery evidence is held separately until research assembles the report sources.
- `eligible_count=0` after discovery does not mean every candidate was rejected.
  A completed filtering pass is visible through the subsequent research-step and
  competitor-acceptance/exclusion events. Private fields are redacted, not replayed.
- `token_usage={}` means the returned object did not expose usage metadata. It does
  not mean that a structured-response model call was free or used zero tokens.
- `step_completed` means a function returned successfully. `request_finished` with
  `completed` means the workflow finished. Neither event certifies factual accuracy.
- `page_fetch_result.kind` records the page type requested by the model. In the
  historical excerpt, third-party domains were labelled `official`; the code does not
  independently confirm manufacturer ownership. Treat that label as an unverified
  classification, not a validation result.

The excerpt demonstrates a completed live execution, but it also shows why output
review is necessary. The implemented price gate does not independently verify
category fit, exact variants, regional stock or whether every cited page supports
the associated claim. These are current implementation boundaries, not changes
made as part of this documentation update.

## How failures reach the user and the log

Open `research_errors.py`. A call such as
`run_step("Competitor discovery", operation)` executes `operation`, measures its
duration and labels failures. The `operation` is usually a small `lambda` that
defers a service call until `run_step` can wrap it consistently.

When an ordinary provider exception occurs, `run_step` raises a `ResearchFailure`
with an application-authored message and links the original exception as its cause.
`failure_details()` follows that chain to collect error types and code locations,
without recording raw provider bodies or submitted values. The browser receives a
short error message; the selected tracing destination contains safe diagnostic metadata.

A page-fetch failure can be recorded while the overall request still completes:
the product researcher catches that failure, returns an explicit gap and continues
with other products. An invalid required product identity or an invalid assessment
can instead stop the request. Read the event's stage and the final request outcome
together rather than treating every intermediate failure as a failed report.

## Use tests as worked examples

Each `test_...` function describes an expected behavior. Assertions such as
`assertEqual` compare the result with that expectation. A `Mock` replaces a real
service with a predictable answer; `patch` temporarily substitutes it at the place
the application imports or calls it. This lets a test reproduce failure conditions
without spending API credits or relying on live websites.

| Test file | Read it when you want an example of |
|---|---|
| `test_streamlit_research_app.py` | Live-only controls, hidden diagnostics, saved slides, export retries and old-session migration |
| `test_workflow_orchestrator.py` | Model-requested repeat research, premature completion, prerequisites and filtering |
| `test_live_research_services.py` | Tool selection, page retrieval and per-product citation contracts with mocked providers |
| `test_comparison.py` | Invalid inputs, unknown prices, currency/budget filtering and rejected evidence |
| `test_trace_logging.py` | Offline request correlation, isolation and credential masking |
| `test_cloud_tracing.py` | Cloud routing, nested observations, model usage, SDK masking and nonblocking failures |
| `test_model_router.py` | Availability-only fallback, retry limits, tool/schema preservation and credential separation |
| `test_research_errors.py` | Safe exception metadata and field-validation diagnostics |
| `test_presentation_export.py` | Source-free editable PowerPoint output and the six-slide maximum |

## Check

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use mocked providers and fictional fixtures: they cover optional-product
behavior, hidden-field exclusion, saved downloads, orchestrator-selected repeat
research, call limits, evidence handling, incomplete reports, safe errors and
editable PPTX contents. The PowerPoint integration test needs the runtime above.
Logging tests cover invalid submissions, failed requests, concurrent-user isolation,
credential redaction, decision explanations and export/request correlation.
Live research quality and provider interoperability still require a run with valid
credentials; tests do not consume paid API credits.
