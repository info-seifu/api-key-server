from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings

from .secrets import load_secret_as_dict, should_use_secret_manager

logger = logging.getLogger("api-key-server.config")


class ProviderConfig(BaseModel):
    """Configuration for a single AI provider."""
    api_key: str
    models: List[str] = Field(default_factory=list)
    base_url: Optional[str] = None  # Optional custom endpoint


class ProductConfig(BaseModel):
    """Configuration for a product with multiple providers."""
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)

    def get_provider_for_model(self, model: str) -> Optional[tuple[str, ProviderConfig]]:
        """Find the provider that supports the given model."""
        for provider_name, provider_config in self.providers.items():
            if not provider_config.models or model in provider_config.models:
                return (provider_name, provider_config)
        return None


class RateLimitConfig(BaseModel):
    bucket_capacity: int = 10
    bucket_refill_per_second: float = 5.0
    daily_quota: int = 200_000


class Settings(BaseSettings):
    app_name: str = "api-key-server"
    allowed_models: List[str] = Field(default_factory=lambda: ["gpt-4o", "gpt-4o-mini", "gpt-3.5-turbo"])
    max_tokens: int = Field(
        default=8192,
        ge=1,
        le=200000,
        description="Maximum tokens for completion. Can be overridden by API_KEY_SERVER_MAX_TOKENS environment variable."
    )
    min_temperature: float = 0.0
    max_temperature: float = 1.0
    jwt_audience: str = "api-key-server"
    hmac_clock_tolerance_seconds: int = 300
    openai_base_url: str = "https://api.openai.com/v1/chat/completions"
    request_timeout_seconds: int = 90

    # Legacy configuration (backward compatibility)
    product_keys: Dict[str, str] = Field(default_factory=dict)

    # New multi-provider configuration
    product_configs: Dict[str, ProductConfig] = Field(default_factory=dict)

    jwt_public_keys: Dict[str, str] = Field(default_factory=dict)
    client_hmac_secrets: Dict[str, str] = Field(default_factory=dict)

    redis_url: Optional[str] = None
    redis_prefix: str = "api-key-server"

    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    # Secret Manager configuration
    gcp_project_id: Optional[str] = None
    secret_product_keys_name: str = "openai-api-keys"
    secret_jwt_keys_name: str = "jwt-public-keys"
    secret_hmac_secrets_name: str = "hmac-secrets"

    class Config:
        env_prefix = "API_KEY_SERVER_"
        env_file = ".env"

    def get_allowed_models_for_product(self, product_id: str) -> List[str]:
        """Get all allowed models for a given product from product_configs."""
        if product_id in self.product_configs:
            config = self.product_configs[product_id]
            models = []
            for provider_config in config.providers.values():
                models.extend(provider_config.models)
            return models
        # Fallback to legacy product_keys (single provider)
        if product_id in self.product_keys:
            return self.allowed_models
        return []

    @validator("product_configs", pre=True)
    def _parse_product_configs(cls, value: object) -> Dict[str, ProductConfig]:
        if should_use_secret_manager() and not value:
            logger.info("Loading product_configs from Secret Manager")
            project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
            secret_name = os.environ.get("API_KEY_SERVER_SECRET_PRODUCT_KEYS_NAME", "openai-api-keys")
            try:
                raw_data = load_secret_as_dict(secret_name, project_id)
                # Check if it's the new format (nested providers)
                if raw_data and any(isinstance(v, dict) and "providers" in v for v in raw_data.values()):
                    logger.info("Detected multi-provider configuration format")
                    return {k: ProductConfig(**v) for k, v in raw_data.items()}
                else:
                    logger.info("Legacy format detected, skipping product_configs")
                    return {}
            except Exception as e:
                logger.error(f"Failed to load product_configs from Secret Manager: {e}")
                raise

        if value is None:
            return {}
        if isinstance(value, dict):
            # Parse nested configuration
            return {k: ProductConfig(**v) if isinstance(v, dict) else v for k, v in value.items()}
        if isinstance(value, str) and value.strip():
            raw_data = json.loads(value)
            return {k: ProductConfig(**v) for k, v in raw_data.items()}
        return {}

    @validator("product_keys", pre=True)
    def _parse_json_dict(cls, value: object) -> Dict[str, str]:
        if should_use_secret_manager() and not value:
            logger.info("Loading product_keys from Secret Manager (legacy format)")
            project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
            secret_name = os.environ.get("API_KEY_SERVER_SECRET_PRODUCT_KEYS_NAME", "openai-api-keys")
            try:
                raw_data = load_secret_as_dict(secret_name, project_id)
                # Check if it's the new multi-provider format - if so, return empty dict
                if raw_data and any(isinstance(v, dict) and "providers" in v for v in raw_data.values()):
                    logger.info("Multi-provider format detected in product_keys validator, skipping")
                    return {}
                return raw_data
            except Exception as e:
                logger.error(f"Failed to load product_keys from Secret Manager: {e}")
                raise
        return cls._parse_dict_field(value)

    @validator("jwt_public_keys", pre=True)
    def _parse_jwt_keys(cls, value: object) -> Dict[str, str]:
        if should_use_secret_manager() and not value:
            logger.info("Loading jwt_public_keys from Secret Manager")
            project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
            secret_name = os.environ.get("API_KEY_SERVER_SECRET_JWT_KEYS_NAME", "jwt-public-keys")
            try:
                return load_secret_as_dict(secret_name, project_id)
            except Exception as e:
                logger.error(f"Failed to load jwt_public_keys from Secret Manager: {e}")
                raise
        return cls._parse_dict_field(value)

    @validator("client_hmac_secrets", pre=True)
    def _parse_hmac_secrets(cls, value: object) -> Dict[str, str]:
        if should_use_secret_manager() and not value:
            logger.info("Loading client_hmac_secrets from Secret Manager")
            project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
            secret_name = os.environ.get("API_KEY_SERVER_SECRET_HMAC_SECRETS_NAME", "hmac-secrets")
            try:
                return load_secret_as_dict(secret_name, project_id)
            except Exception as e:
                logger.error(f"Failed to load client_hmac_secrets from Secret Manager: {e}")
                raise
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
