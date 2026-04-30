import logging
import math
import re
from datetime import datetime
from typing import Optional
import uuid
from zoneinfo import ZoneInfo


from open_webui.utils.misc import get_last_user_message, get_messages_content

from open_webui.env import SRC_LOG_LEVELS
from open_webui.config import DEFAULT_RAG_TEMPLATE

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def get_task_model_id(
    default_model_id: str, task_model: str, task_model_external: str, models
) -> str:
    """
    Get the model ID for background tasks like title and tag generation.
    Fixed to prevent KeyError crashes that cause Kubernetes pod restarts.
    """
    # Set the task model
    task_model_id = default_model_id

    # CRITICAL FIX: Check if models dict exists and is not empty
    if not models or not isinstance(models, dict):
        # If no models available, return the default anyway to prevent KeyError
        return default_model_id

    # Check if the default model exists in the models dict
    if task_model_id not in models:
        # If default model doesn't exist, find the first available model
        if models:
            task_model_id = next(iter(models.keys()))
        else:
            # If no models available, return the default anyway
            return default_model_id

    # Check if the user has a custom task model and use that model
    # Add safety check to ensure model exists before accessing its properties
    if task_model_id in models and models[task_model_id].get("owned_by") == "ollama":
        if task_model and task_model in models:
            task_model_id = task_model
    else:
        if task_model_external and task_model_external in models:
            task_model_id = task_model_external

    return task_model_id


def prompt_template(
    template: str, user_name: Optional[str] = None, user_location: Optional[str] = None
) -> str:
    # Get the current date/time in Eastern Time zone (handles EST/EDT automatically)
    eastern_tz = ZoneInfo("America/New_York")
    current_date = datetime.now(eastern_tz)

    # Format the date to YYYY-MM-DD
    formatted_date = current_date.strftime("%Y-%m-%d")
    formatted_time = current_date.strftime("%I:%M:%S %p %Z")
    formatted_weekday = current_date.strftime("%A")

    template = template.replace("{{CURRENT_DATE}}", formatted_date)
    template = template.replace("{{CURRENT_TIME}}", formatted_time)
    template = template.replace(
        "{{CURRENT_DATETIME}}", f"{formatted_date} {formatted_time}"
    )
    template = template.replace("{{CURRENT_WEEKDAY}}", formatted_weekday)

    if user_name:
        # Replace {{USER_NAME}} in the template with the user's name
        template = template.replace("{{USER_NAME}}", user_name)
    else:
        # Replace {{USER_NAME}} in the template with "Unknown"
        template = template.replace("{{USER_NAME}}", "Unknown")

    if user_location:
        # Replace {{USER_LOCATION}} in the template with the current location
        template = template.replace("{{USER_LOCATION}}", user_location)
    else:
        # Replace {{USER_LOCATION}} in the template with "Unknown"
        template = template.replace("{{USER_LOCATION}}", "Unknown")

    return template


def replace_prompt_variable(template: str, prompt: str) -> str:
    def replacement_function(match):
        full_match = match.group(
            0
        ).lower()  # Normalize to lowercase for consistent handling
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)

        if full_match == "{{prompt}}":
            return prompt
        elif start_length is not None:
            return prompt[: int(start_length)]
        elif end_length is not None:
            return prompt[-int(end_length) :]
        elif middle_length is not None:
            middle_length = int(middle_length)
            if len(prompt) <= middle_length:
                return prompt
            start = prompt[: math.ceil(middle_length / 2)]
            end = prompt[-math.floor(middle_length / 2) :]
            return f"{start}...{end}"
        return ""

    # Updated regex pattern to make it case-insensitive with the `(?i)` flag
    pattern = r"(?i){{prompt}}|{{prompt:start:(\d+)}}|{{prompt:end:(\d+)}}|{{prompt:middletruncate:(\d+)}}"
    template = re.sub(pattern, replacement_function, template)
    return template


