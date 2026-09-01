# Transcript Chatbot — Current Structure, New Requirements, and Implementation Plan

Prepared by: Rukmini
Date: 2026-08-31
Total effort: 43 working days (~9 weeks, one developer)

---

# PART A — WHAT WE HAVE TODAY

## A1. System components

| Component | What it does | Where |
|---|---|---|
| FastAPI app | HTTP API, port 8001 | `Backend/app/main.py` |
| React frontend | Login, chat, admin upload, requirements dashboard | `frontend/src/` |
| PostgreSQL | Source of truth for all records | tables listed in A2 |
| Qdrant | Vector search (meaning-based lookup) | two collections, see A3 |
| Redis + Celery | Background processing of uploaded transcripts | `Backend/app/worker/tasks.py` |
| OpenAI | Embeddings, extraction, comparison, answers | via `services/` |
| Cohere | Re-ranking of search results | `services/reranker.py` |

## A2. Database tables and their exact columns

**`transcripts`** — one row per uploaded call.
```
id, customer_id, filename, session_name, call_date, upload_date,
status, celery_task_id, processing_summary, file_hash, total_blocks, created_at
```
Written by: `routers/requirements.py` on upload, then updated by `worker/tasks.py`.

**`conversation_logs`** — one row per speaker block in a call.
```
id, transcript_id, customer_id, speaker, role, text, call_timestamp, created_at
```
`role` is either `client` or `team_member`. Written by `services/transcript_parser.py`.
**This table is where speaker role and timestamp already live.** Remember this for Part B.

**`requirements`** — one row per requirement. Holds the CURRENT rule only.
```
id, customer_id, category, sub_category, current_text, canonical_text,
status, created_at, updated_at
```
Written by `services/requirement_comparison.py`.

**`requirement_versions`** — one row per call in which a requirement came up. The history.
```
id, requirement_id, version_number, text, change_type, confirmed_by, proposed_by,
discussed_date, session, transcript_id, vector_id, created_at
```
`change_type` is `added`, `modified`, or `unchanged`. Written by
`services/requirement_comparison.py`. **Never updated, only inserted.**

**`customers`** — the project/client.
```
id, name, client_speaker_name, created_at
```
`client_speaker_name` is how the system knows which speaker is the client.

**Supporting tables:** `users` (login, bcrypt hash, role), `chat_sessions` (conversation history
as JSON), `query_logs`, `team_subscriptions`.

## A3. Vector store (Qdrant)

Two collections, both 1536-dimension cosine:

- **`conversations`** — one vector per speaker block. Each block is embedded together with the two
  blocks before and after it, so short lines like "yes, do that" still carry meaning.
- **`requirements`** — exactly one CURRENT vector per requirement. When a requirement is modified,
  the old vector is deleted and replaced. Full history stays in Postgres, not here.

Payloads carry `customer_id`, `transcript_id`, `session`, and a normalised `date_ymd` used for
date filtering.

## A4. The ingestion pipeline, step by step

Trigger: admin uploads a `.docx` at `POST /requirements/transcript`.

**Step 1 — API accepts and hands off** (`routers/requirements.py`)
- Rejects non-`.docx` files.
- Computes SHA-256 of the file; rejects with 409 if that transcript was already uploaded.
- Derives the call date from the **session name** (`05-08-26_Grooming` → 2026-05-08), pinned to
  noon UTC so the date never slips a day in IST. Logic lives in `utils/dates.py`.
- Saves the file, inserts the `transcripts` row with `status="processing"`, queues the Celery task,
  returns immediately. Frontend polls `GET /requirements/task/{id}`.

**Step 2 — Parse** (`services/transcript_parser.py`)
- A regex reads the Teams format `Speaker Name␣␣␣1:23` and produces
  `{speaker, timestamp, text}` per block.
- Skips preamble lines and very short noise utterances ("Okay", "Yeah").
- **Tags each block `role=client` or `role=team_member`** by comparing the speaker to
  `Customer.client_speaker_name`.

**Step 3 — Store the conversation**
- Inserts all blocks into `conversation_logs`.
- Embeds each block with a ±2 block context window, writes to the Qdrant `conversations`
  collection.

**Step 4 — Extract requirements** (`services/requirement_extraction.py`)
- Reads the WHOLE transcript in one context. No chunking.
- Pass 1: an LLM writes structured analyst notes in plain text.
- Pass 2: a cheaper model converts those notes into JSON rows
  (`category`, `sub_category`, `requirement_text`, `canonical_text`, `confirmed_by`, `proposed_by`).
- The completeness-review pass exists but is currently switched off (`run_review=False` in
  `worker/tasks.py`) to cut cost and avoid duplicate rows.

**Step 5 — Compare against history** (`services/requirement_comparison.py`)

For each extracted requirement, decide: added, modified, or unchanged.
- Embeds all requirements in one batch call.
- Searches Qdrant for the 5 nearest existing requirements for this customer.
- Runs an LLM decision, then three more guards:
  1. **Identity gate** — "are these the same specific requirement, or two different rules that
     share a topic?" Biased toward DIFFERENT.
  2. **Adversarial verify** — a second call tries to prove the change is only a rewording. It
     survives as a change only if that challenge fails.
  3. **Confidence gate** — a low-confidence change is downgraded to unchanged.
