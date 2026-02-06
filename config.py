"""
Shared configuration and client factory for all workflow demos.
Loads Azure AI Foundry credentials from .env and provides a reusable client builder.
"""

import os
from dotenv import load_dotenv

load_dotenv()

FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
FOUNDRY_MODEL_DEPLOYMENT_NAME = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o")


def validate_config() -> bool:
    """Return True if required environment variables are set."""
    if not FOUNDRY_PROJECT_ENDPOINT or "<your-" in FOUNDRY_PROJECT_ENDPOINT:
        return False
    return True
