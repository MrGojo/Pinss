# PRD — Pinterest Bulk Pin Generator

## Original Problem Statement
Build a web application that automatically generates Pinterest Pins from an uploaded Excel/CSV sheet for affiliate marketing, including automatic prompt generation, optional template mode, bulk generation (up to 500), previews, download/export, and metadata persistence for future Pinterest automation.

## User Choices (Captured)
- AI generation mode for V1: **MOCK** (no external AI calls yet)
- API key handling: user may provide own keys later
- Generation scale target: up to **500** pins
- Template behavior: adjustable quote placement (**top/center/bottom**)
- Metadata storage: **MongoDB + CSV/JSON metadata export**

## Architecture Decisions
- **Frontend:** React single-page dashboard with shadcn/ui components and responsive preview grid.
- **Backend:** FastAPI with async pipeline and semaphore-based concurrency for pin rendering.
- **Database:** MongoDB (`pin_records`, `generation_sessions`) for metadata and retrieval.
- **Image Rendering:** Pillow-based composition at Pinterest ratio 1000x1500.
- **Mock AI Mode:** deterministic prompt-builder + curated background pool (no external model calls).
- **File Delivery:** static pin serving + endpoint-based individual/ZIP/metadata exports.

## User Personas
1. Affiliate marketer managing large quote/content batches.
2. Content creator preparing scheduled Pinterest creatives.
3. VA/operations user needing download and metadata handoff.

## Core Requirements (Static)
- Upload `.xlsx`/`.csv` with required columns.
- Generate per-row prompt from Meta Title + Meta Description + TAG TOPIC.
- Use template if uploaded; otherwise use auto background generation path (mocked in V1).
- Enforce pin layout: visual background + centered quote + bottom white CTA bar (“Tap to learn more”).
- Filename from quote slug.
- Support bulk up to 500.
- Show progress + preview grid + individual and ZIP downloads.
- Persist metadata and export as CSV/JSON.
- Keep backend structured for future Pinterest automation integration.

## What’s Implemented (with dates)
### 2026-03-16
- Built full backend pin generation API set:
  - `POST /api/pins/generate`
  - `GET /api/pins/{session_id}`
  - `GET /api/pins/progress/{session_id}`
  - `GET /api/pins/download/{pin_id}`
  - `GET /api/pins/download-all/{session_id}`
  - `GET /api/pins/export/{session_id}?export_format=csv|json`
- Implemented CSV/XLSX parsing with strict required-column validation.
- Implemented deterministic prompt builder from metadata fields.
- Implemented Pillow renderer with required CTA strip and quote readability overlays.
- Implemented template upload support with validated text position (`top|center|bottom`).
- Implemented slugified file naming from quote text and unique collision handling.
- Stored pin metadata in MongoDB and enabled CSV/JSON export.
- Built React dashboard: uploads, generation controls, progress, preview cards, and download/export actions.
- Added dark mode with consistent styling across major UI surfaces.
- Added test IDs on interactive and user-critical UI elements.
- Verified backend via curl and frontend via Playwright screenshot flows.

## Prioritized Backlog
### P0 (Next Critical)
- Integrate real AI providers for prompt generation + image generation (replace mocks).
- Add robust job queue (RQ/Celery) for very large batches and resumable jobs.
- Add per-item generation error reporting and retry controls in UI.

### P1 (Important)
- Add true real-time websocket/SSE progress updates.
- Add template text-style controls (font family, size, shadow intensity).
- Add metadata edit panel before export.

### P2 (Later)
- Pinterest publishing module prep endpoints (title/description/tags/board/link mapping).
- Scheduling calendar and bulk posting workflow.
- Multi-user workspaces and role permissions.

## Remaining Next Tasks List
1. Connect real AI services and key management path.
2. Introduce background worker for faster 500+ throughput reliability.
3. Add pre-generation validation report for malformed rows.
4. Add pagination/filtering in preview grid for large sessions.
5. Add phase-2 Pinterest automation scaffolding routes.
