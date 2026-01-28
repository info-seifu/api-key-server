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


# 新形式: 分離設定用モデルクラス
class ProviderApiKey(BaseModel):
    """APIキー設定（プロバイダ単位）"""
    api_key: str
    base_url: Optional[str] = None


class ProviderModelConfig(BaseModel):
    """モデル設定（APIキーなし）"""
    models: List[str] = Field(default_factory=list)


class ProductModelConfig(BaseModel):
    """プロダクトのモデル設定（APIキーなし）"""
    providers: Dict[str, ProviderModelConfig] = Field(default_factory=dict)


# 既存形式: 一体型設定用モデルクラス（後方互換性）
class ProviderConfig(BaseModel):
    """Configuration for a single AI provider."""
    api_key: Optional[str] = None  # Optional for new separated format
    models: List[str] = Field(default_factory=list)
    base_url: Optional[str] = None


class ProductConfig(BaseModel):
    """Configuration for a product with multiple providers."""
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)

    def get_provider_for_model(self, model: str) -> Optional[tuple[str, ProviderConfig]]:
        """Find the provider that supports the given model."""
        for provider_name, provider_config in self.providers.items():
            if not provider_config.models or model in provider_config.models:
                return (provider_name, provider_config)
        return None

    @classmethod
    def from_separated_configs(
        cls,
        model_config: ProductModelConfig,
        api_keys: Dict[str, ProviderApiKey]
    ) -> "ProductConfig":
        """Create ProductConfig by merging model config and API keys."""
        providers = {}
        for provider_name, provider_model_config in model_config.providers.items():
            if provider_name in api_keys:
                api_key_config = api_keys[provider_name]
                providers[provider_name] = ProviderConfig(
                    api_key=api_key_config.api_key,
                    models=provider_model_config.models,
                    base_url=api_key_config.base_url
                )
            else:
                logger.warning(f"API key not found for provider: {provider_name}")
        return cls(providers=providers)


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

    # New multi-provider configuration (unified format)
    product_configs: Dict[str, ProductConfig] = Field(default_factory=dict)

    # New separated configuration (models and API keys separated)
    provider_api_keys: Dict[str, ProviderApiKey] = Field(default_factory=dict)
    product_model_configs: Dict[str, ProductModelConfig] = Field(default_factory=dict)

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
    secret_provider_api_keys_name: str = "provider-api-keys"

    # Configuration file paths (for local development)
    product_models_file: str = "config/product-models.json"
    api_keys_file: str = "config/api-keys.json"

    class Config:
        env_prefix = "API_KEY_SERVER_"
        env_file = ".env"

    def get_merged_product_configs(self) -> Dict[str, ProductConfig]:
        """
        Get product configs by merging separated configurations.
        Falls back to existing product_configs if separated configs not available.
        """
        # If new separated format is available, use it
        if self.product_model_configs and self.provider_api_keys:
            logger.debug("Using separated model configs and API keys")
            merged = {}
            for product_id, model_config in self.product_model_configs.items():
                merged[product_id] = ProductConfig.from_separated_configs(
                    model_config,
                    self.provider_api_keys
                )
            return merged

        # Fallback to existing unified format
        return self.product_configs

    def get_allowed_models_for_product(self, product_id: str) -> List[str]:
        """Get all allowed models for a given product."""
        # Try merged configs first (includes both separated and unified formats)
        merged_configs = self.get_merged_product_configs()
        if product_id in merged_configs:
            config = merged_configs[product_id]
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

    @validator("provider_api_keys", pre=True)
    def _parse_provider_api_keys(cls, value: object) -> Dict[str, ProviderApiKey]:
        """Load provider API keys from Secret Manager or local file."""
        if should_use_secret_manager() and not value:
            logger.info("Loading provider_api_keys from Secret Manager")
            project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
            secret_name = os.environ.get(
                "API_KEY_SERVER_SECRET_PROVIDER_API_KEYS_NAME",
                "provider-api-keys"
            )
            try:
                raw_data = load_secret_as_dict(secret_name, project_id)
                return {k: ProviderApiKey(**v) for k, v in raw_data.items()}
            except Exception as e:
                logger.info(f"provider-api-keys not found in Secret Manager: {e}")
                return {}

        # Local file fallback
        if not value:
            api_keys_file = os.environ.get("API_KEY_SERVER_API_KEYS_FILE", "config/api-keys.json")
            if os.path.exists(api_keys_file):
                logger.info(f"Loading provider_api_keys from file: {api_keys_file}")
                with open(api_keys_file, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                return {k: ProviderApiKey(**v) for k, v in raw_data.items()}
            return {}

        if isinstance(value, dict):
            return {k: ProviderApiKey(**v) if isinstance(v, dict) else v for k, v in value.items()}
        if isinstance(value, str) and value.strip():
            raw_data = json.loads(value)
            return {k: ProviderApiKey(**v) for k, v in raw_data.items()}
        return {}

    @validator("product_model_configs", pre=True)
    def _parse_product_model_configs(cls, value: object) -> Dict[str, ProductModelConfig]:
        """Load product model configs from file."""
        if not value:
            # Try loading from local file first
            models_file = os.environ.get("API_KEY_SERVER_PRODUCT_MODELS_FILE", "config/product-models.json")
            if os.path.exists(models_file):
                logger.info(f"Loading product_model_configs from file: {models_file}")
                with open(models_file, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                return {k: ProductModelConfig(**v) for k, v in raw_data.items()}

            # Optionally check Secret Manager
            if should_use_secret_manager():
                project_id = os.environ.get("API_KEY_SERVER_GCP_PROJECT_ID")
                secret_name = os.environ.get(
                    "API_KEY_SERVER_SECRET_PRODUCT_MODELS_NAME",
                    "product-models"
                )
                try:
                    raw_data = load_secret_as_dict(secret_name, project_id)
                    return {k: ProductModelConfig(**v) for k, v in raw_data.items()}
                except Exception:
                    logger.info("product-models not found in Secret Manager")
            return {}

        if isinstance(value, dict):
            return {k: ProductModelConfig(**v) if isinstance(v, dict) else v for k, v in value.items()}
        if isinstance(value, str) and value.strip():
            raw_data = json.loads(value)
            return {k: ProductModelConfig(**v) for k, v in raw_data.items()}
        return {}

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
