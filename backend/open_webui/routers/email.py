import base64
import html
import logging
import os
from typing import List, Optional

from azure.communication.email.aio import EmailClient
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

from open_webui.env import SRC_LOG_LEVELS, VERSION
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["EMAIL"])

router = APIRouter()

# Temporary: read from the environment until these move into PersistentConfig.
EMAIL_CONNECTION_STRING = os.environ.get("AZURE_EMAIL_CONNECTION_STRING", "")
EMAIL_SENDER_ADDRESS = os.environ.get("EMAIL_SENDER_ADDRESS", "")
EMAIL_SUPPORT_RECIPIENT = os.environ.get("EMAIL_SUPPORT_RECIPIENT", "")


############################
# Helpers
############################


def build_email_body(title: str, sections: List[tuple]) -> tuple:
    """Build the plain-text and HTML bodies from (label, value) pairs.

    User input is escaped before it goes into the HTML body.
    """
    plain_text = f"{title}\n\n" + "\n\n".join(
        f"{label}:\n{value}" for label, value in sections
    )

    html_sections = "".join(
        f"<h3>{html.escape(label)}</h3>"
        f'<p style="white-space: pre-wrap;">{html.escape(value)}</p>'
        for label, value in sections
    )
    html_body = (
        f"<html><body><h2>{html.escape(title)}</h2>{html_sections}</body></html>"
    )

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
    }
    if attachments:
        message["attachments"] = attachments

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
        plain_text, html_body = build_email_body(
            "User Issue Submission",
            [
                ("Environment", request.app.state.config.WEBUI_URL),
                ("App Version", VERSION),
                ("Email", email),
                ("Description", description),
                ("Steps to Reproduce", stepsToReproduce),
            ],
        )
        attachments = await build_attachments(files)

        message_id = await send_email(
            subject="User Issue Submission",
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
