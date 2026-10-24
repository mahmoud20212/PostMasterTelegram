# PostMasterTelegram — Project Specification

## 1. Project Overview

PostMasterTelegram is a Telegram bot for automatic publishing. Users manage their saved advertising messages, target groups, Telegram accounts, publishing schedule, sleep periods, and publishing status through interactive buttons inside Telegram.

The user does not need a web dashboard. A separate web administration panel is intended only for the bot owner.

## 2. Product Goals

- Provide a simple button-based publishing experience inside Telegram
- Allow users to save multiple text messages
- Allow users to add and select Telegram groups
- Allow users to connect one or more Telegram accounts according to their subscription limits
- Start and stop automatic publishing at any time
- Publish one message to each selected group per cycle
- Use a default delay of 15 seconds between messages
- Support an optional sleep mode
- Provide basic publishing statistics
- Start with a low-cost MVP that can run locally
- Keep the code modular and ready for future expansion

## 3. Important Product Decisions

### 3.1 User Interface

The user interface is inside the Telegram bot only. Users interact with inline buttons and conversational steps.

There is no user web dashboard in the MVP.

### 3.2 Admin Interface

The bot owner will use a separate web-based administration panel. Django Admin is sufficient for the first version.

### 3.3 No Campaign Entity in MVP

The MVP will not use campaigns or multiple publishing configurations. Each user has one global publishing setup.

Campaigns and multiple independent publishing schedules may be added later.

### 3.4 Default Settings

- Sleep mode: disabled by default
- Message interval: 15 seconds
- Distribution mode: sequential rotation / Round Robin
- One message per group per cycle
- Publishing continues until the user presses Stop or the system reaches a sleep period

## 4. Main User Flow

### 4.1 Start

When the user sends `/start`, the bot creates a user if one does not already exist and assigns the Free plan automatically.

The bot then displays the main control panel.

### 4.2 Main Control Panel

The main panel should contain buttons similar to:

- Start Publishing
- Stop Publishing
- Messages
- Groups
- Accounts
- Sleep Mode
- Publishing Settings
- Statistics
- My Subscription

The interface should be simple, clean, and based on buttons. Avoid exposing technical concepts such as queues, workers, jobs, or internal services to users.

## 5. User Features

### 5.1 Start Publishing

When the user presses Start Publishing, the system should:

1. Verify that the user has an active subscription
2. Verify that at least one message exists
3. Verify that at least one group is selected
4. Verify that a valid Telegram account is connected
5. Check whether the current time is inside a sleep period
6. Set the publishing status to active
7. Create publishing jobs for the selected groups
8. Start the publishing process in the background

### 5.2 Stop Publishing

When the user presses Stop Publishing, the system should:

- Change the publishing status to stopped
- Stop creating new publishing jobs
- Prevent pending jobs from being executed where possible
- Keep a record of the stop action

### 5.3 Messages

Users can:

- Add a text message
- View saved messages
- Edit a message
- Delete a message
- Enable or disable a message

The MVP supports text messages only. Media support can be added later.

### 5.4 Groups

Users can:

- Add a Telegram group using a username or link
- View saved groups
- Select or deselect groups for publishing
- Remove a group
- Check whether the connected account can access and publish to the group

The connected account must be a member of the target group and must have permission to send messages where required.

### 5.5 Telegram Accounts

Users can connect Telegram user accounts through a guided bot flow:

1. Enter phone number
2. Enter Telegram verification code
3. Enter the two-factor password if enabled
4. Store the Telegram session securely and encrypted
5. Display the account status

Never store verification codes or passwords as plain text.

### 5.6 Publishing Settings

The user has one global publishing setup in the MVP:

- Message interval
- Distribution mode
- User timezone
- Selected messages
- Selected groups
- Active publishing status

The default interval is 15 seconds. The system should enforce a safe minimum interval and allow the owner to change limits later.

### 5.7 Sleep Mode

Sleep mode is disabled by default.

When enabled, the user can define a rest period, for example:

- Sleep starts at 00:00
- Sleep ends at 08:00

During the sleep period, no new messages should be published. Publishing resumes automatically after the sleep period ends.

The system must use the user's configured timezone.

### 5.8 Statistics

Users should see:

- Current publishing status
- Number of messages saved
- Number of selected groups
- Successful posts
- Failed posts
- Last publishing operation
- Next scheduled operation when available
- Error reason for failed operations

## 6. Publishing Algorithm

The MVP uses sequential message rotation, not random selection.

Example:

Messages:

- Message A
- Message B
- Message C

Groups:

- Group 1
- Group 2
- Group 3
- Group 4

Cycle 1:

- Group 1 receives Message A
- Group 2 receives Message B
- Group 3 receives Message C
- Group 4 receives Message A

Cycle 2:

- Group 1 receives Message B
- Group 2 receives Message C
- Group 3 receives Message A
- Group 4 receives Message B

There is a default 15-second delay between every publishing operation.

Each group receives only one message per cycle.

Random distribution may be added later as an optional setting.

## 7. Proposed Architecture