- **Claim guard** — one existing requirement can be matched by at most one new requirement per
  upload.
- Writes: new requirement row (if added), or updates `current_text` (if modified). **In all three
  cases it inserts a `requirement_versions` row.**
- Qdrant writes are deferred until all comparisons finish, so requirements from the same upload
  cannot match each other.

**Step 6 — Finish**
- Sets `transcripts.status = "processed"` with a summary of `{total_extracted, added, modified}`.
- Email notification code exists but is commented out.

## A5. The question-answering pipeline

`POST /api/chat/stream` → `services/chat_service.py`.

1. **Small talk** — "hi", "thanks" match a fixed phrase table and return a canned reply with no
   retrieval and no LLM call (`services/social_replies.py`).
2. **Understand the question** — one LLM call in `services/query_processor.py` returns a routing
   decision: which store to search, filters (speaker, session, date), whether it is a count/list
   question, a change question, a full-requirements request, or a reformat request.
3. **Route to a lane.** Six lanes exist. Critically, the ones that must be exact read Postgres
   directly rather than trusting search:
   - counts and "list all" → read the requirements table
   - "what changed" → read `requirement_versions`
   - "all requirements for topic X" → rank the whole table, then confirm membership
   - normal questions → vector search both collections, Cohere re-rank, generate
4. **Answer** streams back token by token with source citations.

## A6. What the frontend shows today

| Screen | File | Lines | Shows |
|---|---|---|---|
| Chat | `components/chat/ChatInterface.jsx` | 280 | streaming answers, sources, session history |
| Admin | `components/admin/AdminDashboard.jsx` | 612 | upload transcript, project settings, transcript list |
| Requirements | `components/requirements/RequirementsDashboard.jsx` | 151 | **flat list of current requirement text only** |

## A7. Existing API routes on the requirements router

```
POST   /requirements/transcript                     upload
GET    /requirements/task/{transcript_id}           processing status
GET    /requirements/customer/{customer_id}         current requirements list
GET    /requirements/customer/{customer_id}/export  JSON export
GET    /requirements/transcripts                    list uploaded calls
DELETE /requirements/transcript/{transcript_id}     cascade delete
GET    /requirements/customers                      list projects
POST   /requirements/customer                       create project
PUT    /requirements/customer/{customer_id}         update project
DELETE /requirements/customer/{customer_id}         delete project
```

**None of these ten routes returns version history.** This matters in Part B, item 1.

---

# PART B — SUMMARY OF THE SIX REQUIREMENTS

| # | Requirement | Already built | Effort | Risk |
|---|---|---|---|---|
| 1 | Requirement version tracking | 85% | 3 days | Low |
| 2 | Appending updates to existing requirements | 70% | 5 days | Medium |
| 3 | Client requirement change tracking | 60% | 4 days | Medium |
| 4 | BRD and FRD generation | 25% | 9 days | Medium-High |
| 5 | Azure Boards integration | 0% | 5 days | Low |
| 6 | Automatic MOM posting to Teams | 10% | 8 days | Medium |
| — | Foundation work (Part C) | — | 9 days | — |
| | **Total** | | **43 days** | |

Roughly 45% of the email is already in the ground, and it is the part that takes months to get
right. Items 5 and 6 look most impressive on a slide but are the cheapest engineering in the list.

The percentages measure how much of the underlying capability exists, not how close the feature is
to being demonstrable. Item 1 is 85% built but completely invisible — no screen or API reads it.
Item 5 is 0% built but is a well-understood REST integration.

---

# PART C — FOUNDATION WORK, DO THIS FIRST (9 days)

## C1. Repository and measurement baseline — 4 days

Two problems block everything else.

**The repository does not install cleanly.** `Backend/requirements.txt` line 38 has two packages
joined on one line (`httpx==0.26.0cohere==7.0.8`), and `celery`, `redis` and `python-docx` are
imported by the code but never declared.

**Extraction accuracy has never been measured.** Every other number in this document is capped by
it. If a requirement is not extracted from the call, it is absent from the version history, the
BRD, the Azure board and the minutes — and no later safeguard can recover it.

### How to implement

1. Fix `requirements.txt`; add the three missing packages. Delete dead code:
   `utils/chunker.py` and `utils/pdf_processor.py` (leftovers from an older document project),
   and the unwired clarification functions in `llm_service.py` / `chat_service.py`.
   Fix `routers/requirements.py` lines 329 and 351, which reference `surviving_req.original_text`
   — the model field is `current_text`, so that path raises `AttributeError` when it runs.
2. Build the gold set. Two people independently list every requirement they believe each of the two
   existing calls confirmed, reconcile disagreements, and freeze the result as
   `Backend/eval/gold/extraction_<session>.json` with text, category, speaker role, timestamp and
   the verbatim evidence line per item.
