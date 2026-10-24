# Implementation Plan: PostMasterTelegram Foundation

## Overview

Create the initial modular-monolith scaffold for the PostMasterTelegram MVP without implementing user features yet.

## Architecture Decisions

- Django is the application foundation and admin host.
- `bot/` is kept separate from Django domain apps for Telegram UI concerns.
- Domain areas are separate Django apps so models and services can grow independently.
- SQLite is the default local database; secrets come from environment variables.
- Celery/Redis integration is represented by configuration boundaries only; publishing behavior is out of scope for this increment.

## Task List

### Phase 1: Foundation

- [x] Task 1: Add repository and environment configuration.
- [x] Task 2: Add Django project package and application entry points.
- [x] Task 3: Add modular app and bot package boundaries.
- [x] Task 4: Add developer documentation and dependency declarations.

### Checkpoint: Foundation

- [x] Python syntax compilation passes.
- [x] Expected directories and entry points exist.
- [x] No secrets are included in tracked files.

### Phase 2: First Bot Flow

- [x] Task 5: Add user onboarding and the `/start` main control panel.

### Checkpoint: First Bot Flow

- [x] User registration is idempotent by Telegram user ID.
- [x] New users default to the Free plan.
- [x] `/start` returns the main inline keyboard.
- [ ] Django tests pass after dependencies are installed.

### Phase 3: Messages

- [x] Task 6: Add saving, listing, and toggling text messages.

### Checkpoint: Messages Foundation

- [x] Text messages are stored per user.
- [x] Empty messages are rejected by the handler.
- [x] Message status can be toggled by its owner.
- [x] User and message tests pass.

## Risks and Open Questions

- Telegram API credentials and Django secret key must be supplied locally through `.env`; no real credentials are required for scaffolding.
- Dependency installation is intentionally deferred until implementation begins.
