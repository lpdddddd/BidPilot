# BidPilot Database

All tables use UUID primary keys plus `created_at` / `updated_at` (timestamptz). JSON fields use JSONB. Money fields use `NUMERIC`.

## Tables

### organizations
- PK: `id`
- Unique: `name`
- Relations: members, projects, documents, company_profiles, conversations, agent_runs

### users
- PK: `id`
- Unique/index: `email`
- Fields: `hashed_password`, `display_name`, `is_active`
- Auth is modeled only; full auth flow is out of scope for this scaffold.

### organization_members
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- FK: `user_id → users.id` ON DELETE CASCADE
- Unique: (`organization_id`, `user_id`)
- Enum `member_role`: owner, admin, manager, member, reviewer

### bid_projects
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- Indexes: `organization_id`, `status`, (`organization_id`, `status`), `project_code`
- Enum `project_status`: draft, parsing, analyzing, reviewing, completed, archived
- Money: `budget_cny`, `price_ceiling_cny` (`NUMERIC(18,2)`)

### documents
- FK: `project_id → bid_projects.id` ON DELETE CASCADE
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- Indexes: `project_id`, `organization_id`, `parse_status`, `sha256`, (`project_id`, `document_type`)
- Enums: `document_type`, `parse_status`
- Object storage pointers: `storage_bucket`, `storage_key`

### document_versions
- FK: `document_id → documents.id` ON DELETE CASCADE
- Unique: (`document_id`, `version_number`)

### document_chunks
- FK: `document_id → documents.id` ON DELETE CASCADE
- FK: `project_id → bid_projects.id` ON DELETE CASCADE
- Unique: (`document_id`, `chunk_index`)
- Indexes: `project_id`, `content_hash`
- Reserved: `qdrant_point_id` (no embedding column in PostgreSQL)

### requirements
- FK: `project_id → bid_projects.id` ON DELETE CASCADE
- FK: `source_document_id → documents.id` ON DELETE SET NULL
- Indexes: `project_id`, `category`, `requirement_code`, `review_status`, (`project_id`, `category`), (`project_id`, `risk_level`)
- Enums: `requirement_category`, `risk_level`, `quality_level`, `review_status`

### evidence_links
- FK: `requirement_id → requirements.id` ON DELETE CASCADE
- FK: `document_id → documents.id` ON DELETE SET NULL
- FK: `chunk_id → document_chunks.id` ON DELETE SET NULL

### company_profiles
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- Indexes: `organization_id`, `credit_code`

### requirement_matches
- FK: `requirement_id → requirements.id` ON DELETE CASCADE
- FK: `company_profile_id → company_profiles.id` ON DELETE CASCADE
- Indexes: `status`, (`requirement_id`, `company_profile_id`)
- Enum `match_status`: satisfied, partially_satisfied, missing, uncertain

### requirement_match_evidence
- FK: `match_id → requirement_matches.id` ON DELETE CASCADE
- FK: `document_id → documents.id` ON DELETE SET NULL
- FK: `chunk_id → document_chunks.id` ON DELETE SET NULL

### conversations
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- FK: `project_id → bid_projects.id` ON DELETE SET NULL
- FK: `user_id → users.id` ON DELETE SET NULL
- Index: (`organization_id`, `project_id`)

### messages
- FK: `conversation_id → conversations.id` ON DELETE CASCADE
- Enum `message_role`: system, user, assistant, tool

### agent_runs
- FK: `organization_id → organizations.id` ON DELETE CASCADE
- FK: `project_id → bid_projects.id` ON DELETE SET NULL
- FK: `conversation_id → conversations.id` ON DELETE SET NULL
- Indexes: `status`, (`organization_id`, `project_id`), `conversation_id`
- Enum `agent_run_status`: pending, running, waiting_for_user, completed, failed, cancelled
- `event_sequence` (int): atomic counter for next `agent_events.sequence`

### agent_steps
- FK: `agent_run_id → agent_runs.id` ON DELETE CASCADE
- Unique: (`agent_run_id`, `step_index`)

### tool_calls
- FK: `agent_run_id → agent_runs.id` ON DELETE CASCADE
- FK: `agent_step_id → agent_steps.id` ON DELETE SET NULL
- Fields: `tool_name`, `call_id`, `node_name`, `status`, `duration_ms`, `started_at`, `finished_at`
- Indexes: `agent_run_id`, `tool_name`, `call_id`
- Safe summaries only (no secrets / full PDF bodies)

### agent_events
- Unified timeline for one run; **sole ordering source** for the events API
- FK: `agent_run_id → agent_runs.id` ON DELETE CASCADE
- FK: `agent_step_id → agent_steps.id` ON DELETE SET NULL
- FK: `tool_call_id → tool_calls.id` ON DELETE SET NULL
- Unique: (`agent_run_id`, `sequence`)
- Fields: `event_type`, `node_name`, `tool_name`, `status`, `duration_ms`, `safe_summary`, `call_id`, `occurred_at`

## Integration test database

All backend PostgreSQL tests share one entry:

- Env: `TEST_DATABASE_URL` (preferred) or `DATABASE_URL_TEST`
- Default: `postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot_test`
- Safety: URL / DB name must contain `_test` (override only with `BIDPILOT_ALLOW_NONTEST_DB=1`)
- Dedicated compose (port **5433**, does not touch prod volume):

```bash
./scripts/start_test_postgres.sh
# or:
docker compose -f infra/docker-compose.test.yml up -d
export TEST_DATABASE_URL='postgresql+psycopg://bidpilot:bidpilot_test@127.0.0.1:5433/bidpilot_test'
cd backend && alembic upgrade head && pytest
```

If PostgreSQL is unreachable, fixtures **fail with an explicit message** (no mass skip). Tear down test compose with `docker compose -f infra/docker-compose.test.yml down -v`.

## Migrations

- Tool: Alembic
- Initial revision: `backend/alembic/versions/a34e7a76f341_initial_schema.py`
- Apply: `make migrate` or `bash scripts/init_db.sh`
