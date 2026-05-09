from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # AWS
    aws_region: str = "us-east-1"

    # DynamoDB table names — must match the streaming Lambda
    dynamo_messages_table: str = "chatbot_messages"
    dynamo_branches_table: str = "chatbot_branches"
    dynamo_sessions_table: str = "chatbot_sessions"

    # Cognito
    cognito_user_pool_id: str = ""           # e.g. us-east-1_XXXXXXXXX
    cognito_client_id: str = ""              # app client id
    cognito_region: str = "us-east-1"

    # CORS — set to your frontend domain in production
    cors_origins: str = "http://localhost:3000, http://52.6.73.220"  # comma-separated list

    # Pagination
    default_page_size: int = 20
    max_page_size: int = 100

    class Config:
        env_file = ".env"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @property
    def cognito_jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com"
            f"/{self.cognito_user_pool_id}/.well-known/jwks.json"
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
