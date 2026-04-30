"""
Llama Guard Integration for Open WebUI

This module provides a simple interface to evaluate chat messages using
Llama Guard for content policy violation detection. It's designed to be
called from the middleware after a chat response is complete.

The evaluation is non-blocking and logs violations for audit purposes.
It does NOT block or modify user interactions.
"""

import os
import time
import json
import logging
import random
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# Module availability flag
LLAMA_GUARD_AVAILABLE = True

# ============================================================================
# CONFIGURATION
# ============================================================================


@dataclass
class LlamaGuardConfig:
    """Configuration for Llama Guard evaluation."""

    enabled: bool = field(
        default_factory=lambda: os.getenv("LLAMA_GUARD_ENABLED", "true").lower()
        == "true"
    )

    ollama_url: str = field(
        default_factory=lambda: os.getenv(
            "LLAMA_GUARD_OLLAMA_URL", "http://ollama:11434"
        )
    )

    model_name: str = field(
        default_factory=lambda: os.getenv("LLAMA_GUARD_MODEL_NAME", "llama-guard3:8b")
    )

    timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("LLAMA_GUARD_TIMEOUT", "30"))
    )

    sample_rate: float = field(
        default_factory=lambda: float(os.getenv("LLAMA_GUARD_SAMPLE_RATE", "1.0"))
    )

    log_safe_responses: bool = field(
        default_factory=lambda: os.getenv("LLAMA_GUARD_LOG_SAFE", "false").lower()
        == "true"
    )


# MLCommons Hazard Category Descriptions
HAZARD_CATEGORIES = {
    "S1": "Violent Crimes",
    "S2": "Non-Violent Crimes",
    "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation",
    "S5": "Defamation",
    "S6": "Specialized Advice",
    "S7": "Privacy",
    "S8": "Intellectual Property",
    "S9": "Indiscriminate Weapons",
    "S10": "Hate",
    "S11": "Suicide & Self-Harm",
    "S12": "Sexual Content",
    "S13": "Elections",
}


# Global config instance
_config: Optional[LlamaGuardConfig] = None


def get_config() -> LlamaGuardConfig:
    """Get or create the global config instance."""
    global _config
    if _config is None:
        _config = LlamaGuardConfig()
    return _config


# ============================================================================
# HELPERS
# ============================================================================


def should_evaluate(config: LlamaGuardConfig) -> bool:
    """Determine if this request should be evaluated based on sample rate."""
    if not config.enabled:
        return False

    if config.sample_rate >= 1.0:
        return True

    # randomly decided to evaluate or not
    return random.random() < config.sample_rate


def log_violation(
    result: Dict[str, Any],
    chat_id: Optional[str],
    user_id: Optional[str],
    user_email: Optional[str],
    model_id: Optional[str],
    config: LlamaGuardConfig,
):
    """Log a violation to stdout (captured by Docker)."""
    categories = result.get("violated_categories", [])
    category_details = [
        {
            "code": cat,
            "name": HAZARD_CATEGORIES.get(cat, cat),
        }
        for cat in categories
    ]

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "user_id": user_id,
        "user_email": user_email,
        "model_id": model_id,
        "llama_guard_model": config.model_name,
        "result": result.get("result"),
        "violated_categories": categories,
        "category_details": category_details,
        "duration_ms": result.get("metrics", {}).get("duration_ms"),
        "user_message": result.get("user_message"),
    }

    names = ", ".join(f"{c['code']}: {c['name']}" for c in category_details)
    user_info = f"{user_email}" if user_email else f"user_id={user_id}"
    log.warning(
        f"[LLAMA_GUARD_VIOLATION] user={user_info} categories=[{names}] chat_id={chat_id} model={model_id} | {json.dumps(log_entry)}"
    )


# ============================================================================
# MAIN EVALUATION FUNCTION
# ============================================================================


async def evaluate_with_llama_guard(
    user_message: str,
    config: LlamaGuardConfig,
) -> Dict[str, Any]:
    """
    Evaluate a user message using Llama Guard.

    Args:
        user_message: The user's input message
        config: Llama Guard configuration

    Returns:
        Dict with 'result', 'violated_categories', 'metrics', and message content
    """
    start_time = time.perf_counter()

    try:
        # Prepare message for Llama Guard evaluation (just the user message)
        messages = [
            {"role": "user", "content": user_message},
        ]

        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                f"{config.ollama_url}/api/chat",
                json={
                    "model": config.model_name,
                    "messages": messages,
                    "stream": False,
                },
            )

            duration_ms = (time.perf_counter() - start_time) * 1000

            if response.status_code != 200:
                return {
                    "result": "error",
                    "violated_categories": [],
                    "user_message": user_message,
                    "metrics": {
                        "duration_ms": duration_ms,
                        "error": f"Ollama returned status {response.status_code}",
                    },
                }

            data = response.json()
            llama_guard_response = data.get("message", {}).get("content", "").strip()

            # Parse response: "safe" or "unsafe\nS1,S2,..."
            lines = llama_guard_response.strip().split("\n")
            first_line = lines[0].strip().lower()

            if first_line == "safe":
                return {
                    "result": "safe",
                    "violated_categories": [],
                    "user_message": user_message,
                    "metrics": {"duration_ms": duration_ms},
                }
            elif first_line == "unsafe":
                violated = []
                if len(lines) > 1:
                    categories_line = lines[1].strip()
                    violated = [
                        cat.strip().upper()
                        for cat in categories_line.split(",")
                        if cat.strip()
                    ]
                return {
                    "result": "unsafe",
                    "violated_categories": violated,
                    "user_message": user_message,
                    "metrics": {"duration_ms": duration_ms},
                }
            else:
                # Unexpected format
                if "unsafe" in first_line:
                    return {
                        "result": "unsafe",
                        "violated_categories": [],
                        "user_message": user_message,
                        "metrics": {"duration_ms": duration_ms},
                    }
                return {
                    "result": "safe",
                    "violated_categories": [],
                    "user_message": user_message,
                    "metrics": {"duration_ms": duration_ms},
                }

    except httpx.TimeoutException:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return {
            "result": "error",
            "violated_categories": [],
            "user_message": user_message,
            "metrics": {
                "duration_ms": duration_ms,
                "error": f"Timeout after {config.timeout_seconds}s",
            },
        }
    except Exception as e:
        duration_ms = (time.perf_counter() - start_time) * 1000
        return {
            "result": "error",
            "violated_categories": [],
            "user_message": user_message,
            "metrics": {
                "duration_ms": duration_ms,
                "error": str(e),
            },
        }


async def evaluate_chat_for_violations(
    messages: List[Dict[str, Any]],
    chat_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    model_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Evaluate the latest user message for policy violations.

    This is the main entry point called from the middleware after a chat
    request is received.

    Args:
        messages: List of all messages in the conversation
        chat_id: The chat ID for logging
        user_id: The user ID for logging
        user_email: The user's email address for logging
        model_id: The model ID that will generate the response

    Returns:
        Evaluation result dict, or None if evaluation was skipped
    """
    config = get_config()

    # Check if we should evaluate this request
    if not should_evaluate(config):
        return None

    # Extract last user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role", "").lower() == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                user_message = " ".join(text_parts)
            else:
                user_message = content
            break

    if not user_message:
        return None

    # Perform evaluation (removed health check for performance)
    result = await evaluate_with_llama_guard(
        user_message=user_message,
        config=config,
    )

    # Only log unsafe violations
    if result["result"] == "unsafe":
        log_violation(result, chat_id, user_id, user_email, model_id, config)

    return result