3. Write `Backend/eval/score_extraction.py`. It runs
   `extract_requirements_notes_based()` over a stored transcript's blocks and compares output to
   gold **semantically** — embed both sides, match above a threshold, then one LLM call adjudicates
   near-misses. Never string equality. It prints recall, precision, and the list of missed items.
   That missed list is the work queue for prompt tuning.
4. Strengthen `Backend/eval/eval_set.json`. It currently has 33 cases graded by case-insensitive
   substring match, which makes several assertions meaningless — the prompt-safety case passes if
   the answer merely contains the words "can" and "prompt", so an answer that leaks the system
   prompt would pass. Add regex assertions, and add cases for the lanes with no coverage.
5. Make `run_eval.py` write `Backend/eval/results/<date>.json` plus a line in `history.csv`. Run it
   before and after every change from here on.

| Task | Days |
|---|---|
| Fix dependency file, remove dead code, fix delete-path bug | 0.5 |
| Build gold set (two people, two calls, reconcile) | 1.5 |
| Write extraction scorer | 1.0 |
| Strengthen eval set, record results to disk | 1.0 |
| **Total** | **4.0** |

## C2. Speaker role and evidence propagation — 5 days

**This is the single most important change in the plan. It unlocks five of the six requirements.**

The parser already decides, for every line of every call, whether the speaker is the client or a
team member. It stores that in `conversation_logs.role`, uses it to answer chat questions, and then
**throws it away before the requirement record is written.**

### How to implement

**Step 1 — migration.** Add five columns to `requirement_versions`:

```
speaker_role       varchar(20)    'client' or 'team_member'
speaker_name       varchar(255)   the named speaker
call_timestamp     varchar(20)    e.g. '14:22'
evidence_text      text           the verbatim transcript line(s)
evidence_log_ids   json           array of conversation_logs.id
```

Keep `requirements` unchanged. Evidence belongs to the version, not to the current state.

**Step 2 — extraction emits the quote.** Update `app/prompts/requirement_notes_prompt.txt` and
`requirement_notes_to_json_prompt.txt` so each requirement row carries the timestamp and the quoted
line that produced it. The notes pass already reads the transcript in
`Speaker [timestamp]: text` form, so the information is present and simply discarded today. Thread
the new fields through `extract_requirements_notes_based()`.

**Step 3 — new file `services/evidence_resolver.py`.** Given a quoted line and a transcript id, find
the matching `conversation_logs` row: exact match, then whitespace-normalised match, then fuzzy
match above a high threshold. Return the log id, speaker, role and timestamp **read from the
database row, not from what the model claimed.**

This is the important design point. The model proposes the quote; the database supplies the
attribution. Attribution therefore stops being a model guess and becomes a lookup.

**Step 4 — wire it in.** Call the resolver in `services/requirement_comparison.py` at the point the
`RequirementVersion` object is built (around line 204). If no match is found, still store the
requirement but set a flag, and surface the count of unresolved items in
`transcripts.processing_summary` so it is visible, never silent.

**Step 5 — backfill.** Extend `Backend/backfill_transcripts.py` to re-run evidence resolution over
existing versions.

### Why this is also the best accuracy gate available

Because the quote must appear verbatim in `conversation_logs`, correctness is checkable by string
match with **no model involved**. That gives a number that cannot be argued with, and it is the
reason we can commit to ~99% on attribution while only estimating ~90% on extraction.

| Task | Days |
|---|---|
| Migration for the five columns | 0.5 |
| Extraction prompts emit timestamp and quote | 1.0 |
| `evidence_resolver.py` with three-tier matching | 1.5 |
| Wire into comparison service, flag unresolved | 1.0 |
| Backfill script and run over existing data | 1.0 |
| **Total** | **5.0** |

Serves items 1, 2, 3, 4 and 6.

---

# PART D — REQUIREMENT BY REQUIREMENT

## D1. Requirement 1 — Version tracking (3 days)

**Asked for:** maintain versions in a structured table, retain previous versions rather than
overwriting, show when a requirement was first discussed and how it evolved.

### What works now

Two tables already do exactly this.

- `requirements` holds the current rule.
- `requirement_versions` holds one row per call the requirement came up in.
- Versions are appended, never overwritten. Version 1 stays untouched permanently.
- A version row is written **even when nothing changed**, so the table is already a complete
  discussion history and not merely a change log.

### Worked example — one rule across three calls

Call 1, 2026-05-01. New requirement found.
```
requirements          current_text: "Rollback must be manual"
requirement_versions  v1 | added | 2026-05-01 | "Rollback must be manual"
```

Call 2, 2026-05-04. Client changes their mind. Matching recognises the same requirement and
classifies it modified.
```
requirements          current_text: "Rollback is automatic via Azure DevOps"   <- replaced
requirement_versions  v1 | added    | 2026-05-01 | "Rollback must be manual"
                      v2 | modified | 2026-05-04 | "Rollback is automatic via Azure DevOps"
```

Call 3, 2026-05-11. Discussed again, same rule restated. Classified unchanged, so current text is
untouched — but the mention is recorded.
```
requirement_versions  v1 | added     | 2026-05-01
                      v2 | modified  | 2026-05-04
                      v3 | unchanged | 2026-05-11
```

