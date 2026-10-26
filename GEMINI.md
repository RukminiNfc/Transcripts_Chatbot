# GEMINI.md — Project Memory & Architectural Guide for OnRecord

This file provides persistent project context, architecture, operational rules, and development guidelines for Antigravity and AI agents working on this codebase.

---

## 1. Project Overview & Mission

**OnRecord (Transcripts Chatbot)** is an AI-powered requirement intelligence and RAG platform built for client grooming-call transcripts (Microsoft Teams `.docx` files).

### Core Problem It Solves
Verbal agreements and scope changes across recurring client grooming sessions drift, become lost, or conflict over time. OnRecord ingests meeting transcripts, generates Minutes of Meeting (MOM), tracks requirement versions (`added`, `modified`, `unchanged`, `deprecated`), maintains living product module specifications in Markdown, extracts action items to push to **Azure DevOps Boards** or **Jira Cloud**, and provides an isolated multi-lane natural language chat interface.

---

## 2. Infrastructure & Environment

- **Backend**: FastAPI (Python 3.11, port 8001), SQLAlchemy (Async), Alembic, Celery + Redis (Memurai) on port 6379.
- **Frontend**: React 18, Vite (dev server port 5174+), Material UI (MUI), React Router 6.
- **PostgreSQL**: Relational source of truth on port 5432.
- **Qdrant**: Vector database on `localhost:6333` with 1536-dim cosine collections:
  - `conversations`: Verbatim speaker utterances with $\pm 2$ window context.
  - `requirements`: Active requirement blocks with normalized `date_ymd` filters.
- **AI Models (OpenAI & Cohere)**:
  - Chat Answers & Formatting: `gpt-5.4-mini` (or configured `OPENAI_LLM_MODEL`).
  - Reasoning, Extraction, Reconcile, MOM: `gpt-5.5` (or configured `OPENAI_EXTRACTION_MODEL` / `OPENAI_MOM_MODEL`).
  - Next-gen models reject custom `temperature` and require `max_completion_tokens`. Use the `_is_next_gen()` pattern.
  - Reranker: Cohere `rerank-v3.5` with fair-share allocation across collections.
- **Observability**: Langfuse integration (`observability.py`) records latency, tokens, and per-stage cost attribution (`trace_cost_report.py`).

---

## 3. Standard Commands

All backend commands run from `Backend/` using the workspace venv (`../venv/Scripts/python.exe` on Windows):

```powershell
# 1. API Server (port 8001, auto-reload)
python run.py

# 2. Celery Worker (REQUIRED for uploads & MOM processing; MUST use solo pool on Windows)
celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo

# 3. Create Admin User (registration is admin-only; bootstrap first user)
python create_admin.py --username admin

# 4. Database Migrations (use Backend/alembic, NOT Backend/migrations)
alembic upgrade head
alembic revision --autogenerate -m "description"

# 5. Accuracy & Regression Evaluation (run before and after prompt/retrieval changes)
../venv/Scripts/python.exe eval/run_eval.py

# 6. Observability & Cost Report
python trace_cost_report.py
```

Frontend commands (from `frontend/`):
```powershell
npm run dev      # Vite dev server
npm run build    # Production build
npm run lint
```

---

## 4. Key Pipelines & Architecture

### A. Ingestion Pipeline (`worker/tasks.py`)
1. `POST /requirements/transcript` (admin-only) computes SHA-256 hash dedup, writes `Transcript` row (`status="processing"`), and queues Celery task.
2. `TranscriptParserService` parses speaker turns, timestamps, and tags speaker role (`client` vs `team_member` via `Customer.client_speaker_name`). Inserts to Postgres `conversation_logs` and Qdrant `conversations`.
3. Call date is strictly derived from the **session name** (`05-08-26_Grooming` -> 2026-05-08) and pinned to **noon UTC** (`utils/dates.py`).
4. Ingestion can optionally trigger MOM generation (`ENABLE_AUTO_MOM=True`).

