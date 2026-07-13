# Schedule Agent MVP

## Goal

Add a schedule domain module that lets the top-level chat controller:

- create schedules through `schedule_create`
- query schedules through `schedule_query`
- delete schedules through `schedule_delete`

Heartbeat execution is intentionally out of scope for this step, but the service API already reserves the methods needed later.

## Top-Level Tools

### `schedule_create`

Used for: create one schedule.

Input shape:

- `due_at: str` (ISO-8601)
- `action: str` (concrete reminder/action text)
- `timezone_name: str | None`

Returns `schedule_id` and `item`.

### `schedule_query`

Used for: list/search schedules.

Input shape:

- `keyword: str`
- `start_at: str`
- `end_at: str`
- `timezone_name: str | None`
- `include_completed: bool`
- `limit: int | None`

Returns `items[]` and `schedule_ids[]`.

### `schedule_delete`

Used for: cancel one schedule by id.

Input shape:

- `schedule_id: str`

## Internal Architecture

### `ScheduleAgent`

Domain controller responsible for:

- structured create/query/delete handlers
- returning a stable result shape to the chat controller

### `ScheduleService`

Deterministic business layer responsible for:

- create
- list
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