Read that chain and every part of the request is answered:

| Asked for | Where it already is |
|---|---|
| When it was initially discussed | v1 `discussed_date` = 2026-05-01 |
| What was originally said | v1 `text` |
| How it evolved | the v1 → v2 → v3 chain |
| What changed and when | v2 `text` and `discussed_date` |
| The current version | `requirements.current_text` |
| Previous versions retained | v1 still present, untouched |

**The data is already in the database. It simply has no route out.**

### What needs improvement

- No API route returns version history. The closest one,
  `GET /requirements/customer/{id}`, loads all the version rows, keeps only the latest date, and
  discards the rest (lines 166–178).
- The response shape `RequirementVersionResponse` is already written in `models/schemas.py` line 66
  and is used by nothing.
- `requirement_versions.requirement_id` has no index, although every history read filters on it.

### What is missing

- Any screen showing a timeline. The dashboard renders a flat list of current text.
- A way to ask the chatbot to walk through one requirement's history. The existing change feature
  answers "what changed in call X", not "how did this rule evolve".

### How to implement

**New route 1** in `routers/requirements.py`:
```
GET /requirements/{requirement_id}/history
->  { current_text, category, sub_category,
      versions: [ { version_number, text, change_type, discussed_date,
                    session, confirmed_by, proposed_by } ] }
```
Query `RequirementVersion` filtered by `requirement_id`, ordered by `version_number`. Serialise
with the existing `RequirementVersionResponse` schema. For each `modified` version, include the
previous version's text as `previous_text` so the UI can render before → after without a second
call.

**New route 2**, for the list screen:
```
GET /requirements/customer/{customer_id}/history
->  [ { id, category, sub_category, current_text,
        version_count, has_changes, first_discussed, last_discussed } ]
```
One grouped query over `requirement_versions` gives count, min and max date per requirement. This
is what lets the table show a "v3" badge and offer a "changed only" filter.

**Migration:** index on `requirement_versions.requirement_id`.

**Frontend:** in `RequirementsDashboard.jsx`, make each row expandable into a timeline:

```
Rollback is automatic via Azure DevOps                    [v3]  CI/CD
   |
   +-- 2026-05-01   Originally discussed
   |                "Rollback must be manual"
   |
   +-- 2026-05-04   Changed
   |                Before: "Rollback must be manual"
   |                After:  "Rollback is automatic via Azure DevOps"
   |
   +-- 2026-05-11   Discussed again, no change

   Current: "Rollback is automatic via Azure DevOps"
```

**Practical note:** with 170 requirements across many calls, `unchanged` rows will dominate.
Collapse them by default and put them behind a toggle, otherwise the timeline reads as clutter.

| Task | Days |
|---|---|
| History endpoint | 0.5 |
| Customer history list endpoint | 0.5 |
| Index migration | 0.25 |
| Timeline UI, expandable rows | 1.5 |
| Collapse unchanged rows, add "changed only" filter | 0.25 |
| **Total** | **3.0** |

### Boundary to state clearly

Three days delivers a visible, complete, dated version chain. It does NOT yet say who drove each
change, where in the call it was said, or store a fixed description of what changed. Those belong
to item 3 and to the foundation work. Items 1 and 3 must not be demonstrated as one thing — item 1
is nearly free, item 3 is real work.

---

## D2. Requirement 2 — Appending updates to existing requirements (5 days)

**Asked for:** recognise a requirement discussed in a later call as existing, append the new
information instead of creating a duplicate, and maintain the complete discussion history.

### What works now

Duplicate prevention is the best-engineered part of the system. Per newly extracted requirement:
vector search for candidates, an LLM match decision, an identity gate, an adversarial reword
challenge, a confidence gate, and a claim guard. The similarity score is deliberately not used as
a cutoff, so a re-worded requirement is still recognised instead of piling up as new.

### What needs improvement — a trap in the wording

**"Append the new information" has two readings and we only do one of them.**

- (a) Append to the **record** — same requirement row, new version, no duplicate. **Built.**
- (b) Append to the **text** — the statement accumulates detail across calls. **Not built.** On a
  modification, `current_text` is replaced wholesale.

This is a real exposure:
```
Call 1: "Deploy the API before the UI, and only after the test suite is green."
Call 4: "API goes before UI."
```
Classified as a modification, today's behaviour makes the second sentence the current requirement,
and the test-suite condition silently drops out of every document generated from then on. The
detail survives in history, but a client reading the requirements document sees something thinner
than what they actually agreed.

### What is missing

- A consolidation step that merges a change into the existing statement while preserving
  still-valid detail.
- A link from each version to the actual transcript exchange, so "discussion history" means the
  conversation and not just the sentence.

### How to implement

**New file `services/requirement_consolidation.py`.** One function that takes the old current text
and the newly extracted text and returns a merged statement. Prompt constraints:
- use only wording drawn from the two inputs; add no new facts
- keep every condition, constraint and exception that the new version does not explicitly revoke
- when the new version contradicts the old, the new wins for that clause only
- return the merged text plus a list of clauses carried forward, so the merge is auditable

