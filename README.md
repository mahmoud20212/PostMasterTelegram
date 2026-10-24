# PostMasterTelegram

Telegram automatic publishing bot based on the MVP specification in
[`PROJECT_SPEC.md`](PROJECT_SPEC.md).

## Current status

The repository currently contains the initial modular-monolith scaffold. User
features, database models, and publishing workflows will be implemented in
separate increments.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python manage.py check
python manage.py migrate
python manage.py runserver
```

Redis and Telegram credentials are not required for the initial Django health
check, but will be required by the bot and publishing worker increments.

## Structure

- `config/`: Django project configuration and Celery integration boundary
- `bot/`: aiogram handlers, keyboards, states, and bot services
- `users/`, `messages/`, `telegram_accounts/`, `telegram_groups/`, `publishing/`, `subscriptions/`, `reports/`: domain boundaries
- `tasks/`: implementation plan and task checklist

