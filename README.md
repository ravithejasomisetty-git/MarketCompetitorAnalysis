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
Set `OPENAI_MODEL=gpt-4o-mini` in that same file. This single setting controls the
request safety classifier, orchestrator and all live specialists; there is no hardcoded model fallback.
Changing it requires restarting the app. Offline demonstration needs no model setting.
Existing shell environment variables take precedence. The app normally runs at
http://localhost:8501. Live research sends the submitted context to OpenAI and
search queries to Tavily and consumes API credits.

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
6. Sources and full research are hidden by default. Enable **Show sources and research
   details** in the left sidebar to inspect them; toggling this never repeats research.

To try without keys, enable **Offline demonstration**. It uses fictional product
fixtures and a scripted coordinator, explicitly labelled as a demonstration;
it does not run an AI orchestrator or establish any real market findings.
The example brief is electric bicycles in India, INR 30,000–50,000.

“Top three” means the most relevant products for the category, region, budget and
intended use; it does not mean verified best sellers. Missing facts remain Unknown.
An idea and supplied product notes are user assumptions, not verified product facts.

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
(one configured model retry). Agents can finish much earlier. Export uses no API calls.

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
3. One slide per product: overview, positives, negatives and suggested improvements.

Exploration with three competitors produces five slides. Comparing an existing
product against three competitors produces six. Longer findings are condensed to
brief excerpts, with ellipses where needed, rather than adding continuation pages.
Full findings remain in the saved result and are visible only when the sidebar
sources/research toggle is enabled.

Source evidence, URLs, source appendices and source speaker notes are excluded from
PowerPoint entirely. The sidebar toggle controls browser visibility only and never
adds evidence to an export. The app discards previously prepared decks after a
presentation-layout update, so an open session cannot download the old long format.

Research results and prepared PPTX bytes remain in the current Streamlit session;
refreshing or restarting can lose them. No research reports are automatically saved.
Generated reports, previews and development validation files are excluded from
version control. PowerPoint is the supported report export.

## Server-side traces

Every **Explore market** submission creates `logs/<request-id>.jsonl` on the server,
including invalid inputs, missing credentials and failed runs. Logs are written
immediately and survive clearing results or restarting Streamlit. UI reruns and slide
navigation do not create another request. Preparing a PowerPoint appends export events
to the original request's file.

The browser shows normal progress, results and short error messages. It does not
display the activity trace, stack locations or diagnostic panels.

Each JSON line contains a UTC timestamp, request ID, event name and event details:

| Event | What it tells you |
|---|---|
| `request_started` | Request ID and live/demo mode; the submitted brief is redacted |
| `orchestrator_decision` | Chosen action, candidate/eligible counts, assessment-ready flag and call counts; model-written explanations and assignments are redacted |
| `specialist_assignment` / `specialist_tool_selected` | Which specialist and action were called; queries, product names and assignments are omitted |
| `step_started` / `step_completed` | Stage boundaries, elapsed milliseconds and token counts when supplied by the provider |
| `web_search_result` / `page_fetch_result` | Source counts and retrieval outcomes; URLs and query text are redacted |
| `competitor_accepted` / `competitor_excluded` | The application's fixed inclusion/exclusion reason code, with product names, prices and currency details redacted |
| `action_blocked` | Missing prerequisites or exhausted specialist call allowances |
| `assessment_validated` / `analysis_completed` | Completion, source/product/limitation counts and specialist usage; research narratives are redacted |
| `step_failed` / `request_failed` | Safe error types, failing stages, field names and code locations |
| `request_rejected` / `request_finished` | Validation/configuration rejection or final completed/failed status and total duration |

The model still supplies a short explanation to the coordinator, but this text can
contain private research context and is no longer written to trace files. Follow
the selected actions, prerequisite flags, counts and fixed validation reason codes
to understand execution. Demo decisions remain scripted and labelled as such.

From the project folder, inspect or follow the newest trace using PowerShell:

```powershell
$trace = Get-ChildItem .\logs\*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $trace.FullName -Wait
```

To show just the orchestrator's decisions, omit `-Wait` and run:

```powershell
Get-Content -LiteralPath $trace.FullName | ConvertFrom-Json | Where-Object event -eq 'orchestrator_decision' | ForEach-Object { $_.details | ConvertTo-Json -Depth 8 }
```

Set an absolute `MARKET_LOG_DIR` in `.env` to change the location. The server account
must be able to write there; if logging cannot start, the app does not begin unlogged
research. Logs are ignored by Git and are not included in PowerPoint downloads.
Files are retained until you delete/archive them; no automatic expiration is applied.

Logging now uses a metadata-only policy. Briefs, product names, prices, queries,
assignments, model-written explanations, research narratives and URLs are omitted.
Unknown fields and arbitrary text are denied by default. Credential-shaped fields,
known credential values from the environment/local `.env`, and recognized token,
authorization, email and phone patterns are also masked. Full prompts, provider
error bodies, model responses and page excerpts must never be passed as log content.

Logs stay server-side and are ignored by Git. Metadata can still reveal operational
activity and timing, so access to the folder should remain limited to operators.
New folders/files use restrictive creation modes on platforms that support them;
Windows permissions are controlled by the parent folder's ACL. Redaction is defense
in depth, not permission to log arbitrary secrets.

