import re
from pydantic import Field, field_validator, ValidationError
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    aws_region: str = Field(default="us-east-1")

    cognito_user_pool_id: str = Field(
        description="User pool ID (required for JWT verification). Format: us-east-1_XXXXXXXXX"
    )
    cognito_client_id: str = Field(
        description="Cognito client ID (required for token validation)"
    )

    class Config:
        env_file = ".env"

    @field_validator("cognito_user_pool_id")
    @classmethod
    def validate_cognito_pool_id(cls, v: str) -> str:
        v = v.strip() if v else v
        if not v:
            raise ValueError(
                "COGNITO_USER_POOL_ID is required and cannot be empty. "
                "Set it in your .env or Lambda environment variables."
            )
        if not re.match(r'^[a-z]{2}-[a-z]+-\d+_\w+$', v):
            raise ValueError(
                f"COGNITO_USER_POOL_ID looks invalid: {v}. "
                f"Expected format like 'us-east-1_XXXXXXXXX'"
            )
        return v

    @field_validator("cognito_client_id")
    @classmethod
    def validate_cognito_client_id(cls, v: str) -> str:
        v = v.strip() if v else v
        if not v:
            raise ValueError(
                "COGNITO_CLIENT_ID is required and cannot be empty. "
                "Set it in your .env or Lambda environment variables."
            )
        return v

    demo_models_allowed_emails: str = Field(default="")

    @property
    def demo_allowed_emails(self) -> list[str]:
        return [e.strip() for e in self.demo_models_allowed_emails.split(",") if e.strip()]

    @property
    def cognito_jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self.aws_region}.amazonaws.com"
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