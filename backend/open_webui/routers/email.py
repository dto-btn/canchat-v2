import asyncio
import base64
import logging
from pathlib import Path
from typing import List, Optional

from azure.communication.email.aio import EmailClient
from azure.core.exceptions import HttpResponseError
from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from open_webui.config import (
    AZURE_EMAIL_CONNECTION_STRING,
    EMAIL_SENDER_ADDRESS,
    EMAIL_SUPPORT_RECIPIENTS,
)
from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.auth import get_verified_user
from open_webui.utils.rate_limit import RateLimiter

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["EMAIL"])

router = APIRouter()

EMAIL_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "email"

# Autoescape covers the .html template, so user input can't inject markup.
templates = Environment(
    loader=FileSystemLoader(EMAIL_TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)

# The logo goes in as an inline attachment the HTML references by cid:.
# Outlook blocks data: URIs and asks before loading remote images.
LOGO_CID = "canchat-logo"
LOGO_ATTACHMENT = {
    "name": "canchat-logo.png",
    "contentType": "image/png",
    "contentInBase64": base64.b64encode(
        (EMAIL_TEMPLATES_DIR / "canchat-logo.png").read_bytes()
    ).decode("utf-8"),
    "contentId": LOGO_CID,
}

# Azure caps a message at 10 MB including base64 overhead (~4/3), so keep the
# raw attachment total well under that.
MAX_ATTACHMENT_BYTES = 7 * 1024 * 1024
# Matches MAX_FILES in IssueModal.svelte and SuggestionModal.svelte.
MAX_FILES = 3
# Per text field; keeps the message well under the 10 MB limit and stops abuse.
MAX_TEXT_LENGTH = 20_000
EMAIL_POLLING_INTERVAL_SECONDS = 1
EMAIL_SEND_TIMEOUT_SECONDS = 60

# Issue reports and suggestions share one per-user budget, which protects the
# support inbox and the Azure sending quota from a user submitting in a loop.
email_rate_limiter = RateLimiter("support-email", limit=5, window_seconds=60 * 60)

# The forms only accept images. The filename and content type are whatever the
# client sends, so the type is decided from the file's leading bytes instead.
# SVG is deliberately excluded: it can carry scripts.
IMAGE_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


############################
# Helpers
############################


def build_email_body(
    title: str, subject: str, submitted_by: str, sections: List[tuple]
) -> tuple:
    """Render the plain-text and HTML bodies from (label, value) pairs.

    The submitter's address is rendered as a mailto link with a reply
    subject already filled in.
    """
    context = {
        "title": title,
        "subject": subject,
        "submitted_by": submitted_by,
        "sections": sections,
        "logo_cid": LOGO_CID,
    }
    plain_text = templates.get_template("form_submission.txt").render(context)
    html_body = templates.get_template("form_submission.html").render(context)
    return plain_text, html_body


def detect_image_type(content: bytes) -> Optional[str]:
    """Return the image MIME type from the file's leading bytes, or None."""
    for signature, content_type in IMAGE_SIGNATURES:
        if content.startswith(signature):
            return content_type
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def safe_filename(filename: Optional[str], index: int) -> str:
    """Drop any path and control characters from a client-supplied filename."""
    name = Path((filename or "").replace("\\", "/")).name
    name = "".join(char for char in name if char.isprintable()).strip()
    return name[:255] or f"attachment-{index}"


async def build_attachments(
    files: Optional[List[UploadFile]],
) -> tuple:
    """Return (attachments, omitted file names).

    Rejects the submission (400) if there are more than MAX_FILES files or any
    file is not a supported image. Azure Communication Services rejects a whole
    message over 10 MB (base64 included), so files that would push past the
    budget are left out rather than failing the submission.
    """
    files = files or []
    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You can only upload up to {MAX_FILES} files.",
        )

    attachments = []
    omitted = []
    total_bytes = 0

    for index, file in enumerate(files, start=1):
        name = safe_filename(file.filename, index)
        await file.seek(0)
        # Read at most one byte past the budget so an oversized upload is
        # never loaded into memory in full.
        content = await file.read(MAX_ATTACHMENT_BYTES + 1)

        if not content:
            log.warning(f"Skipping empty file: {name}")
            continue

        content_type = detect_image_type(content)
        if content_type is None:
            log.warning(f"Rejected attachment {name}: not a supported image")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PNG, JPEG, GIF and WebP images can be attached.",
            )

        if total_bytes + len(content) > MAX_ATTACHMENT_BYTES:
            log.warning(f"Omitting attachment {name}: over the email size limit")
            omitted.append(name)
            continue

        total_bytes += len(content)
        attachments.append(
            {
                "name": name,
                "contentType": content_type,
                "contentInBase64": base64.b64encode(content).decode("utf-8"),
            }
        )

    return attachments, omitted


