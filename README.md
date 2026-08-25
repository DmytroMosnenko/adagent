# AdAgent

AI-powered OLX ad analysis SaaS. Paste any OLX, Otomoto, or Otodom filter URL — AdAgent scrapes every ad, scores it 1–10, flags red flags, and returns a ranked leaderboard report.

**Freemium model:** first 5 ads free, unlimited with Pro subscription ($9.99/month via Stripe).

---

## Table of Contents

1. [Architecture overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Server installation (production)](#server-installation-production)
4. [Developer machine setup](#developer-machine-setup)
5. [Environment variables reference](#environment-variables-reference)
6. [Stripe setup](#stripe-setup)
7. [AWS SES setup](#aws-ses-setup)
8. [Deploying a new version](#deploying-a-new-version)
9. [Running tests](#running-tests)
10. [Adding a new prompt preset](#adding-a-new-prompt-preset)
11. [Project structure](#project-structure)
12. [Operational runbook](#operational-runbook)

---

## Architecture overview

```
Browser → nginx (TLS) → uvicorn (FastAPI)
                              │
                 ┌────────────┼─────────────┐
                 │            │             │
             MariaDB      Playwright     OpenAI
            (sessions,   (scraping)    (analysis)
            reports,
           subscriptions)
                              │
                     /opt/adagent/report_storage/
                         (HTML report files)
```

- **Web layer** — FastAPI serves both the HTML UI (Jinja2 templates) and the JSON status API. No separate frontend build step.
- **Analysis** — runs as a FastAPI `BackgroundTask` (asyncio). Playwright collects links and extracts content; OpenAI analyzes each ad and produces a summary; Jinja2 renders the HTML report.
- **Auth** — passwordless magic link via AWS SES → `httponly` session cookie (7-day TTL).
- **Billing** — Stripe Checkout (subscription). After payment, the Stripe webhook creates the subscription record and emails a magic sign-in link to the customer.
- **Reports** — preset-prompt reports are stored as self-contained `.html` files in `report_storage/`. Custom-prompt results are stored as JSON in the `reports` table.

---

## Prerequisites

### Both environments

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.12 | |
| MariaDB | 10.6 | MySQL 8 works too |
| Git | any | |

### Production server only

| Tool | Notes |
|------|-------|
| AlmaLinux 9 / Amazon Linux 2023 | Other RHEL-compatible distros work |
| nginx | Installed via `dnf` |
| certbot | For TLS; installed via `dnf` |
| systemd | Standard on all target distros |

---

## Server installation (production)

This guide assumes a fresh **AlmaLinux 9** or **Amazon Linux 2023** server.

### 1 — System packages

```bash
# Enable EPEL (AlmaLinux)
dnf install -y epel-release

# Core packages
dnf install -y python3.12 python3.12-pip mariadb-server nginx certbot \
               python3-certbot-nginx git

# Start MariaDB and nginx
systemctl enable --now mariadb nginx
```

### 2 — MariaDB database

```bash
# Secure the installation (set root password, remove test DB)
mysql_secure_installation

# Create database and user
mysql -u root -p << 'SQL'
CREATE DATABASE adagent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'adagent'@'localhost' IDENTIFIED BY 'STRONG_PASSWORD_HERE';
GRANT ALL PRIVILEGES ON adagent.* TO 'adagent'@'localhost';
FLUSH PRIVILEGES;
SQL
```

### 3 — Build the RPM

The RPM is built automatically by GitHub Actions on every `v*` tag push (see `.github/workflows/build-rpm.yml`). Download the `.rpm` asset from the GitHub Release page.

**Or build manually** (on any Linux machine with `rpmbuild`):

```bash
# Install build tools (Ubuntu/Debian CI runner)
apt-get install -y rpm rpmlint python3.12 python3.12-venv

mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
cp -r . ~/rpmbuild/SOURCES/
cp packaging/adagent.spec ~/rpmbuild/SPECS/

rpmbuild -bb \
  --define "_topdir     $HOME/rpmbuild" \
  --define "_sourcedir  $HOME/rpmbuild/SOURCES" \
  --define "version     0.1.0" \
  ~/rpmbuild/SPECS/adagent.spec

# RPM will be at ~/rpmbuild/RPMS/x86_64/adagent-0.1.0-1.x86_64.rpm
```

### 4 — Install the RPM

```bash
# Copy the RPM to the server, then:
dnf install -y ./adagent-0.1.0-1.x86_64.rpm
```

The `%post` script automatically:
- Creates the `adagent` system user
- Installs Python dependencies into `/opt/adagent/venv/`
- Downloads Playwright Chromium to `/opt/adagent/.playwright/`
- Requests a TLS certificate from Let's Encrypt (requires the domain to be pointed at this server)
- Runs Alembic migrations (`adagent-migrate.service`)
- Enables and starts `adagent.service`
- Enables the twice-daily SSL renewal timer

### 5 — Configure secrets

The secrets file is installed as `/opt/adagent/secrets/env` (mode 600, owned by `adagent`).  
Edit it before or immediately after installation:

```bash
nano /opt/adagent/secrets/env
```

See [Environment variables reference](#environment-variables-reference) for all keys.  
At minimum you must set:

```
DB_PASSWORD=the_password_from_step_2
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICES_JSON={"monthly": "price_..."}
OPENAI_API_KEY=sk-...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
SECRET_KEY=<64 random chars — run: python3 -c "import secrets; print(secrets.token_hex(32))">
```

### 6 — Restart and verify

```bash
systemctl restart adagent

# Check it started cleanly
systemctl status adagent
journalctl -u adagent -n 50

# Health check
curl https://adagent.dimosense.com/health
# → {"status":"ok","version":"0.1.0"}
```

### 7 — Register the Stripe webhook

In the [Stripe Dashboard → Webhooks](https://dashboard.stripe.com/webhooks), add:

- **Endpoint URL:** `https://adagent.dimosense.com/webhooks/stripe`
- **Events to send:**
  - `checkout.session.completed`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`

Copy the **Signing secret** (`whsec_...`) into `STRIPE_WEBHOOK_SECRET` in the env file, then restart:

```bash
systemctl restart adagent
```

---

## Developer machine setup

### 1 — Clone and create virtualenv

```bash
git clone https://github.com/DmytroMosnenko/adagent.git
cd adagent

python3.12 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 2 — Install Playwright browser

```bash
playwright install chromium
playwright install-deps chromium   # installs system-level libs (Linux only)
```

### 3 — Local MariaDB (Docker recommended)

```bash
docker run -d \
  --name adagent-db \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=adagent \
  -e MYSQL_USER=adagent \
  -e MYSQL_PASSWORD=localpassword \
  -p 3306:3306 \
  mariadb:10.11
```

Or use a local MariaDB/MySQL installation — create the DB and user as in the server guide.

### 4 — Create secrets file

```bash
mkdir -p secrets
cp packaging/env secrets/env
```

Edit `secrets/env`. For local development the minimum required set is:

```bash
DB_PASSWORD=localpassword          # matches Docker setup above
OPENAI_API_KEY=sk-...              # real key needed for actual analysis
SECRET_KEY=any-local-dev-secret-at-least-32-chars

# Leave Stripe/SES empty for local dev — auth and payments won't work
# but the core analysis pipeline runs fine
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

### 5 — Run database migrations

```bash
alembic upgrade head
```

### 6 — Start the development server

```bash
uvicorn adagent_server:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in a browser.

> **Note:** magic-link emails won't be sent without real SES credentials.  
> To test the auth flow locally, watch the server logs — the magic link token is logged at DEBUG level, so you can paste it directly into the browser:  
> `http://localhost:8000/auth/verify/<token>`

---

## Environment variables reference

All variables are read from `secrets/env` (production: `/opt/adagent/secrets/env`).

### Application

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `APP_BASE_URL` | `https://adagent.dimosense.com` | | Full public URL, used in email links |
| `DEBUG` | `false` | | Enable FastAPI debug mode |
| `SECRET_KEY` | — | **Yes** | HMAC secret; generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `FREE_ADS_LIMIT` | `5` | | Max ads analyzed on the free tier |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_USER` | `adagent` | MariaDB username |
| `DB_PASSWORD` | — | **Required** |
| `DB_HOST` | `localhost:3306` | host:port |
| `DB_NAME` | `adagent` | Database name |

### Stripe

| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | `sk_live_...` from Stripe Dashboard |
| `STRIPE_WEBHOOK_SECRET` | `whsec_...` from the Webhook endpoint page |
| `STRIPE_PRICES_JSON` | JSON map of period → Stripe price ID. Add `weekly`/`daily` when you create those prices: `{"monthly":"price_xxx","weekly":"price_yyy","daily":"price_zzz"}` |
| `STRIPE_SUCCESS_URL` | Redirect after successful payment |
| `STRIPE_CANCEL_URL` | Redirect on checkout cancel |

### AWS SES

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_REGION` | `eu-west-1` | SES region |
| `AWS_SES_FROM_EMAIL` | `noreply@dimosense.com` | Verified sender address |
| `AWS_ACCESS_KEY_ID` | — | IAM key with `ses:SendEmail` permission |
| `AWS_SECRET_ACCESS_KEY` | — | |

### OpenAI

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | **Required** for analysis |
| `OPENAI_AD_MODEL` | `gpt-4o-mini` | Model for per-ad analysis (runs N times) |
| `OPENAI_SUMMARY_MODEL` | `gpt-4o-mini` | Model for the final summary (runs once) |
| `OPENAI_AD_MAX_TOKENS` | `1024` | |
| `OPENAI_SUMMARY_MAX_TOKENS` | `1024` | |

### Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `SESSION_TTL_DAYS` | `7` | Cookie lifetime |
| `MAGIC_LINK_TTL_MINUTES` | `15` | Magic link expiry |

### Storage / Playwright

| Variable | Default | Description |
|----------|---------|-------------|
| `REPORT_STORAGE_PATH` | `/opt/adagent/report_storage` | Where HTML report files are saved |
| `PLAYWRIGHT_BROWSERS_PATH` | `/opt/adagent/.playwright` | Playwright browser cache |
| `PLAYWRIGHT_HEADLESS` | `true` | Set `false` for visual debugging |

---

## Stripe setup

1. Go to [Stripe Dashboard → Products](https://dashboard.stripe.com/products) → **Add product**
2. Name: `AdAgent Pro`
3. Add a price: **Recurring**, **Monthly**, `$9.99 USD`
4. Copy the **Price ID** (`price_xxx`) into `STRIPE_PRICES_JSON`:
   ```
   STRIPE_PRICES_JSON={"monthly": "price_xxx"}
   ```
5. To add weekly or daily plans later, create additional prices and extend the JSON:
   ```
   STRIPE_PRICES_JSON={"monthly": "price_xxx", "weekly": "price_yyy", "daily": "price_zzz"}
   ```
   The subscribe page automatically renders a button for each configured plan.
6. Register the webhook endpoint (see [server installation step 7](#7--register-the-stripe-webhook)).

---

## AWS SES setup

1. In the [AWS SES Console](https://eu-west-1.console.aws.amazon.com/ses/):
   - Verify the sender domain (`dimosense.com`) or email address
   - If in sandbox mode, also verify recipient addresses, or [request production access](https://docs.aws.amazon.com/ses/latest/dg/request-production-access.html)
2. Create an **IAM user** with this policy:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": "ses:SendEmail",
       "Resource": "*"
     }]
   }
   ```
3. Generate an access key and put it in `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

---

## Deploying a new version

```bash
# 1. Commit and push your changes
git add .
git commit -m "feat: improve real estate prompt"
git push origin main

# 2. Tag the release — this triggers the GitHub Actions RPM build
git tag v0.2.0
git push origin v0.2.0

# 3. GitHub Actions builds adagent-0.2.0-1.x86_64.rpm and attaches it
#    to the GitHub Release page automatically.

# 4. On the server:
dnf upgrade -y ./adagent-0.2.0-1.x86_64.rpm
# The %post script runs migrations and restarts the service automatically.
```

---

## Running tests

```bash
# Install test dependencies (already in requirements.txt)
pip install -r requirements.txt
pip install aiosqlite pytest-asyncio

# Run all 114 tests
pytest

# Run a specific file
pytest tests/test_routes.py -v

# Run with output on failure
pytest --tb=short

# Run a single test
pytest tests/test_tasks.py::test_task_applies_freemium_limit -v
```

**Tests never make real network calls** — Playwright, OpenAI, Stripe, and AWS SES are all mocked. An in-memory SQLite database is used so no MariaDB is needed for testing.

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `test_schemas.py` | 13 | URL and preset validation |
| `test_ai_client.py` | 13 | JSON parsing, ad formatting |
| `test_report_builder.py` | 28 | Spec items, HTML rendering, dedup, freemium banner |
| `test_crud.py` | 26 | All database operations |
| `test_routes.py` | 26 | All HTTP routes end-to-end |
| `test_tasks.py` | 8 | Full analysis pipeline, freemium limit, error handling |

---

## Adding a new prompt preset

1. Create two prompt files in `prompts/`:
   ```
   prompts/electronics_ad.txt      ← per-ad JSON prompt
   prompts/electronics_summary.txt ← summary JSON prompt
   ```
   Follow the JSON schema in the existing `vehicles_ad.example.txt` as a template.

2. Register the preset in `adagent_server.py`:
   ```python
   PRESETS = {
       "vehicles":    {"label": "Vehicles 🚗",      "description": "..."},
       "realestate":  {"label": "Real Estate 🏠",   "description": "..."},
       "electronics": {"label": "Electronics 💻",   "description": "..."},  # ← add this
   }
   ```

3. Update the validator in `service/schemas.py`:
   ```python
   if v not in ("vehicles", "realestate", "electronics"):
   ```

4. Add a test case in `tests/test_schemas.py` and `tests/test_tasks.py`.

No other changes needed — the task runner auto-resolves prompt paths from the preset name.

---

## Project structure

```
adagent/
├── adagent_server.py          FastAPI application — all routes and startup
├── alembic.ini                Alembic configuration
├── pytest.ini                 pytest configuration
├── requirements.txt
│
├── service/                   Business logic
│   ├── config.py              Pydantic Settings (reads secrets/env)
│   ├── db.py                  SQLAlchemy async engine + get_db dependency
│   ├── models.py              ORM models: User, Session, MagicLink, Subscription, Report
│   ├── schemas.py             Pydantic request/response schemas + validators
│   ├── crud.py                All database operations
│   ├── auth.py                Token generation + current user dependency
│   ├── email_client.py        AWS SES — magic link and welcome emails
│   ├── stripe_client.py       Stripe Checkout, webhook parsing, plan detection
│   ├── scraper.py             Playwright OLX/Otomoto/Otodom scraper
│   ├── ai_client.py           OpenAI calls with retry + parse_json_safe
│   ├── report_builder.py      Jinja2 HTML report builder
│   ├── tasks.py               Background analysis pipeline
│   └── logger.py              Shared logger factory
│
├── templates/                 Jinja2 HTML templates
│   ├── base.html              Navigation, <head>
│   ├── index.html             Landing page + analysis form
│   ├── status.html            "Analyzing…" with JS polling
│   ├── report_custom.html     Custom-prompt result display
│   ├── subscribe.html         Pricing page
│   ├── subscribe_success.html Post-payment confirmation
│   ├── auth_request.html      Magic link request form
│   ├── history.html           User's report history
│   ├── error.html             Generic error page
│   └── report.html.j2         Self-contained HTML report (preset)
│
├── static/
│   └── style.css              Global stylesheet (no external dependencies)
│
├── prompts/                   AI instruction files (plain text)
│   ├── vehicles_ad.txt
│   ├── vehicles_summary.txt
│   ├── realestate_ad.txt
│   └── realestate_summary.txt
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py    Creates all tables
│
├── tests/
│   ├── conftest.py            Shared fixtures (SQLite DB, test client, mocks)
│   ├── test_schemas.py
│   ├── test_ai_client.py
│   ├── test_report_builder.py
│   ├── test_crud.py
│   ├── test_routes.py
│   └── test_tasks.py
│
├── packaging/
│   ├── adagent.spec           RPM spec
│   ├── adagent.service        systemd unit
│   ├── adagent-migrate.service  oneshot migration unit
│   ├── adagent-nginx.conf     nginx virtual host (with rate limiting on /analyze)
│   ├── adagent-logrotate      Log rotation config
│   ├── adagent-ssl_all_certs_renew.service
│   ├── adagent-ssl_all_certs_renew.timer  Twice-daily certbot renewal
│   └── env                    Secrets file template
│
└── .github/
    └── workflows/
        └── build-rpm.yml      Builds RPM on every v* tag push
```

---

## Operational runbook

### Service management

```bash
systemctl status adagent
systemctl restart adagent
systemctl stop adagent

# Live logs
journalctl -u adagent -f

# Application logs
tail -f /var/log/adagent/adagent.log

# nginx logs
tail -f /var/log/adagent/nginx_access.log
tail -f /var/log/adagent/nginx_error.log
```

### Database access

```bash
mysql -u adagent -p adagent
```

```sql
-- Check recent reports
SELECT id, status, ads_found, ads_analyzed, is_limited, created_at
FROM reports ORDER BY created_at DESC LIMIT 20;

-- Check active subscriptions
SELECT u.email, s.status, s.plan_period, s.current_period_end
FROM subscriptions s JOIN users u ON u.id = s.user_id
WHERE s.status = 'active';

-- Manually fail a stuck report
UPDATE reports SET status='failed', error_message='Manual reset'
WHERE id='<report_id>' AND status='running';
```

### Run migrations manually

```bash
cd /opt/adagent
sudo -u adagent /opt/adagent/venv/bin/alembic upgrade head
```

### Renew TLS certificate manually

```bash
certbot renew --deploy-hook "systemctl reload nginx"
```

### Health check endpoint

```bash
curl https://adagent.dimosense.com/health
# {"status":"ok","version":"0.1.0"}
```
