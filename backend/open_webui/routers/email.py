import base64
import logging
import os
from pathlib import Path
from typing import List, Optional

from azure.communication.email.aio import EmailClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["EMAIL"])

router = APIRouter()

# Temporary: read from the environment until these move into config.py.
EMAIL_CONNECTION_STRING = os.environ.get("AZURE_EMAIL_CONNECTION_STRING", "")
EMAIL_SENDER_ADDRESS = os.environ.get("EMAIL_SENDER_ADDRESS", "")
EMAIL_SUPPORT_RECIPIENT = os.environ.get("EMAIL_SUPPORT_RECIPIENT", "")

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


async def build_attachments(files: Optional[List[UploadFile]]) -> List[dict]:
    attachments = []

    for file in files or []:
        await file.seek(0)
        content = await file.read()

        if not content:
            log.warning(f"Skipping empty file: {file.filename}")
            continue

        attachments.append(
            {
                "name": file.filename,
                "contentType": file.content_type or "application/octet-stream",
                "contentInBase64": base64.b64encode(content).decode("utf-8"),
            }
        )

    return attachments


async def send_email(
    subject: str,
    plain_text: str,
    html_body: str,
    reply_to: str,
    attachments: List[dict],
) -> str:
    """Send an email to the support inbox and return the Azure message id."""
    if not (
        EMAIL_CONNECTION_STRING and EMAIL_SENDER_ADDRESS and EMAIL_SUPPORT_RECIPIENT
    ):
        log.error("Email service is not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email service is not configured.",
        )

    message = {
        "senderAddress": EMAIL_SENDER_ADDRESS,
        "recipients": {"to": [{"address": EMAIL_SUPPORT_RECIPIENT}]},
        "replyTo": [{"address": reply_to}],
        "content": {
            "subject": subject,
            "plainText": plain_text,
            "html": html_body,
        },
        "attachments": [LOGO_ATTACHMENT, *attachments],
    }

    async with EmailClient.from_connection_string(EMAIL_CONNECTION_STRING) as client:
        poller = await client.begin_send(message)
        result = await poller.result()

    if result.get("status") != "Succeeded":
        log.error(f"Email send failed: {result.get('error')}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send email.",
        )

    return result.get("id")


############################
# Report Issue
############################


@router.post("/issue", tags=["email"])
async def report_issue(
    request: Request,
    email: str = Form(...),
    description: str = Form(...),
    stepsToReproduce: str = Form(...),
    files: List[UploadFile] = File(None),
    user=Depends(get_verified_user),
):
    log.info("Processing issue report submission")

    try:
        subject = "CANCHAT Issue Report"
        attachments = await build_attachments(files)
        plain_text, html_body = build_email_body(
            "Issue Report",
            subject,
            email,
            [
                ("Description", description),
                ("Steps to Reproduce", stepsToReproduce),
            ],
        )

        message_id = await send_email(
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
            reply_to=email,
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
    email: str = Form(...),
    description: str = Form(...),
    files: List[UploadFile] = File(None),
    user=Depends(get_verified_user),
):
    log.info("Processing suggestion submission")

    try:
        subject = "CANCHAT Suggestion"
        attachments = await build_attachments(files)
        plain_text, html_body = build_email_body(
            "Suggestion",
            subject,
            email,
            [("Suggestion", description)],
        )

        message_id = await send_email(
            subject=subject,
            plain_text=plain_text,
            html_body=html_body,
            reply_to=email,
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