def extract_title_from_response(resp: dict) -> Optional[str]:
    """Extract raw content from LLM response."""
    if not isinstance(resp, dict):
        return None

    try:
        choices = resp.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message") or first.get("text") or {}
                if isinstance(msg, dict):
                    content = msg.get("content", "") or msg.get("text", "")
                elif isinstance(first.get("text"), str):
                    content = first.get("text")
                else:
                    return None
                return content.strip() if content else None
    except Exception:
        return None

    return None


def truncate_title_by_chars(title: str, max_chars: int = 50) -> str:
    """
    Truncate title to max_chars while preserving whole words.
    If truncation would break a word, keeps the whole word even if it slightly exceeds the limit.
    Preserves leading emoji if present.
    """
    if not title or len(title) <= max_chars:
        return title

    # Check if first token is emoji/non-alphanumeric
    tokens = title.split()
    if not tokens:
        return title

    leading_emoji = []
    word_tokens = []

    for i, token in enumerate(tokens):
        if i == 0 and not any(c.isalnum() for c in token):
            leading_emoji.append(token)
        else:
            word_tokens.append(token)

    # Calculate available space for words
    emoji_part = " ".join(leading_emoji)
    emoji_length = len(emoji_part) + (
        1 if emoji_part else 0
    )  # +1 for space after emoji
    available_chars = max_chars - emoji_length

    # Build truncated title
    result_words = []
    current_length = 0

    for word in word_tokens:
        word_length = len(word)
        space_length = 1 if result_words else 0  # Space before word

        # If adding this word would exceed limit
        if current_length + space_length + word_length > available_chars:
            # If we have at least one word, stop here
            if result_words:
                break
            # If this is the first word and it's longer than available space,
            # include it anyway to preserve whole word
            result_words.append(word)
            break

        result_words.append(word)
        current_length += space_length + word_length

    truncated = " ".join(result_words)
    return (
        (" ".join(leading_emoji) + " " + truncated).strip()
        if leading_emoji
        else truncated
    )


def replace_messages_variable(
    template: str, messages: Optional[list[str]] = None, filter_reasoning: bool = False
) -> str:
    def replacement_function(match):
        full_match = match.group(0)
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)
        # If messages is None, handle it as an empty list
        if messages is None:
            return ""

        # Process messages based on the number of messages required
        if full_match == "{{MESSAGES}}":
            return get_messages_content(messages, filter_reasoning)
        elif start_length is not None:
            return get_messages_content(messages[: int(start_length)], filter_reasoning)
        elif end_length is not None:
            return get_messages_content(messages[-int(end_length) :], filter_reasoning)
        elif middle_length is not None:
            mid = int(middle_length)

            if len(messages) <= mid:
                return get_messages_content(messages, filter_reasoning)
            # Handle middle truncation: split to get start and end portions of the messages list
            half = mid // 2
            start_msgs = messages[:half]
            end_msgs = messages[-half:] if mid % 2 == 0 else messages[-(half + 1) :]
            formatted_start = get_messages_content(start_msgs, filter_reasoning)
            formatted_end = get_messages_content(end_msgs, filter_reasoning)
            return f"{formatted_start}\n{formatted_end}"
        return ""

    template = re.sub(
        r"{{MESSAGES}}|{{MESSAGES:START:(\d+)}}|{{MESSAGES:END:(\d+)}}|{{MESSAGES:MIDDLETRUNCATE:(\d+)}}",
        replacement_function,
        template,
    )

    return template


# {{prompt:middletruncate:8000}}


