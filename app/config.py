from pydantic import Field, field_validator, ValidationError
from pydantic_settings import BaseSettings
from functools import lru_cache

# DynamoDB table names (fixed for this application)
DYNAMO_MESSAGES_TABLE = "chatbot_messages"
DYNAMO_BRANCHES_TABLE = "chatbot_branches"
DYNAMO_SESSIONS_TABLE = "chatbot_sessions"


class Settings(BaseSettings):
    # AWS
    aws_region: str = Field(default="us-east-1")

    # Cognito — REQUIRED in all environments
    cognito_user_pool_id: str = Field(
        description="User pool ID (required for JWT verification). Format: us-east-1_XXXXXXXXX"
    )
    cognito_client_id: str = Field(
        description="Cognito client ID (required for token validation)"
    )
    cognito_region: str = Field(default="us-east-1")

    # CORS — set to your frontend domain in production
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Comma-separated list of allowed origins"
    )

    # Pagination
    default_page_size: int = Field(default=20, ge=1, le=100)
    max_page_size: int = Field(default=100, ge=1, le=1000)

    class Config:
        env_file = ".env"

    @field_validator("cognito_user_pool_id")
    @classmethod
    def validate_cognito_pool_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "COGNITO_USER_POOL_ID is required and cannot be empty. "
                "Set it in your .env or Lambda environment variables."
            )
        if not v.startswith("us-") and "_" not in v:
            raise ValueError(
                f"COGNITO_USER_POOL_ID looks invalid: {v}. "
                f"Expected format like 'us-east-1_XXXXXXXXX'"
            )
        return v.strip()

    @field_validator("cognito_client_id")
    @classmethod
    def validate_cognito_client_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "COGNITO_CLIENT_ID is required and cannot be empty. "
                "Set it in your .env or Lambda environment variables."
            )
        return v.strip()

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("CORS_ORIGINS cannot be empty")
        # Parse and validate each origin
        origins = [o.strip() for o in v.split(",") if o.strip()]
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one valid origin")
        for origin in origins:
            if not origin.startswith(("http://", "https://")):
                raise ValueError(
                    f"Invalid CORS origin: {origin}. Must start with http:// or https://"
                )
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cognito_jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com"
            f"/{self.cognito_user_pool_id}/.well-known/jwks.json"
        )


@lru_cache()
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        # Log all validation errors clearly
        error_msg = "\n".join([
            f"  • {error['loc'][0]}: {error['msg']}"
            for error in e.errors()
        ])
        raise RuntimeError(
            f"Configuration error:\n{error_msg}\n"
            f"Check your .env file or Lambda environment variables."
        ) from e