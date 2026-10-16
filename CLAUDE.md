# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A RAG system over client grooming-call transcripts. Two pipelines:

- **Ingest** — a `.docx` transcript is parsed, LLM-extracted into discrete *requirements*, and each requirement is diffed against the existing set (added / modified / unchanged) with a full version history.
- **Chat** — a user asks questions in natural language; a router classifies the question and dispatches it down one of several deliberately isolated answer lanes.

FastAPI + Celery backend (`Backend/`), React + Vite frontend (`frontend/`), Postgres for relational data, Qdrant for vectors, Redis as the Celery broker.

## Commands

All backend commands run from `Backend/` with the repo-root venv (`../venv/Scripts/python.exe` on Windows).

```powershell
# API (port 8001, auto-reload)
python run.py

# Celery worker — REQUIRED for transcript uploads to process at all.
# Windows needs the solo pool.
celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo

# Bootstrap the first admin (registration is admin-only, so this is the only way in)
python create_admin.py --username admin

# Alembic migrations live in Backend/alembic (NOT Backend/migrations — that dir is empty/stale)
alembic upgrade head
alembic revision --autogenerate -m "message"
```

```powershell
# Frontend (from frontend/)
npm run dev      # Vite dev server; backend CORS allows any localhost port
npm run build
npm run lint
```

Infrastructure must be running first: Postgres, Qdrant on `localhost:6333`, Redis/Memurai on `localhost:6379`.

### Evaluation — the accuracy gate

```powershell
# from Backend/
../venv/Scripts/python.exe eval/run_eval.py
```