**Migration:** add `consolidated_text` and `merge_note` to `requirement_versions`. Store both the
raw extracted statement (`text`, unchanged) and the merged result. `requirements.current_text` then
takes the merged value. Keeping both means a bad merge can be diagnosed and re-run without
re-processing the call.

**Wire in** at `requirement_comparison.py` in the `elif change_type == "modified":` branch — call
the consolidator instead of assigning `req_text` directly.

**Measurement.** Build a small gold set of merge cases from the two existing calls and measure
detail-loss rate before shipping. This is the item most likely to need a second pass.

**Discussion history view.** Uses the evidence columns from the foundation work: for each version,
show the surrounding transcript exchange, not just the requirement sentence.

| Task | Days |
|---|---|
| Consolidation service and prompt | 2.0 |
| Pipeline wiring, store raw plus merged | 1.0 |
| Gold set and detail-loss measurement | 1.0 |
| Discussion history view per requirement | 1.0 |
| **Total** | **5.0** |

### Question for the client

Does "append the new information" mean the requirement text should accumulate detail, or only that
no duplicate row is created? It is genuinely ambiguous and it changes the build. Asking is better
than guessing five days of work, and it is the kind of question that reads as thorough.

---

## D3. Requirement 3 — Client requirement change tracking (4 days)

**Asked for:** explicitly identify when the client changes a previously stated requirement, and
track the original statement and date, what changed and its date, and the current version.

### Point-by-point status

| What was asked for | Status | Where it lives |
|---|---|---|
| What the client originally mentioned | Have it | version 1 `text` |
| Date of the original discussion | Have it | version 1 `discussed_date` |
| What was subsequently changed | Partial | later version `text` — but the change description is regenerated on every question, never stored |
| Date of the change | Have it | later version `discussed_date` |
| The latest / current version | Have it | `requirements.current_text` |
| **That the change was client-driven** | **Missing** | nothing distinguishes a client change from a team change |

### What works now

Five of the six data points exist. The system also already knows who the client is
(`Customer.client_speaker_name`) and tags every transcript line accordingly.

### What needs improvement

The change description is produced fresh each time someone asks
(`llm_service.summarize_changes`), so the same change can be worded differently on Tuesday than on
Monday. For an audit record that is not acceptable. Generate once at ingestion, store it, never
recompute.

### What is missing

The speaker role on the version record. `confirmed_by` and `proposed_by` are free-text strings the
model produced, validated against nobody. Without the resolved role, "client-driven traceability"
cannot honestly be claimed.

### How to implement

**Persist the change summary.** Move the `summarize_changes` call out of the chat path and into
`requirement_comparison.py`, right after a `modified` classification is confirmed. Add a
`change_summary` column to `requirement_versions` and store the sentence there. Then update the
chat change lane in `chat_service.py` to read the stored column instead of generating.

**Derive the client flag.** With `speaker_role` on the version from the foundation work, this is a
column read, not a model call. Add a computed `is_client_driven` on the API response where
`speaker_role == 'client'`.

**New route:**
```
GET /requirements/customer/{customer_id}/change-ledger?client_only=true&from=&to=
->  [ { requirement_id, category, sub_category,
        original_text, original_date,
        change_summary, changed_text, change_date,
        current_text,
        speaker_name, speaker_role, evidence_text, session } ]
```
Built by joining version 1 and version n per requirement. This single response is exactly the five
things the client listed, plus the attribution they asked for.

**Frontend:** a ledger table with a client-only toggle, date range, and CSV/Excel export for
delivery review meetings.

| Task | Days |
|---|---|
| Persist change summaries at ingestion; switch chat lane to read them | 1.0 |
| Client vs team flag from resolved role | 0.5 |
| Change ledger endpoint | 1.0 |
| Ledger screen and export | 1.5 |
| **Total** | **4.0** |

Depends on the foundation work in C2.

---

## D4. Requirement 4 — BRD and FRD generation (9 days)

**Asked for:** on request, generate the complete BRD and the complete FRD from the captured
requirements and their version history, reflecting current requirements while preserving
traceability. Explicitly: not only a summary.

The phrase "not only a summary" tells us the client has already hit the exact failure the current
path produces.

### Why the current path cannot do this

Today a BRD comes from the chat retrieval route: retrieve roughly 30 relevant passages for a topic,
hand them to the model, ask for BRD sections (`llm_service.py` lines 618–622). With 170
requirements in the store, **30 passages cannot be a complete document.** It will always be a
good-looking subset, and nobody can tell which requirements were dropped.

### What works now

- The section structures for BRD, FRD, user stories and technical specification are already written
  and tested in the chat prompt.
- The right pattern already exists in the code. The complete-requirements lane
  (`chat_service.py` lines 825–895) reads the whole requirements table and assembles the answer
  deterministically, so items cannot be silently dropped.

### What is missing

- Any stored document artifact and any document versioning.
- File output. No document-writing library is installed.
- Traceability footnotes linking each line to its meeting and version.
- A completeness check on the generated output.