The MVP uses a low-cost Modular Monolith architecture.

```text
Telegram User
      |
      v
Telegram Bot - aiogram
      |
      v
Django Application
      |
      +--> SQLite in local development
      |
      +--> Redis Queue
                  |
                  v
            Celery Worker
                  |
                  v
              Telethon
                  |
                  v
          Telegram Target Groups
```

The owner uses Django Admin separately:

```text
Bot Owner --> Django Admin --> Django Application
```

## 8. Recommended Technologies

### Backend

- Python 3.12+
- Django
- Django Admin

### Telegram

- aiogram for the bot interface and interactive buttons
- Telethon for connected Telegram user accounts and publishing

### Background Processing

- Redis as the task queue and temporary state store
- Celery Worker for background publishing tasks
- Celery Beat only if periodic scheduling is required by the implementation

### Database

- SQLite for local development and first MVP tests
- PostgreSQL when deploying to production or supporting more users

### Deployment

- Ubuntu VPS later
- Nginx later when a public web admin panel is deployed
- Docker optional for development and deployment

## 9. Data Model

### User

- Telegram user ID
- Username
- First name
- Status
- Current plan
- Created at
- Last active at

### Message

- User
- Text
- Is active
- Created at
- Updated at

### TelegramAccount

- User
- Telegram user ID
- Phone number, masked in the UI
- Username
- Encrypted session
- Connection status
- Last error
- Last connected at

### TelegramGroup

- User
- Telegram chat ID
- Username or invite link
- Title
- Is selected
- Can publish
- Status
- Last error

### PublishingSettings

- User
- Interval in seconds
- Distribution mode
- Is running
- Sleep enabled
- Sleep start time
- Sleep end time
- Timezone
- Current message index
- Current group index

### PublishingJob

- User
- Message
- Telegram group
- Telegram account
- Scheduled time
- Status
- Attempt count
- Error message
- Published at

### PublishingLog

- User
- Job
- Result
- Error details
- Created at

### Subscription

- User
- Plan
- Status
- Start date
- End date
- Limits snapshot

## 10. Suggested Project Structure

```text
postmaster/
├── bot/
│   ├── handlers/
│   ├── keyboards/
│   ├── states/
│   └── services/
├── core/
│   ├── settings.py
│   ├── urls.py
│   └── celery.py
├── users/
├── messages/
├── telegram_accounts/
├── telegram_groups/
├── publishing/
├── subscriptions/
├── reports/
├── manage.py
├── requirements.txt
├── .env.example
└── PROJECT_SPEC.md
```

## 11. MVP Scope

### Included

- Telegram bot
- Inline button interface
- Free plan
- Text messages
- Groups
- One connected Telegram account per user initially
- Start and stop publishing
- Sequential message rotation
- 15-second default interval
- Optional sleep mode
- Basic statistics
- Django Admin
- Local development

### Excluded for Now

- User web dashboard
- Campaigns
- Multiple independent publishing schedules
- Media messages
- Advanced financial reports
- Online payments
- React or Next.js frontend
- Microservices
- Multiple servers
- Advanced analytics

## 12. Development Roadmap

### Phase 1 — Project Setup

- Create Python virtual environment
- Install dependencies
- Create Django project
- Create aiogram bot
- Configure environment variables

### Phase 2 — User Interface

- Implement `/start`
- Create the main button panel
- Add navigation and back buttons
- Add conversation states

### Phase 3 — Messages and Groups

- Add, list, edit, delete messages
- Add, list, select, and remove groups
- Add validation and user limits

### Phase 4 — Telegram Account

- Implement Telethon login flow
- Handle verification code and 2FA
- Encrypt and store sessions
- Verify group access and publishing permission

### Phase 5 — Publishing Engine

- Implement sequential message rotation
- Add 15-second delay
- Implement start and stop
- Add job and log records

### Phase 6 — Sleep Mode and Statistics

- Implement optional sleep mode
- Add timezone handling
- Add success and failure statistics
- Display the latest errors

### Phase 7 — Testing and Deployment

- Test locally with one account and one group
- Test multiple messages and groups
- Test stop and resume
- Test sleep mode
- Move to a small VPS after successful testing

## 13. Safety and Reliability Requirements

- Respect Telegram rate limits and FloodWait responses
- Do not publish to groups where the account lacks permission
- Add an emergency stop for the user and owner
- Use a safe minimum interval
- Avoid duplicate jobs using an idempotency key
- Encrypt Telegram sessions
- Do not log verification codes or passwords
- Record all publishing errors
- Stop or pause an account after repeated failures
- The system must be used for permitted, non-spam publishing only

## 14. Instructions for Codex

Before implementing a feature:

1. Read this file completely
2. Preserve the MVP scope
3. Do not add a user web dashboard unless explicitly requested
4. Do not introduce campaigns in the MVP
5. Keep the interface button-based inside Telegram
6. Use SQLite for local development initially
7. Keep code modular so PostgreSQL can be introduced later
8. Implement and test one feature at a time
9. Do not expose secrets in code or logs
10. Explain changed files and test results after each implementation step

