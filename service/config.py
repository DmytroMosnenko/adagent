import json
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="secrets/env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DEBUG: bool = False
    APP_VERSION: str = "0.1.0"
    APP_BASE_URL: str = "https://adagent.dimosense.com"

    # ── MariaDB ────────────────────────────────────────────────────────────────
    DB_USER: str = "adagent"
    DB_PASSWORD: str = "localpassword"
    DB_HOST: str = "localhost:3306"
    DB_NAME: str = "adagent"
    DB_CHARSET: str = "utf8mb4"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 300
    DB_POOL_PRE_PING: bool = True
    ALEMBIC_VERSION_TABLE_NAME: str = "adagent_alembic_version"

    # ── Stripe ─────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # JSON map: period → Stripe price_id
    # e.g. '{"monthly":"price_xxx","weekly":"price_yyy","daily":"price_zzz"}'
    STRIPE_PRICES_JSON: str = '{"monthly": ""}'
    STRIPE_SUCCESS_URL: str = "https://adagent.dimosense.com/subscribe/success"
    STRIPE_CANCEL_URL: str = "https://adagent.dimosense.com/subscribe"

    # ── Email backend ──────────────────────────────────────────────────────────
    # "smtp" → local/remote Postfix via SMTP  |  "ses" → AWS SES
    EMAIL_BACKEND: str = "smtp"
    EMAIL_FROM: str = "noreply-adagent@dimosense.com"

    # ── SMTP (Postfix on the same Hetzner box, or any relay) ───────────────────
    # Postfix typically listens on localhost:25 (no auth, no TLS needed for
    # loopback) or localhost:587 with STARTTLS if you configured submission.
    # Leave SMTP_USERNAME / SMTP_PASSWORD empty for unauthenticated relay.
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 25
    SMTP_USE_STARTTLS: bool = False   # set True + port 587 for submission
    SMTP_USE_SSL: bool = False         # set True + port 465 for SMTPS
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TIMEOUT: int = 10             # seconds

    # ── AWS SES ────────────────────────────────────────────────────────────────
    AWS_REGION: str = "eu-west-1"
    AWS_SES_FROM_EMAIL: str = "noreply-adagent@dimosense.com"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_AD_MODEL: str = "gpt-5-nano"
    OPENAI_SUMMARY_MODEL: str = "gpt-5-mini"
    OPENAI_AD_MAX_TOKENS: int|None = None
    OPENAI_SUMMARY_MAX_TOKENS: int|None = None

    # ── Auth ───────────────────────────────────────────────────────────────────
    SESSION_TTL_DAYS: int = 7
    MAGIC_LINK_TTL_MINUTES: int = 15
    SECRET_KEY: str = "change-me-in-production"  # for HMAC if needed

    # ── Freemium ───────────────────────────────────────────────────────────────
    FREE_ADS_LIMIT: int = 5
    # Abuse / budget guard for anonymous & free users (paid traffic makes this matter).
    # Counted per client (keyed hash of the IP, see adagent_server._client_hash) over
    # a rolling 24 h. 0 disables a limit. Subscribers are never limited.
    # Keep the per-client value generous: Polish mobile carriers put many users
    # behind one IPv4 address (CGNAT).
    FREE_REPORTS_PER_CLIENT_PER_DAY: int = 10
    FREE_REPORTS_GLOBAL_PER_DAY: int = 100

    # ── Price shown in the UI ──────────────────────────────────────────────────
    # DISPLAY ONLY — what people are actually charged comes from the Stripe price.
    # Set PRICE_LABEL_MONTHLY_PL together with STRIPE_CHECKOUT_CURRENCY_PL, and only
    # after the Stripe price has a matching PLN entry in currency_options.
    PRICE_LABEL_MONTHLY: str = "$9.99"
    STRIPE_CHECKOUT_CURRENCY_USD: str = "USD"    # e.g. "usd" (forces USD for non Polish-UI checkouts)
    PRICE_LABEL_MONTHLY_PL: str = "39,99 zł"     # e.g. "39,99 zł"
    STRIPE_CHECKOUT_CURRENCY_PL: str = "PLN"     # e.g. "pln" (forces PLN for Polish-UI checkouts)
    # Show an explicit "I accept the Terms / start immediately, losing withdrawal right"
    # checkbox in Checkout. Requires the Terms URL to be set in the Stripe Dashboard
    # (Settings → Public details) — Stripe rejects the session otherwise.
    STRIPE_REQUIRE_TOS_CONSENT: bool = False
    # Let business buyers enter a NIP / VAT ID in Checkout (for a faktura VAT).
    STRIPE_COLLECT_TAX_ID: bool = False

    # ── Google Ads / GA4 (all optional — nothing is rendered until an ID is set) ─
    GOOGLE_ADS_ID: str = ""                       # "AW-1234567890"
    GOOGLE_ADS_LABEL_PURCHASE: str = ""           # conversion label of the "Purchase" action
    GOOGLE_ADS_LABEL_ANALYSIS_STARTED: str = ""   # conversion label of the "Analysis started" action
    GA4_MEASUREMENT_ID: str = ""                  # "G-XXXXXXXXXX"
    # False = gtag.js is loaded only after the visitor clicks "Accept" (Consent Mode "basic").
    # True  = gtag.js loads immediately with all storage denied and sends cookieless pings
    #         (Consent Mode "advanced": better conversion modelling, weaker privacy posture).
    GOOGLE_CONSENT_ADVANCED: bool = False

    # Optional link to a finished report that visitors can open as an example
    # (generate one, then set e.g. https://adagent.dimosense.com/report/<id>).
    SAMPLE_REPORT_URL: str = ""

    # ── Funnel tracking / admin reporting ───────────────────────────────────────
    # First-party, cookieless landing-page funnel (service/funnel.py, static/funnel.js).
    FUNNEL_ENABLED: bool = True
    FUNNEL_RETENTION_DAYS: int = 90          # rows older than this are purged by the daily report job
    REPORT_TIMEZONE: str = "Europe/Warsaw"   # "a day" in the daily funnel report
    ADMIN_EMAIL: str = ""                    # receives the daily funnel report; empty = email disabled
    # Shared secret for /internal/* endpoints (sent as X-Internal-Token, loopback callers only).
    # Empty = those endpoints are disabled (404).
    INTERNAL_TOKEN: str = ""

    # ── Storage ────────────────────────────────────────────────────────────────
    REPORT_STORAGE_PATH: str = "/opt/adagent/report_storage"

    # ── Playwright ─────────────────────────────────────────────────────────────
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_BROWSERS_PATH: str = "/opt/adagent/.playwright"

    # ── Concurrency (global, fair-shared across all reports AND all uvicorn
    #    workers) ─────────────────────────────────────────────────────────────
    # True totals — your ScraperAPI plan's concurrent-thread cap, your OpenAI
    # concurrent-request budget. Arbitration happens in the standalone
    # concurrency_broker.py process (one per deployment, not per worker), so
    # these numbers don't need to be divided by worker count: whichever
    # worker actually has traffic gets access to the full limit.
    SCRAPER_MAX_CONCURRENT_TOTAL: int = 20   # ScraperAPI concurrent-thread plan limit
    OPENAI_MAX_CONCURRENT_TOTAL: int = 100   # OpenAI concurrent request budget

    # Unix domain socket the broker listens on and adagent_server workers
    # connect to. Same box only — this is not a network-reachable address.
    # /run/adagent/ is created by systemd's RuntimeDirectory=adagent on the
    # broker's own unit (see packaging/adagent-concurrency-broker.service) —
    # both units must load the same secrets/env so they agree on this path.
    CONCURRENCY_BROKER_SOCKET: str = "/run/adagent/concurrency.sock"

    # ── HTTP Proxy (optional) ───────────────────────────────────────────────────
    # OLX / Otomoto use CloudFront which blocks datacenter IPs (Hetzner, AWS…).
    # Set a residential or ISP proxy to route scraper traffic through a clean IP.
    # Format: "http://user:pass@host:port"  or leave empty to use direct connection.
    # Example providers: BrightData, Oxylabs, Smartproxy, ScraperApi (residential pool).
    SCRAPER_PROXY_ADDRESS: str = ""
    SCRAPER_PROXY_USERNAME: str = ""
    SCRAPER_PROXY_PASSWORD: str = ""
    SCRAPER_PROXY_IGNORE_HTTPS_ERRORS: bool = True

    @property
    def db_url_async(self) -> str:
        return (
            f"mysql+aiomysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def db_url_sync(self) -> str:
        """Used by Alembic (sync driver)."""
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}/{self.DB_NAME}?charset={self.DB_CHARSET}"
        )

    @property
    def tracking_enabled(self) -> bool:
        return bool(self.GOOGLE_ADS_ID or self.GA4_MEASUREMENT_ID)

    @property
    def google_ads_labels(self) -> dict[str, str]:
        return {
            "purchase": self.GOOGLE_ADS_LABEL_PURCHASE,
            "analysis_started": self.GOOGLE_ADS_LABEL_ANALYSIS_STARTED,
        }

    def price_label(self, lang: str | None = None) -> str:
        if lang == "pl" and self.PRICE_LABEL_MONTHLY_PL:
            return self.PRICE_LABEL_MONTHLY_PL
        return self.PRICE_LABEL_MONTHLY

    @property
    def stripe_prices(self) -> dict[str, str]:
        """Returns {period: price_id} dict parsed from STRIPE_PRICES_JSON."""
        return json.loads(self.STRIPE_PRICES_JSON)


settings = Settings()