### How to implement

The fix is architectural, not prompt work: **generate from the requirements table, never from
retrieval.**

**Migration — new `documents` table:**
```
id, customer_id, doc_type, version, content_markdown,
source_transcript_id, input_fingerprint, generated_at, generated_by
```
`doc_type` is `brd`, `frd`, `tech_spec`, `minutes` or `change_log`. `input_fingerprint` is a hash of
the requirement ids and their `updated_at` values, so regeneration is skipped when nothing changed.

**New file `services/document_generation.py`.** The design principle for every document type:

1. Query the requirement rows in scope — a SQL read, so the item list is exact.
2. Build the section skeleton in code, not by the model.
3. For each section, one LLM call writes prose **for that section's items only**. Never one call
   for all 170 requirements — it will hit the output limit and truncate silently.
4. Attach footnotes by joining to `requirement_versions`: meeting, date, version number. This is a
   data join, not a model output, so it cannot be hallucinated.
5. Assemble, then run the completeness gate.

**Completeness gate.** Count the requirement items rendered in the document and compare to the SQL
count. If they differ, the document does not ship and the response names the missing ids. This is
what turns "complete document" from a claim into a verifiable property.

**The two documents are genuinely different:**

| BRD sections | FRD sections |
|---|---|
| Objective / purpose | Scope and actors |
| Business background and need | Numbered functional requirements, each "The system shall …", atomic and testable |
| Scope: in scope / out of scope | Validation rules |
| Business requirements by area | Behaviour and flow |
| Assumptions and constraints | Error handling |
| Dependencies | Configuration |
| Open questions | Traceability matrix: FR id → requirement id → source meeting |

**Word export.** Add `python-docx`, build an NFC-branded template, render the stored markdown.
Decide on PDF after the content is proven correct, not before.

**Routes:**
```
POST /documents/generate     { customer_id, doc_type, scope }  -> document id + version
GET  /documents/{id}                                            -> markdown + metadata
GET  /documents/{id}/download?format=docx
GET  /documents?customer_id=&doc_type=                          -> version list
```

| Task | Days |
|---|---|
| `documents` table and migration | 0.5 |
| Generation engine: SQL skeleton, per-section prose | 3.0 |
| BRD and FRD templates, two distinct structures | 2.0 |
| Traceability footnotes join | 1.0 |
| Completeness gate | 0.5 |
| DOCX export with template | 1.5 |
| Endpoints and UI | 0.5 |
| **Total** | **9.0** |

### Worth telling the client

Because the document is assembled from the table rather than retrieved, its coverage of stored
requirements is **100% and count-verifiable**. What cannot be claimed as 100% is whether every
requirement made it into the store in the first place — that is the extraction number, measured
separately in the foundation work.

---

## D5. Requirement 5 — Azure Boards integration (5 days)

**Asked for:** create an Epic or User Story in Azure Boards directly from a newly identified
requirement, carrying the requirement detail and supporting transcript context, traceable back to
the transcript and requirement version.

### What works now

Nothing yet. But the data model maps cleanly onto the target, and this is the lowest technical risk
of the six items.

### How to implement

**Mapping.** `category` becomes an Epic; each requirement becomes a User Story beneath it. That
falls out of the existing schema with no modelling work.

**Migration.** Add to `requirements`:
```
ado_work_item_id    integer     null until pushed
ado_work_item_url   varchar     null until pushed
ado_pushed_at       timestamp
```
Checking `ado_work_item_id` before creating is what makes the push idempotent. Without it, a re-run
duplicates the client's backlog.

**New file `services/azure_boards.py`.** Work item creation is a JSON-patch POST:
```
POST https://dev.azure.com/{org}/{project}/_apis/wit/workitems/$Epic?api-version=7.0
Content-Type: application/json-patch+json
Authorization: Basic base64(":" + PAT)

[ { "op": "add", "path": "/fields/System.Title",       "value": "..." },
  { "op": "add", "path": "/fields/System.Description", "value": "..." },
  { "op": "add", "path": "/fields/System.AreaPath",    "value": "..." } ]
```
Story-to-epic linking uses a second patch adding a `System.LinkTypes.Hierarchy-Reverse` relation.

**Description body** should carry the requirement text, the source session and date, the version
number, and the evidence quote from the foundation work — so the work item is traceable back on its
own, without opening our app.

**Config.** Organisation, project, area path, PAT and work item type names all belong in
`core/config.py` and `.env`, never hardcoded.

### Recommendation — automate the creation, not the judgement

The email says "automatically create". I recommend proposing an approval gate instead. This is
engineering judgement, not a technical limit.

Extraction is not perfectly precise. Fully automatic creation means every extraction error becomes
a work item in the client's board that somebody has to find and delete. That is highly visible and
it damages confidence in everything else we built.

Proposal: newly identified requirements land in a review list; an approver ticks the ones they
want; one click pushes the selected items. This is still automatic creation — we are automating the
work, not the decision — and it fits the single-approver model their own process implies.

