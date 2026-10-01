# Context Core — Backend (Public Architecture & Code Review Mirror)

> [!IMPORTANT]
> **Public Review Notice**:
> This repository is a sanitized public mirror of the **Context Core** backend created specifically for external architecture and code review (including DeepSeek review).
> - All secrets, credentials, and private runtime data are intentionally excluded.
> - Required credentials and secrets must be provided via local environment variables (see .env.example).
> - The private local repository remains the authoritative source of truth.
> - This public mirror contains no private production data or confidential environment keys.

---
A private, single-user backend that maintains **authoritative, durable project reality** across disposable AI conversations (ChatGPT, DeepSeek, Qwen, Claude, Cline, etc.) and multiple client devices (Windows, Android, tablet, browser).

> **Core Principle**: AI conversations are disposable workers. Context Core owns durable project reality. The human developer remains the final authority.

---

## What This Backend Provides

- **Device Authentication**: Opaque random bearer tokens; only SHA-256 hashes are persisted; instant per-device revocation.
- **Project Isolation**: Every project entity is strictly isolated; no cross-project data leaks.
- **Project Lifecycle & Deletion**: Coherent hard delete with PostgreSQL `CASCADE` constraints across all dependent records (state, events, memory, captures, conversations, handoffs, roles).
- **AI Profiles & Team Roles**: Project-scoped role assignments (`Architecture`, `Technical Advisor`, `Coding Architect`, `Implementation Agent`, custom).
- **Conversation Capture**:
  - Request-level idempotency via `(client_id, local_id)`.
  - Message deduplication keyed on `(conversation_id, external_id)`, preserving legitimate repeated messages (e.g., repeated acknowledgments).
  - Stream synchronization cursors.
- **Structured Memory & Authority**:
  - Item types: `fact`, `requirement`, `decision`, `proposal`, `task`, `problem`.
  - Provenance hierarchy: `USER_CONFIRMED` > `PROJECT_FILE` > `TEST_RESULT` > `DOCUMENTATION` > `CONFIRMED_DECISION` > `AI_EXTRACTED` > `AI_PROPOSAL` > `INFERENCE` > `UNKNOWN`.
  - Client cannot arbitrarily force authoritative status; AI proposals start in `proposed` status.
- **Project State**: Append-only version history with optimistic concurrency (`expected_version` returning `409 Conflict` on version collision) and deep-copy immutable merge.
- **Event Log & WebSocket Sync**:
  - Per-project monotonic sequence allocated atomically under project row locks (`SELECT ... FOR UPDATE`).
  - Mutations and events are committed in the same database transaction.
  - WebSocket provides real-time notification; the authoritative source of truth remains REST catch-up (`GET /projects/{id}/events?since=N`).
- **Context Compiler**: Role-aware prompt generation with hard token budget enforcement.
- **Deterministic Conflict Detection**: Pure rule-based detection for value contradictions, duplicate completed tasks, and weaker-provenance overrides. No LLM dependency.
- **COMBINE**: Synthesis of project continuity (state, active memory, open problems, decisions, tasks) distinguishing known reality from proposals and conflicts.
- **Handoff Generation**: Structured inter-role handoffs with persisted context snapshots.
- **Search**: Scoped ILIKE queries across memory items, messages, conversations, and handoffs.
- **Portable Export**: Full JSON project export covering metadata, AI team, state history, conversations, messages, memory, and event log. (*Import is deliberately NOT IMPLEMENTED in V1 to prevent event sequence corruption*).
- **Postgres-Backed Job Queue**: In-process worker with exponential backoff and dead-letter tracking; AI provider failures never discard captured conversation data.

---

## Technology Stack

- **Python**: 3.12+ (tested on Python 3.13)
- **Framework**: FastAPI, Starlette, Uvicorn
- **ORM & Migrations**: SQLAlchemy 2.0, Alembic
- **Database**: PostgreSQL 14+ (relies on UUID, JSONB, row locking `FOR UPDATE`, `SKIP LOCKED`)
- **Validation & Settings**: Pydantic v2, Pydantic-Settings
- **Structured Logging**: Structlog

---

## Authentication & Device Identities

### Identity Concepts

1. **Device Identity**: Authenticated via `Authorization: Bearer <device_token>`. Generated as an opaque high-entropy random token (`secrets.token_urlsafe(48)`). Only the SHA-256 hash is stored in the database.
2. **Capture Client Identity**: In capture requests, `client_id` represents the device that recorded the exchange. If provided in the body, it must match the authenticated `device.id`; client impersonation is rejected (`403 Forbidden`).
3. **Capture Idempotency Identity**: `local_id` is a client-generated UUID. Together, `(client_id, local_id)` forms the unique idempotency key in the database.
4. **JWT_SECRET**: Reserved configuration for future token signing. Active V1 authentication uses opaque random bearer tokens.