def rag_template(template: str, context: str, query: str):
    if template.strip() == "":
        template = DEFAULT_RAG_TEMPLATE

    if "[context]" not in template and "{{CONTEXT}}" not in template:
        log.debug(
            "WARNING: The RAG template does not contain the '[context]' or '{{CONTEXT}}' placeholder."
        )

    if "<context>" in context and "</context>" in context:
        log.debug(
            "WARNING: Potential prompt injection attack: the RAG "
            "context contains '<context>' and '</context>'. This might be "
            "nothing, or the user might be trying to hack something."
        )

    query_placeholders = []
    if "[query]" in context:
        query_placeholder = "{{QUERY" + str(uuid.uuid4()) + "}}"
        template = template.replace("[query]", query_placeholder)
        query_placeholders.append(query_placeholder)

    if "{{QUERY}}" in context:
        query_placeholder = "{{QUERY" + str(uuid.uuid4()) + "}}"
        template = template.replace("{{QUERY}}", query_placeholder)
        query_placeholders.append(query_placeholder)

    template = template.replace("[context]", context)
    template = template.replace("{{CONTEXT}}", context)
    template = template.replace("[query]", query)
    template = template.replace("{{QUERY}}", query)

    for query_placeholder in query_placeholders:
        template = template.replace(query_placeholder, query)

    return template


def title_generation_template(
    template: str, messages: list[dict], user: Optional[dict] = None
) -> str:
    # Filter reasoning content from messages for title generation
    prompt = get_last_user_message(messages, filter_reasoning=True)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages, filter_reasoning=True)

    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )

    return template


def tags_generation_template(
    template: str, messages: list[dict], user: Optional[dict] = None
) -> str:
    # Filter reasoning content from messages for tags generation
    prompt = get_last_user_message(messages, filter_reasoning=True)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages, filter_reasoning=True)

    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )
    return template


def image_prompt_generation_template(
    template: str, messages: list[dict], user: Optional[dict] = None
) -> str:
    # Filter reasoning content from messages for image prompt generation
    prompt = get_last_user_message(messages, filter_reasoning=True)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages, filter_reasoning=True)

    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )
    return template


def emoji_generation_template(
    template: str, prompt: str, user: Optional[dict] = None
) -> str:
    template = replace_prompt_variable(template, prompt)
    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )

    return template


def autocomplete_generation_template(
    template: str,
    prompt: str,
    messages: Optional[list[dict]] = None,
    type: Optional[str] = None,
    user: Optional[dict] = None,
) -> str:
    template = template.replace("{{TYPE}}", type if type else "")
    template = replace_prompt_variable(template, prompt)
    # Filter reasoning content from messages for autocomplete generation
    template = replace_messages_variable(template, messages, filter_reasoning=True)

    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )
    return template


def query_generation_template(
    template: str, messages: list[dict], user: Optional[dict] = None
) -> str:
    # Filter reasoning content from messages for query generation
    prompt = get_last_user_message(messages, filter_reasoning=True)
    template = replace_prompt_variable(template, prompt)
    template = replace_messages_variable(template, messages, filter_reasoning=True)

    template = prompt_template(
        template,
        **(
            {"user_name": user.get("name"), "user_location": user.get("location")}
            if user
            else {}
        ),
    )
    return template


def moa_response_generation_template(
    template: str, prompt: str, responses: list[str]
) -> str:
    def replacement_function(match):
        full_match = match.group(0)
        start_length = match.group(1)
        end_length = match.group(2)
        middle_length = match.group(3)

        if full_match == "{{prompt}}":
            return prompt
        elif start_length is not None:
            return prompt[: int(start_length)]
        elif end_length is not None:
            return prompt[-int(end_length) :]
        elif middle_length is not None:
            middle_length = int(middle_length)
            if len(prompt) <= middle_length:
                return prompt
            start = prompt[: math.ceil(middle_length / 2)]
            end = prompt[-math.floor(middle_length / 2) :]
            return f"{start}...{end}"
        return ""

    template = re.sub(
        r"{{prompt}}|{{prompt:start:(\d+)}}|{{prompt:end:(\d+)}}|{{prompt:middletruncate:(\d+)}}",
        replacement_function,
        template,
    )

    responses = [f'"""{response}"""' for response in responses]
    responses = "\n\n".join(responses)

    template = template.replace("{{responses}}", responses)
    return template


def tools_function_calling_generation_template(template: str, tools_specs: str) -> str:
    template = template.replace("{{TOOLS}}", tools_specs)
    return template