`eval/run_eval.py` runs all 33 cases in `eval/eval_set.json` through the **real** chat pipeline against the live DB, grades `must_include` / `must_not_include` on the final answer, and — critically — diagnoses each miss as **RETRIEVAL** (fact never retrieved) vs **GEN** (retrieved but the LLM didn't use it). It is the regression suite for chat behavior: run it before and after any change to retrieval, routing, prompts, or the config thresholds. The eval set is grounded in specific stored data (client, 2 transcripts, requirement counts); if the DB contents change, the expected values in `eval_set.json` must be updated with them.

There is no pytest suite. `Backend/test_*.py` are standalone read-only diagnostic scripts (`python test_change_detection.py`), not pytest tests — they measure change-detection accuracy against hand-labeled pairs.

### Maintenance / diagnostic scripts (Backend/)

`check_data.py` (duplicate-vector + version-history check), `check_dates.py`, `purge_orphan_vectors.py` (dry-run by default, `--apply` to delete), `backfill_transcripts.py` (bulk historical load — sequential and date-ordered by design), `backfill_date_ymd.py` (one-time per environment), `wipe_data.py` (destructive; keeps customers/subscriptions, prompts for `WIPE`). Each has a docstring explaining its safety model — read it before running.

## Architecture

### Ingest pipeline

`POST /requirements/transcript` (admin-only) hash-dedupes the file, writes a `Transcript` row with `status="processing"`, and dispatches `process_transcript_task` to Celery. The worker ([tasks.py](Backend/app/worker/tasks.py)) then runs:

1. `TranscriptParserService` — parse DOCX speaker blocks into Postgres `conversation_logs` + the Qdrant `conversations` collection.
2. `RequirementExtractionService.extract_requirements_notes_based` — the **v4 notes-based** path is what's live: reason over the whole transcript into structured notes, then convert notes to JSON. Earlier strategies (`extract_requirements`, `_topic_based`, `_exhaustive`) are kept in the file but not called by the worker.
3. `RequirementComparisonService.process_and_compare` — vector-match each extracted requirement against existing ones, then a multi-layer LLM gate (`_verify_same_requirement`, `_verify_change`) decides added / modified / unchanged, writes `Requirement` + `RequirementVersion`, and supersedes the old Qdrant vector.
4. Email notification is **intentionally commented out, not deleted**, in `tasks.py` — restore by uncommenting; `notification_service.py` stays intact.

Prompts are plain text files in `app/prompts/` loaded at service construction, with `{{CLIENT_NAME}}` / `{{TOPIC_TITLE}}` / `{{EXISTING_CATEGORIES}}` placeholders. Change extraction behavior by editing those files, not by inlining prompt strings.

### Chat pipeline — routing lanes

`ChatService._prepare_answer` ([chat_service.py](Backend/app/services/chat_service.py)) is the single shared path for both streaming and non-streaming answers. `QueryProcessor.analyze_intent` makes one LLM call returning a fixed JSON contract — `intent` (conversations|requirements), `mode`, `transform_scope`, `needs_dialogue`, `aggregate`, `verify`, `scope`, `greeting`, `changes`, `complete_requirements`, `topic`, `search_query`, `filters` — and those fields dispatch to **isolated lanes**, each returning early with a deterministic `structured_answer` or falling through to RAG:

- **transform / previous_answer** — rewrites the last answer directly, no retrieval.
- **changes** — reads `RequirementVersion` history for exact before/after diffs. An unscoped "what changed" returns a per-call counts *overview*, never a full dump.
- **aggregate count/list** — answers from the requirements table directly, not from semantic snippets.
- **complete_requirements** — two-stage: rerank the *entire* requirements store at a generous gather cutoff, then an LLM confirm pass (`filter_items_by_topic`) decides true topic membership. Relevance decides membership, never the LLM-generated `category`.
- **verify** ("are you sure?") — re-checks against `last_sources` stored on the session, so the original grounding isn't lost to a thin fresh search.
- **default RAG** — `search_multi` across both Qdrant collections, Cohere rerank, then the answer LLM.

The invariant behind this design: each lane is isolated so a fix for one question type cannot regress the others. Preserve that when adding behavior — add a lane, don't widen an existing one.

### Retrieval details

- `SearchService.search_multi` embeds once and queries both `requirements` and `conversations`. `date_ymd` is a **hard Qdrant pre-filter**; `session` and `speaker` are **soft post-filters** applied only if they leave results (never a false-empty — speaker matching is partial so "Prasad" matches "Prasad Kadrikar").
- Both `SearchService` and `Reranker` apply a `_fair_share` cut so one store's dense text can't crowd the other out of the result seats.
- `Reranker` (Cohere) degrades safely: no API key, any error, or an empty result falls back to top-N by embedding score.
- **Every retrieval knob lives in [config.py](Backend/app/core/config.py)** (`RETRIEVAL_CANDIDATE_POOL`, `RETRIEVAL_TOP_N_NARROW/BROAD`, the `RERANK_*_THRESHOLD` family). Tune those against the eval set; do not hardcode thresholds in services.

### Dates

[dates.py](Backend/app/utils/dates.py) is the single source of truth. The call date is derived from the **session name** (`05-08-26_Grooming`, MM-DD-YY), not from the client-supplied timestamp, and is pinned to **noon UTC** so it can't slip a calendar day in timezones ahead of UTC. Anything storing or comparing a call date must go through `session_to_ymd` / `session_to_datetime`.

### Auth

JWT + bcrypt ([security.py](Backend/app/core/security.py)). `role` is `admin` or `user`. `Depends(get_current_user)` = any login; `Depends(require_admin)` = admin. The whole `/requirements` router and the frontend's Admin/Requirements routes are admin-only; chat is open to any logged-in user. `POST /api/auth/register` is admin-only, hence `create_admin.py`.

Frontend: token/role/username in `localStorage`, in-progress chat in `sessionStorage`. Both `setAuth` and `clearAuth` in [api.js](frontend/src/services/api.js) clear the chat state — without that, the next user on the same browser inherits the previous user's conversation. Any 401 triggers logout plus redirect to `/login`.

### Model configuration

Model IDs are per-role settings in `config.py` (`OPENAI_LLM_MODEL` for answers, `OPENAI_EXTRACTION_MODEL` for hard reasoning, `OPENAI_FORMAT_MODEL` for mechanical JSON conversion, plus comparison/clarify/resolver). GPT-5 / o-series models reject a custom `temperature` and need `max_completion_tokens` — each service that calls OpenAI carries an `_is_next_gen()` guard plus a fallback model. Copy that pattern rather than calling `chat.completions` directly.

## Gotchas

- **Celery on Windows**: each task uses a fresh `asyncio.run()` loop, so `_process_transcript_async` must `await engine.dispose()` in its `finally` — otherwise the next task reuses connections on a dead loop and fails with `'NoneType' object has no attribute 'send'`.
- **`conversation_history` is a plain JSON column**, not mutation-tracked. Always rebuild it with fresh dict/list objects and call `flag_modified(session, "conversation_history")`, or the write is silently dropped.
- `init_db()` runs `create_all` on startup *and* Alembic migrations exist; schema can drift between the two. Prefer Alembic for real schema changes. Note there are **two** migration dirs: `Backend/alembic` is live, `Backend/migrations` is an empty shell — check `alembic.ini` before generating a revision. `customers` and `team_subscriptions` have no migration at all; they were created by `create_all`, so adding columns to them needs a hand-written revision.
- **`config.py` sets `extra = "ignore"`**, so a misspelled key in `.env` is silently dropped and the code default is used. `Backend/.env.example` documents every supported key — keep it in sync when adding settings.
- **`JWT_SECRET_KEY` is absent from `.env`**, so the insecure placeholder default in `config.py` is currently in force. Set a real one before any non-local deployment.
- `version_number` on `requirement_versions` is derived as `count(existing) + 1`, and the chat changes lane finds the "before" text via `version_number - 1`. Both assume a contiguous 1..n chain, so anything that deletes version rows must renumber the survivors (`routers/requirements.py` does this on transcript delete).
- Uploaded `.docx` files land in `Backend/app/uploads/` and are deleted by the worker after processing. `data/`, `Backend/backups/`, and `.env` are gitignored.

## Planning document

`OnRecord_Requirements_Plan.md` (root) is the authoritative spec for in-flight work: Part A documents the system as built (tables, vector payloads, both pipelines), Parts C–D break down six client requirements (version tracking, appending updates, change tracking, BRD/FRD generation, Azure Boards, Teams MOM posting) with per-requirement "what works now / what's missing / how to implement". Consult it before designing anything in those areas.