---

## Setup & Running

### Option 1: Docker Compose

```bash
cp .env.example .env
# Edit .env and set DEVICE_REGISTRATION_SECRET and optional GROQ_API_KEY
docker compose up --build
```

The container automatically runs `scripts/wait_for_db.py`, executes `alembic upgrade head`, and starts Uvicorn at `http://localhost:8000`.

### Option 2: Local Python Environment

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env

# Configure PostgreSQL connection
export DATABASE_URL="postgresql+psycopg2://context_core:context_core@localhost:5432/context_core"
export DEVICE_REGISTRATION_SECRET="your-device-registration-secret"

alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Enrolling a Device

Before making API requests, enroll your client device:

```bash
curl -X POST http://localhost:8000/auth/device/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "workstation-pc",
    "platform": "windows",
    "registration_secret": "your-device-registration-secret"
  }'
```

Response:

```json
{
  "device_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "name": "workstation-pc",
  "platform": "windows",
  "token": "NGKf5LVKNouo157-acKD8woAMnUWz1mSQ--oOwAuhq-vB7hhQ2aP69nncBR5JJNH"
}
```

Store this token securely on the client. Send it as `Authorization: Bearer <token>` on all requests.

---

## API Summary

| Category | Method | Path | Description |
|---|---|---|---|
| **Auth** | `POST` | `/auth/device/register` | Register new device with shared secret |
| | `GET` | `/auth/device/me` | Inspect authenticated device |
| | `GET` | `/devices` | List registered devices |
| | `POST` | `/devices/{id}/revoke` | Revoke a device |
| **Projects** | `POST` | `/projects` | Create project |
| | `GET` | `/projects` | List projects |
| | `GET` | `/projects/{id}` | Read project |
| | `PATCH` | `/projects/{id}` | Update project |
| | `DELETE` | `/projects/{id}` | Hard delete project (cascades all children) |
| **AI Team** | `POST` | `/ai-profiles` | Create global AI profile |
| | `GET` | `/ai-profiles` | List AI profiles |
| | `POST` | `/projects/{id}/ai-team` | Assign profile to project role |
| | `GET` | `/projects/{id}/ai-team` | List project AI team |
| | `DELETE` | `/projects/{id}/ai-team/{role_id}` | Remove role from project |
| **Captures** | `POST` | `/projects/{id}/captures` | Idempotent conversation capture |
| | `GET` | `/projects/{id}/conversations` | List project conversations |
| | `GET` | `/projects/{id}/conversations/{conv_id}` | Get conversation with messages |
| **Memory** | `GET` | `/projects/{id}/memory` | List memory items (filter type/status) |
| | `POST` | `/projects/{id}/memory` | Create memory item & check conflicts |
| | `GET` | `/projects/{id}/memory/{mid}` | Read memory item |
| | `PATCH` | `/projects/{id}/memory/{mid}` | Update memory lifecycle status |
| **State** | `GET` | `/projects/{id}/state` | Get current state |
| | `GET` | `/projects/{id}/state/history` | List state version history |
| | `PUT` | `/projects/{id}/state` | Patch/replace state (optimistic concurrency) |
| **Events** | `GET` | `/projects/{id}/events?since=N` | Catch-up event query |
| | `WS` | `/projects/{id}/ws` | Live WebSocket event stream |
| **Context** | `POST` | `/projects/{id}/context` | Compile role-aware prompt context |
| **Combine** | `POST` | `/projects/{id}/combine` | Synthesize project reality continuity |
| **Handoff** | `POST` | `/projects/{id}/handoffs` | Create structured AI role handoff |
| | `GET` | `/projects/{id}/handoffs` | List project handoffs |
| **Search** | `GET` | `/projects/{id}/search?q=...` | Project-scoped multi-entity search |
| **Export** | `GET` | `/projects/{id}/export` | Export complete project JSON |

---

## Testing

```bash
# Run unit tests
pytest -q

# Run against dedicated PostgreSQL test instance:
TEST_DATABASE_URL="postgresql+psycopg2://context_core:context_core@localhost:5432/context_core_test" pytest -v
```

---

## Known Limitations & V1 Boundary

1. **Import is NOT IMPLEMENTED**: Project import is deferred to avoid sequence number corruption; only export is supported.
2. **Search is ILIKE**: Full-text `tsvector` or vector search is not required for single-user V1.
3. **In-Process Worker**: Job queue poller runs in a background thread inside the FastAPI process.
4. **WebSocket is Best-Effort**: Slow consumers drop queued frames and catch up through the REST `/events?since=N` endpoint.
5. **Private / Single-User Backend**: V1 does not require multi-tenant billing or role-based user hierarchies.

