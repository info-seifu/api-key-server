from __future__ import annotations

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger("api-key-server.secrets")


def get_secret_from_manager(secret_name: str, project_id: Optional[str] = None) -> str:
    """
    Fetch a secret from Google Cloud Secret Manager.

    Args:
        secret_name: Name of the secret (e.g., "openai-api-keys")
        project_id: GCP project ID. If None, uses the default project.

    Returns:
        The secret value as a string.

    Raises:
        Exception: If the secret cannot be retrieved.
    """
    try:
        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()

        if project_id is None:
            # Try to get project ID from environment
            project_id = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
            if not project_id:
                raise ValueError(
                    "Project ID not specified. Set GCP_PROJECT or GOOGLE_CLOUD_PROJECT environment variable, "
                    "or pass project_id parameter."
                )

        # Build the resource name (defaults to "latest" version)
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

        logger.info(f"Fetching secret from Secret Manager: {secret_name}")
        response = client.access_secret_version(request={"name": name})

        payload = response.payload.data.decode("UTF-8")
        logger.info(f"Successfully retrieved secret: {secret_name}")

        return payload

    except ImportError:
        logger.warning(
            "google-cloud-secret-manager not installed. "
            "Install it with: pip install google-cloud-secret-manager"
        )
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve secret {secret_name}: {e}")
        raise


def load_secret_as_dict(secret_name: str, project_id: Optional[str] = None) -> Dict[str, str]:
    """
    Load a secret from Secret Manager and parse it as JSON dictionary.

    Args:
        secret_name: Name of the secret
        project_id: GCP project ID

    Returns:
        Parsed JSON as a dictionary.
    """
    payload = get_secret_from_manager(secret_name, project_id)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse secret {secret_name} as JSON: {e}")
        raise ValueError(f"Secret {secret_name} is not valid JSON") from e


def should_use_secret_manager() -> bool:
    """
    Determine if Secret Manager should be used based on environment variables.

    Returns:
        True if USE_SECRET_MANAGER is set to "true" (case-insensitive).
    """
    return os.environ.get("USE_SECRET_MANAGER", "").lower() in ("true", "1", "yes")
