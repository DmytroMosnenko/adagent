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
    AWS_SES_FROM_EMAIL: str = "noreply@dimosense.com"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── OpenAI ─────────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_AD_MODEL: str = "gpt-4o-mini"
    OPENAI_SUMMARY_MODEL: str = "gpt-4o-mini"
    OPENAI_AD_MAX_TOKENS: int = 1024
    OPENAI_SUMMARY_MAX_TOKENS: int = 1024

    # ── Auth ───────────────────────────────────────────────────────────────────
    SESSION_TTL_DAYS: int = 7
    MAGIC_LINK_TTL_MINUTES: int = 15
    SECRET_KEY: str = "change-me-in-production"  # for HMAC if needed

    # ── Freemium ───────────────────────────────────────────────────────────────
    FREE_ADS_LIMIT: int = 5

    # ── Storage ────────────────────────────────────────────────────────────────
    REPORT_STORAGE_PATH: str = "/opt/adagent/report_storage"

    # ── Playwright ─────────────────────────────────────────────────────────────
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_BROWSERS_PATH: str = "/opt/adagent/.playwright"

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
    def stripe_prices(self) -> dict[str, str]:
        """Returns {period: price_id} dict parsed from STRIPE_PRICES_JSON."""
        return json.loads(self.STRIPE_PRICES_JSON)


settings = Settings()
