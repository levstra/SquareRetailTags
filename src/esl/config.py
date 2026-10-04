from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    # Owner/superuser connection for migrations and admin tasks. The web app
    # never uses it; DATABASE_URL must be a login without BYPASSRLS.
    admin_database_url: str | None = None
    # Single-business mode until logins exist (rollout step 2).
    default_business_id: str = "00000000-0000-0000-0000-000000000001"
    redis_url: str = "redis://localhost:6379/0"
    square_token: str
    square_environment: str = "SANDBOX"
    square_api_version: str = "2026-05-20"
    square_webhook_signature_key: str = ""
    square_webhook_url: str = ""
    # Cognito user pool for dashboard sign-in (rollout step 2). Empty = sign-in
    # not configured; authenticated endpoints answer 503.
    cognito_region: str = ""
    cognito_user_pool_id: str = ""
    cognito_app_client_id: str = ""


settings = Settings()