**Routes:**
```
GET  /integrations/ado/queue?customer_id=      requirements not yet pushed
POST /integrations/ado/push  { requirement_ids }   -> per-item result with links
GET  /integrations/ado/status                       connection check
```

| Task | Days |
|---|---|
| Azure DevOps client: auth, create, link, retries | 1.5 |
| Mapping, hierarchy, idempotency columns | 1.0 |
| Approval queue model and endpoints | 1.0 |
| Review and push screen with result links | 1.5 |
| **Total** | **5.0** |

No dependencies. Can be pulled forward at any time for an early demonstration.

---

## D6. Requirement 6 — Automatic MOM posting to Teams (8 days)

**Asked for:** after a transcript is processed, generate the Minutes of Meeting and post to the
relevant Teams channel, covering key discussions, requirements, requirement changes, decisions and
action items.

Posting to Teams is straightforward. The dependency is the **content** — two of the five sections
rely on data we do not collect.

| MOM section | Status | Note |
|---|---|---|
| Requirements from the call | Have it | version rows for that transcript |
| Requirement changes | Have it | change lane already produces before → after |
| Key discussions | Partial | `app/prompts/topic_segmentation_prompt.txt` exists from an earlier extractor and is reusable |
| Decisions | Missing | not extracted |
| Action items | Missing | no table, no extraction — improvised at question time only |

### Reusable asset — do not rewrite this

The hard part of action items is already written and debugged against real calls. The ownership
rules live in the chat system prompt at `services/llm_service.py` lines 699–747: explicit versus
inferred versus unassigned owners, and never attributing a task to the client who merely approved
it. **Lift that text into an extraction prompt** rather than writing it again.

### How to implement

**Migration — `action_items` table:**
```
id, transcript_id, customer_id, text, owner_name, owner_role,
owner_confidence, deadline, evidence_text, evidence_log_ids, status, created_at
```
`owner_confidence` is `explicit`, `inferred` or `unassigned`, matching the existing rules.

Add a `decisions` table with the same shape, or a `type` column on one table — decisions and action
items share every field.

**New file `services/action_item_extraction.py`.** Same whole-transcript notes pattern the
requirement extractor uses. Runs in `worker/tasks.py` after requirement extraction, in the same
Celery task.

**New file `services/mom_generation.py`.** Fixed template, filled from the tables:
```
Minutes — {session_name} ({date})
Attendees            <- distinct speakers from conversation_logs
Topics discussed     <- topic segmentation
Requirements added   <- version rows, change_type = added
Requirements changed <- version rows, change_type = modified, before -> after
Decisions            <- decisions table
Action items         <- action_items table, numbered, with owner and deadline
```
Attendees and action items come from the tables, never from prose. Store the result in the
`documents` table from item 4 as `doc_type = 'minutes'` — one document store serves both items.

**New file `services/teams_notifier.py`.** Post the MOM to a configured channel.
Confirm the currently supported route before committing: Microsoft has been retiring the older
Office 365 connector webhooks in favour of Workflows / Power Automate. Graph API
(`POST /teams/{id}/channels/{id}/messages`) is the durable path but needs an app registration and
admin consent. Budget accordingly and verify first.

**Pipeline hook.** At the end of `_process_transcript_async` in `worker/tasks.py`, after status is
set to `processed`, enqueue MOM generation.

**Draft review gate.** Same reasoning as Azure Boards. A minutes document auto-posted to a
client-facing channel is the most visible thing this product does. Generate automatically, hold in
`status = 'draft'`, and let a person release it. A wrong MOM costs far more trust than a MOM that
arrives an hour later. After a few weeks of clean output, switch to direct posting if the client
wants.

| Task | Days |
|---|---|
| `action_items` and `decisions` tables | 0.5 |
| Action item extraction, lifting the existing ownership rules | 2.0 |
| Decision extraction | 1.0 |
| MOM assembly from tables | 1.5 |
| Teams posting and channel configuration | 1.5 |
| Draft review gate and release UI | 1.0 |
| Action item recall and owner accuracy measurement | 0.5 |
| **Total** | **8.0** |

---

# PART E — SCHEDULE

Sequenced so the foundation work five items depend on is done first, and so the cheapest
client-visible wins land early.

| Phase | Work | Delivers | Days |
|---|---|---|---|
| 0 | Repository and measurement baseline | Clean install; extraction accuracy measured for the first time | 4 |
| 1 | Speaker role and evidence propagation | Role, timestamp and verbatim quote on every version, verified against the transcript | 5 |
| 2 | Item 1 — version tracking | Visible, dated version timeline per requirement | 3 |
| 3 | Item 3 — client change tracking | Client-driven change ledger with stored change descriptions | 4 |
| 4 | Item 2 — consolidation | Requirements accumulate detail instead of losing it | 5 |
| 5 | Item 4 — BRD and FRD | Complete, traceable, downloadable documents | 9 |
| 6 | Item 5 — Azure Boards | Approved requirements become Epics and User Stories | 5 |
| 7 | Item 6 — MOM to Teams | Minutes generated and posted after every call | 8 |
| | **Total, one developer, sequential** | | **43** |