To apply the same policy to older traces, while queries are idle, run:

```powershell
.venv\Scripts\python.exe -B -c "from market_analysis.trace_logging import sanitize_existing_logs; print('Sanitized log files:', sanitize_existing_logs())"
```

This rewrites traces atomically without an unsanitized backup. It preserves event
order, timestamps and request IDs. A file changed by an active request is left
untouched and the operation raises an error so it can be retried safely.

## Code guide

| File | Responsibility |
|---|---|
| `streamlit_research_app.py` | Market brief, conditional product fields, saved results, slide navigation and PowerPoint download |
| `market_analysis/workflow_orchestrator.py` | AI coordinator instructions, tool execution, evidence checks, filtering and call limits |
| `market_analysis/request_screening.py` | Checks the submitted request before the coordinator; stops on rejection or an unavailable safety decision |
| `market_analysis/react_specialist_agents.py` | Discovery, shared product research and opportunity/comparison agents |
| `market_analysis/live_research_services.py` | OpenAI model and Tavily search/page adapters |
| `market_analysis/offline_demo_research.py` | Fictional fixtures and a clearly labelled scripted coordinator |
| `market_analysis/research_schemas.py` | Validated requests, optional selected product, facts, assessments and opportunities |
| `market_analysis/research_errors.py` | Safe error messages and diagnostics without raw provider payloads |
| `market_analysis/trace_logging.py` | Request IDs, per-request server files, event redaction and session isolation |
| `market_analysis/presentation_export.py` | Shared slide content, escaped HTML preview and the Node export bridge |
| `market_analysis/powerpoint_renderer.mjs` | Editable PowerPoint text shapes using Artifact Tool |
| `market_analysis/__init__.py` | Python package marker |

Comments explain the key steps for readers unfamiliar with Python.

### Reading the code without a Python background

Start with `streamlit_research_app.py`, then follow its call to `compare` in
`workflow_orchestrator.py`. The orchestrator exposes specialist actions implemented
in `react_specialist_agents.py`. `live_research_services.py` connects those agents
to providers; `offline_demo_research.py` supplies fictional replacements.
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
the entered values and selects either `LiveServices` or `DemoServices`. It then
calls `compare(...)`. Changing a slide or the sources toggle does not enter this
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
| `Source` | Evidence ID, URL, title, excerpt, retrieval time and method | Research evidence and the optional source view |
| `Shortlist` | Up to three candidate names | Discovery output |
| `ProductFacts` | Price, availability, performance, features, support, drawbacks and citations | Structured product-research output |
| `Product` | ProductFacts plus the assigned product name | Coordinator validation and report assembly |
| `ProductAssessment` | Positives, negatives, suggested improvements and citations for one name | Assessment specialist output |
| `Opportunity` | A dimension, observed gap, suggested change and citations | Market-level opportunities |
| `Summary` | Assessment specialist's verdict, assessments, opportunities and limitations | Checked before the report can finish |
| `Comparison` | Optional selected product, qualifying competitors and final assessment | Saved result, slide builder and source/details view |

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

All live roles reuse one client selected by `OPENAI_MODEL` in `.env` (the example
configuration uses `gpt-4o-mini`). They do not share
one conversation: each agent builds its own message list, so the coordinator and
research specialists have different instructions and histories.

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
the browser preview. Full findings and sources are displayed separately only when
the user enables the sidebar toggle.

When **Prepare PowerPoint** is clicked, `powerpoint_export()` writes the shared
slide content into a temporary JSON file and launches `powerpoint_renderer.mjs`.
That JavaScript file positions editable text boxes and saves a PPTX. Python reads
the file's bytes before cleaning up the temporary directory. Streamlit stores those
bytes for the download button. Neither the renderer nor the exporter calls an AI.

### 8. Use demo mode to learn the flow without provider calls

Open `offline_demo_research.py`. `DemoOrchestratorModel.invoke()` returns scripted
requests for discovery, research, assessment and completion. `DemoServices` provides
fictional products through the same method signatures as the live adapter. This
makes it useful for stepping through validation, presentation and export locally.
It does not test how a live model chooses tools or how accurately it interprets
web evidence.

## Read a request trace alongside the code

Open `trace_logging.py` to connect log events with functions. The UI starts a
`request_trace(...)` context, which creates the file and request ID. Calls to
`log_event(...)` in nested components look up that active context automatically.
After research, `resume_trace(...)` lets an export append events to the same file.
Calling `compare()` directly outside a trace context does not create a trace file.

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
short error message; the request file contains the selected diagnostic metadata.

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
| `test_streamlit_research_app.py` | Checkbox behavior, hidden sources, saved slides, export retries and old-session migration |
| `test_workflow_orchestrator.py` | Model-requested repeat research, premature completion, prerequisites and filtering |
| `test_live_research_services.py` | Tool selection, page retrieval and per-product citation contracts with mocked providers |
| `test_comparison.py` | Invalid inputs, unknown prices, currency/budget filtering and rejected evidence |
| `test_trace_logging.py` | Request correlation, concurrent-user isolation and credential masking |
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