### B. Minutes of Meeting (MOM) & Action Items (`services/mom_generation.py`)
1. Generates structured MOM Markdown (`## Topic`, `### Sub-section`, bullets, action items).
2. Action items extracted into `mom_action_items` table (draft status).
3. Admin reviews and assigns owners/due dates on `/minutes/:momId/tasks`.
4. On approval, tasks are pushed to the customer's configured tracker (**Azure DevOps Boards** or **Jira Cloud**) with bidirectional links.
5. MOM can be edited inline in frontend, exported to `.docx` Word format, or emailed via SMTP.

### C. Living Module Documents Pipeline (`services/module_pipeline.py`)
When `ENABLE_MODULE_DOCS=True`:
1. MOM sections are classified into customer modules (`module_classifier.py`).
2. **One MOM sub-section = One requirement** (`requirement_blocks.py`). Code builds deterministic blocks (`Topic: Sub-heading` + bullets) with normalized `heading_key`.
3. `module_reconciler.py` returns pure decisions (`ADD`, `MODIFY`, `DEPRECATE`, `SKIP`).
4. `module_document_writer.py` updates Markdown in Python without hallucinating unaffected requirements.
5. Stamped snapshots with line diffs stored in `module_document_revisions`.
6. `module_indexer.py` synchronizes the `requirements` table and Qdrant vectors.

### D. Chat & Retrieval Pipeline (`services/chat_service.py`)
`QueryProcessor.analyze_intent` classifies intent and dispatches to **isolated lanes**:
- `transform / previous_answer`: Rewrites last answer without vector retrieval.
- `changes`: Reads `RequirementVersion` history for exact before/after diffs.
- `aggregate count / list`: Direct SQL over `requirements` table.
- `complete_requirements`: Two-stage gather + LLM confirm pass for true topic membership.
- `verify`: Double-checks previous answer against cached `last_sources`.
- `minutes`: Emits MOM document card for viewing or downloading.
- `default RAG`: Dual-search (`requirements` + `conversations`), fair-share cut, Cohere rerank, grounded generation.

*Rule: Keep lanes isolated so a fix for one question type never regresses others.*

---

## 5. Core Database Models (`app/models/database.py`)

- `transcripts`: File hash, session name, call date, extraction JSON (`extracted_requirements`), status (`processing` / `processed` / `failed`).
- `conversation_logs`: Verbatim speaker utterances, timestamps, role (`client` / `team_member`).
- `requirements`: Active state (`current_text`, `category`, `module_document_id`, `heading_key`, `title`, `status`).
- `requirement_versions`: Audit history (`version_number`, `change_type`, `text`, `session`, `discussed_date`, `transcript_id`).
- `module_documents`: Living product specs per customer (`module_name`, `content_markdown`, `gdoc_file_id`, `current_version`).
- `module_document_revisions`: Full Markdown snapshots, `revision_summary`, `line_diffs` per update.
- `meeting_minutes`: Markdown content, `original_markdown` (pre-edit baseline), version, approval metadata, email status.
- `mom_action_items`: Draft/pushing/created/failed tasks, `tracker` (`ado`/`jira`), `external_key`, `external_url`.
- `customers`: Multi-tenant settings, `client_speaker_name`, `tracker`, ADO area/iteration path, Jira project key.
- `users`: Username, hashed password (bcrypt), role (`admin` / `user`).

---

## 6. Critical Operational Gotchas

1. **Celery on Windows Connection Pool**: Each Celery task creates a fresh `asyncio.run()` loop. Any async worker function MUST call `await engine.dispose()` in its `finally` block, or subsequent tasks fail with `'NoneType' object has no attribute 'send'`.
2. **Postgres JSON Columns**: `conversation_history` and `extracted_requirements` are mutable JSON columns. Changes must be assigned as fresh dicts/lists and flagged via `flag_modified(session, "column_name")`.
3. **Pydantic Settings**: `config.py` sets `extra = "ignore"`. Misspelled keys in `.env` are silently ignored. Keep `.env` and `.env.example` in sync.
4. **Evaluation Gate**: Always run `eval/run_eval.py` before and after changing prompts, retrieval thresholds, or routing logic.
5. **Prompt Files**: Plaintext prompts reside in `Backend/app/prompts/`. Modify prompt text files rather than hardcoding prompt strings in Python services.
