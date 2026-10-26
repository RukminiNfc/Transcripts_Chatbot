# OnRecord — Project Memory & Quick Reference

> **Note**: For the comprehensive, always-active agent rules and architecture guide, see [GEMINI.md](file:///c:/Users/NFC%20User/Documents/Ai_projects/OnRecord/OnRecord_ChatBot/GEMINI.md) and [CLAUDE.md](file:///c:/Users/NFC%20User/Documents/Ai_projects/OnRecord/OnRecord_ChatBot/CLAUDE.md).

## What is this project?
**OnRecord (Transcripts Chatbot)** is a RAG and requirement intelligence platform for client grooming calls. It parses Microsoft Teams `.docx` transcripts, generates Minutes of Meeting (MOM), tracks requirement versions (`added`, `modified`, `unchanged`, `deprecated`), maintains living product module specifications in Markdown, extracts action items and pushes them to **Azure DevOps Boards** or **Jira Cloud**, and powers a grounded multi-lane chat interface.

## Key Paths & Components
- **API Server**: `Backend/run.py` (FastAPI on port 8001)
- **Celery Worker**: `celery -A app.core.celery_app.celery_app worker --loglevel=info --pool=solo` (Windows solo pool)
- **Frontend**: `frontend/` (React + Vite + MUI)
- **Relational DB**: PostgreSQL (`app/models/database.py`, migrations in `Backend/alembic/`)
- **Vector DB**: Qdrant (`localhost:6333`, collections: `conversations` and `requirements`)
- **Evaluation Gate**: `Backend/eval/run_eval.py` (33 real-case regression gate)
- **Prompts**: `Backend/app/prompts/*.txt` (Prompts are externalized text files with placeholders)
- **Living Modules**: `Backend/app/services/module_*.py` and `Backend/app/services/requirement_blocks.py`
- **Issue Trackers**: `Backend/app/services/azure_devops.py` & `Backend/app/services/jira.py`
- **Observability**: `Backend/app/core/observability.py` & `Backend/trace_cost_report.py` (Langfuse)

## Important Invariants
1. **Windows Celery connection pool**: Always call `await engine.dispose()` in task `finally` blocks.
2. **Date Pinning**: Session dates are parsed from session name (`MM-DD-YY_Grooming`) and pinned to noon UTC in `utils/dates.py`.
3. **Chat Lanes**: Chat query routing uses isolated lanes (`transform`, `changes`, `aggregate`, `complete_requirements`, `verify`, `minutes`, `default RAG`). Never widen one lane to fix another.
4. **Evaluation**: Always run `eval/run_eval.py` before and after modifying retrieval, routing, or prompts.
