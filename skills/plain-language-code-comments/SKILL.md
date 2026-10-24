---
name: plain-language-code-comments
description: Write or revise source code and code examples with plain-language comments that help non-coders understand purpose, flow, inputs, outputs, and important decisions. Use whenever generating, editing, or refactoring code, including tests and scripts.
---

# Plain language code comments

Make new and changed code understandable to a reader who knows what the application
does but is unfamiliar with the programming language. Apply these instructions to
the requested changes; do not rewrite unrelated code just to add comments.

## Explain the purpose and flow

- Give a new source file a short introduction explaining its role and how it fits
  into the application. For an existing file, update the introduction when its role
  changes. Use the language's normal module comment or documentation convention.
- Explain the purpose of meaningful functions and classes. Mention inputs, returned
  results, state changes, or external actions when they are not already clear.
  A short helper may need only one sentence; an obvious one-line operation may
  need no extra explanation.
- Introduce a non-obvious block before the reader encounters it. Explain what the
  block accomplishes and why the chosen approach matters.
- Explain unfamiliar language features when they are necessary to understand the
  flow. For example, explain that a callback runs when a user clicks a button, or
  that a context manager performs cleanup when an operation finishes. Explain a
  repeated pattern once rather than at every occurrence.

## Make comments useful and accurate

- Use familiar English, short sentences, and concrete descriptions. Explain a
  necessary technical term in context; avoid assuming Python or framework knowledge.
- Prefer comments about purpose, decisions, constraints, and consequences over
  comments that merely translate syntax. Do not comment every line or add filler
  such as "increment counter" beside an obvious increment.
- Explain important boundaries: when a paid service is called, which state survives
  a rerun, why an error is retried or not retried, what validation establishes, and
  what remains unverified. Include only boundaries relevant to the changed code.
- Keep the explanation next to the code it describes. Remove or correct stale,
  misleading, redundant, and commented-out code in the touched area.
- Write clear identifiers so comments support readable code rather than compensate
  for unnecessarily confusing names. Preserve public interfaces unless the task
  authorizes changing them.

## Respect executable and data boundaries

- Use valid comment syntax for the target language. Do not put comments into strict
  JSON or other formats that prohibit them; use an existing documentation location
  or a brief accompanying explanation instead.
- Distinguish ordinary comments from AI prompts, tool descriptions, schemas, and
  docstrings consumed at runtime. Editing those strings can change behavior;
  preserve their contracts while improving explanations around them.
- Never include credentials, private research inputs, personal details, or raw
  provider responses in examples or comments. Describe limitations honestly;
  do not claim that a validation check proves more than it actually checks.
- Leave dependencies, generated artifacts, and third-party source files unchanged
  unless the user specifically requests changes to them.

## Review before delivery

Read the changed code as a newcomer: can the reader identify what starts the work,
what the main stages do, where data goes, and what is returned? Fill important gaps,
then remove comments that add no useful information. Confirm that comments match
the final implementation and preserve valid syntax and behavior. Use the checks
appropriate to the actual code change; comment-only changes do not require tests
that merely assert comment wording.

If the user requests code without comments or a strict output format, follow that
explicit request. This skill does not authorize unrelated changes or new actions.
