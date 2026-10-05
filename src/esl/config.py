from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    # Owner/superuser connection for migrations and admin tasks. The web app
    # never uses it; DATABASE_URL must be a login without BYPASSRLS.
    admin_database_url: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    square_environment: str = "SANDBOX"
    square_api_version: str = "2026-05-20"
    square_webhook_signature_key: str = ""
    square_webhook_url: str = ""
    # Square OAuth app credentials (rollout step 3). Sandbox values in dev.
    square_application_id: str = ""
    square_application_secret: str = ""
    # Fernet key (urlsafe base64, 32 bytes) encrypting stored Square tokens.
    token_encryption_key: str = ""
    # Where the dashboard is served; the OAuth redirect URL is built from it.
    public_base_url: str = "http://localhost:5173"
    # In-process scheduled jobs (reconciliation sync, token refresh).
    jobs_enabled: bool = True
    # Cognito user pool for dashboard sign-in (rollout step 2). Empty = sign-in
    # not configured; authenticated endpoints answer 503.
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""


settings = Settings()
