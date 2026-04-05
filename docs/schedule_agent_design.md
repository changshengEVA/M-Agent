# Schedule Agent MVP

## Goal

Add a schedule domain module that lets the top-level chat controller:

- manage schedules through `schedule_manage`
- query schedules through `schedule_query`

Heartbeat execution is intentionally out of scope for this step, but the service API already reserves the methods needed later.

## Top-Level Tools

### `schedule_manage`

Used for:

- create schedule
- update schedule
- cancel schedule

Input shape:

- `instruction: str`
- `timezone_name: str | None`

### `schedule_query`

Used for:

- list schedules
- search by keyword
- filter by day

Input shape:

- `query: str`
- `timezone_name: str | None`
- `include_completed: bool`
- `limit: int | None`

## Internal Architecture

### `ScheduleAgent`

Domain controller responsible for:

- routing manage intents: `create | update | cancel`
- query parsing
- time extraction
- target resolution for update/cancel
- returning a stable result shape to the chat controller

### `ScheduleService`

Deterministic business layer responsible for:

- create
- list
- resolve target candidates
- update
- cancel
- future heartbeat-facing methods:
  - `lease_due_schedules`
  - `mark_done`
  - `mark_failed`

### `ScheduleStore`

Local JSON persistence layer.

Current storage layout:

- `data/schedules/by_user/<owner-id>/by_thread/<thread-slug>/schedules.json`

Owner isolation:

- authenticated chat threads use scoped internal thread ids such as `alice::demo-thread`
- `ScheduleAgent` derives `owner_id=alice` from the scoped thread id and persists it explicitly
- anonymous usage falls back to `owner_id=__anonymous__`

## Data Model

Each schedule item stores:

- `schedule_id`
- `owner_id`
- `thread_id`
- `title`
- `status`
- `due_at_utc`
- `timezone_name`
- `original_time_text`
- `action_type`
- `action_payload`
- `created_at`
- `updated_at`
- `source_text`
- `metadata`

## Current Scope

Included:

- one-shot schedule creation
- query by day / keyword
- update by natural language target + new time
- cancel by natural language target

Not included yet:

- recurring schedules
- heartbeat execution
- conflict optimization
- autonomous schedule rearrangement