Items 1, 3 and the version timeline land inside the first three weeks, which is the right
sequencing for client confidence.

### Compressing the calendar

- **Two developers, roughly 6 weeks.** After Phase 1, items 5 and 6 are independent of items 1–4
  and can run on a second track.
- **Early demonstration.** Item 5 has no dependencies and can be pulled forward at any point.
- **If the timeline compresses hard:** keep Phases 0, 1, 2, 3 and the minutes. Defer consolidation,
  the technical specification and DOCX rendering. Items 1 and 3 with real transcript evidence make
  a stronger demonstration than four half-finished features.

---

# PART F — ACCURACY

The distinction that matters, and the one to explain to the client, is between what is assembled
from the database and what involves model judgement. Anything read from the tables is correct by
construction. Only the judgement surface carries error, and the engineering goal is to keep that
surface small.

| Component | Type | Expected | Why |
|---|---|---|---|
| Version history, lineage, dates | Deterministic | 100% | database reads |
| Client vs team attribution | Deterministic | ~99% | role comes from the transcript row, not the model |
| BRD/FRD coverage of stored requirements | Deterministic | 100% | assembled from the table, count-verifiable |
| Azure Boards creation | Deterministic | 100% | REST call with a duplicate guard |
| MOM structure and delivery | Deterministic | 100% | template filled from tables |
| Duplicate / same-requirement detection | Judgement | 90–95% | four guards in place; measurable today with `test_match_pool.py` |
| Change versus rewording | Judgement | ~95% precision, ~85% recall | deliberately biased against false alarms |
| Text consolidation | Judgement | ~90% | new work, needs its own measurement |
| Action items and owners | Judgement | 85–90% | rules exist, unproven at scale |
| **Requirement extraction** | **Unmeasured** | 85–95% estimate | **never measured — this caps every number above** |

Read that last row as the headline. If extraction misses a requirement, it is absent from the
version history, the BRD, the Azure board and the minutes, and no downstream guard can recover it.

### The honest sentence to give the client

"Traceability, document completeness and the integrations will be exact — they are database
operations, not judgement calls. What requires measurement, and what we will report a number for,
is how completely we capture requirements from a call in the first place."

That framing is true and considerably stronger than a vague claim of high accuracy. It also sets up
the right conversation: the one lever worth tuning is extraction, and after Phase 0 we will know
exactly where it stands.

---

# PART G — WHAT TO SAY BACK TO THE CLIENT

### Commit to

- Full version history with lineage, dates, and previous versions retained.
- No duplicate requirements across calls; every mention recorded against the same requirement.
- Explicit client-driven versus team-driven change attribution, backed by transcript evidence.
- Complete BRD and FRD covering every tracked requirement, with traceability to prior versions —
  not a summary.
- Azure Boards Epic and User Story creation with two-way links.
- Minutes generated after every processed call and delivered to Teams.

### Ask

- Does "append the new information" mean the requirement text should accumulate detail, or only
  that no duplicate is created?
- Which Azure DevOps organisation, project and area path should work items land in, and who is the
  approver?
- Which Teams channel receives the minutes, and should the first few weeks run draft-first?

### Reframe gently

Propose approval gates on the two outbound integrations. Work items and client-channel minutes are
visible artifacts, and one wrong Epic in their backlog costs more trust than a day's delay. Present
it as automatic generation with human release — which is what mature delivery teams want anyway,
and what their own single-approver language already implies.

### Do not commit to

A blanket accuracy percentage, until Phase 0 produces the extraction number. Commit instead to
reporting that number, and to the parts that are exact by construction.

---

# PART H — RISKS

- **Extraction recall may come in low.** The pipeline runs a single reasoning pass with the
  completeness review switched off (`run_review=False` in `worker/tasks.py`), chosen to cut cost
  and duplicate rows — a trade of recall at the source. If Phase 0 shows recall below target,
  re-enabling that review is the first lever. Tune it against the number, not by feel.
- **Consolidation is subtler than it looks.** Merging requirement statements without losing or
  inventing detail is genuinely difficult and deserves its own measured gold set. Budgeted at five
  days; treat it as the item most likely to need a second pass.
- **BRD scale.** At 170 requirements a naive approach hits output limits and truncates silently.
  Section-wise generation over a fixed skeleton from day one, not as a later refactor.
- **Evidence quoting will be the buggiest new thing.** Models paraphrase quotes. That is precisely
  why resolution goes through a verbatim database lookup and unmatched evidence is flagged rather
  than trusted.
- **Teams posting route is shifting.** Microsoft has been retiring the older connector webhooks.
  Confirm the supported path before committing the 1.5 days.
- **Cost per call rises.** Ingestion already makes many model calls; action items, decisions,
  consolidation and document generation add more. Measure per-call cost at the end of Phase 4 and
  reflect it in pricing.
- **Multi-project isolation.** The chat layer does not currently filter by project — it picks the
  first customer row and no retrieval path filters on `customer_id`. Fine while one client's data
  is loaded, but it must be fixed before a second project exists in the same database.
