from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import BaseModel, BaseSettings, Field, validator


class RateLimitConfig(BaseModel):
    bucket_capacity: int = 10
    bucket_refill_per_second: float = 5.0
    daily_quota: int = 200_000


class Settings(BaseSettings):
    app_name: str = "api-key-server"
    allowed_models: List[str] = Field(default_factory=lambda: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"])
    max_tokens: int = 2048
    min_temperature: float = 0.0
    max_temperature: float = 1.0
    jwt_audience: str = "api-key-server"
    hmac_clock_tolerance_seconds: int = 300
    openai_base_url: str = "https://api.openai.com/v1/chat/completions"
    request_timeout_seconds: int = 30

    product_keys: Dict[str, str] = Field(default_factory=dict)
    jwt_public_keys: Dict[str, str] = Field(default_factory=dict)
    client_hmac_secrets: Dict[str, str] = Field(default_factory=dict)

    redis_url: Optional[str] = None
    redis_prefix: str = "api-key-server"

    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    class Config:
        env_prefix = "API_KEY_SERVER_"
        env_file = ".env"

    @validator("product_keys", pre=True)
    def _parse_json_dict(cls, value: object) -> Dict[str, str]:
        return cls._parse_dict_field(value)

    @validator("jwt_public_keys", pre=True)
    def _parse_jwt_keys(cls, value: object) -> Dict[str, str]:
        return cls._parse_dict_field(value)

    @validator("client_hmac_secrets", pre=True)
    def _parse_hmac_secrets(cls, value: object) -> Dict[str, str]:
        return cls._parse_dict_field(value)

    @staticmethod
    def _parse_dict_field(value: object) -> Dict[str, str]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            return json.loads(value)
        return {}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