def omitted_section(omitted: List[str]) -> List[tuple]:
    if not omitted:
        return []
    return [
        (
            "Attachments omitted (too large to email)",
            "\n".join(omitted),
        )
    ]


async def send_email(
    subject: str,
    plain_text: str,
    html_body: str,
    reply_to: str,
    attachments: List[dict],
) -> str:
    """Send an email to the support inboxes and return the Azure message id."""
    if not (
        AZURE_EMAIL_CONNECTION_STRING
        and EMAIL_SENDER_ADDRESS
        and EMAIL_SUPPORT_RECIPIENTS
    ):
        log.error("Email service is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email service is not configured.",
        )

    message = {
        "senderAddress": EMAIL_SENDER_ADDRESS,
        "recipients": {
            "to": [{"address": address} for address in EMAIL_SUPPORT_RECIPIENTS]
        },
        "replyTo": [{"address": reply_to}],
        "content": {
            "subject": subject,
            "plainText": plain_text,
            "html": html_body,
        },
        "attachments": [LOGO_ATTACHMENT, *attachments],
    }

    try:
        async with EmailClient.from_connection_string(
            AZURE_EMAIL_CONNECTION_STRING
        ) as client:
            # The SDK polls every 30s by default; poll faster and cap the wait.
            poller = await client.begin_send(
                message, polling_interval=EMAIL_POLLING_INTERVAL_SECONDS
            )
            result = await asyncio.wait_for(
                poller.result(), timeout=EMAIL_SEND_TIMEOUT_SECONDS
            )
    except asyncio.TimeoutError:
        log.error("Timed out waiting for the email send to complete")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timed out sending email.",
        )
    except HttpResponseError as e:
        # The poller raises (rather than returning status "Failed") when the
        # service rejects the message or the operation fails.
        log.error(f"Email send failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send email.",
        )

    if result.get("status") != "Succeeded":
        log.error(f"Email send failed: {result.get('error')}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send email.",
        )

    return result.get("id")


async def rate_limited_user(user=Depends(get_verified_user)):
    """The signed-in user, once they are within their email budget."""
    retry_after = email_rate_limiter.hit(user.id)
    if retry_after is not None:
        log.warning(f"Email rate limit reached for user {user.id}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You've sent too many reports. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    return user


############################
# Report Issue
############################


@router.post("/issue", tags=["email"])
async def report_issue(
    description: str = Form(..., max_length=MAX_TEXT_LENGTH),
    stepsToReproduce: str = Form(..., max_length=MAX_TEXT_LENGTH),
    files: List[UploadFile] = File(None),
    user=Depends(rate_limited_user),
):
    log.info("Processing issue report submission")

    try:
        # The submitter is the signed-in user, never a client-supplied address,
        # so replies can't be redirected and senders can't be impersonated.
        submitter = user.email
        subject = "CANCHAT Issue Report"
        attachments, omitted = await build_attachments(files)
        plain_text, html_body = build_email_body(
            "Issue Report",
            subject,
            submitter,
            [
                ("Description", description),
                ("Steps to Reproduce", stepsToReproduce),
                *omitted_section(omitted),
            ],
        )

        message_id = await send_email(
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
            reply_to=submitter,
            attachments=attachments,
        )

        log.info(f"Issue report sent: {message_id}")
        return {"success": True, "messageId": message_id}

    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Unexpected error while sending issue report: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing your request.",
        )


############################
# Submit Suggestion
############################


@router.post("/suggestion", tags=["email"])
async def submit_suggestion(
    description: str = Form(..., max_length=MAX_TEXT_LENGTH),
    files: List[UploadFile] = File(None),
    user=Depends(rate_limited_user),
):
    log.info("Processing suggestion submission")

    try:
        submitter = user.email
        subject = "CANCHAT Suggestion"
        attachments, omitted = await build_attachments(files)
        plain_text, html_body = build_email_body(
            "Suggestion",
            subject,
            submitter,
            [
                ("Suggestion", description),
                *omitted_section(omitted),
            ],
        )

        message_id = await send_email(
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
            reply_to=submitter,
            attachments=attachments,
        )

        log.info(f"Suggestion sent: {message_id}")
        return {"success": True, "messageId": message_id}

    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Unexpected error while sending suggestion: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred while processing your request.",
        )
