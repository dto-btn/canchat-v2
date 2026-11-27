"""
Unit tests for the email router (/api/v1/email/issue and /api/v1/email/suggestion).

The router is mounted on a minimal FastAPI app, the signed-in user is faked by
overriding get_current_user (so the real get_verified_user role check still
runs), and the Azure EmailClient is replaced with a fake that records the
message instead of sending it. No database, network or Azure account is used.
"""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from azure.core.exceptions import HttpResponseError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import email as email_router
from open_webui.utils.auth import get_current_user

SUPPORT_INBOX = "support@example.gc.ca"
SENDER = "DoNotReply@example.azurecomm.net"
SUBMITTER = "jane.doe@example.gc.ca"

# Minimal files that start with each image type's signature bytes.
PNG = b"\x89PNG\r\n\x1a\n" + b"png-data"
JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-data"
GIF = b"GIF89a" + b"gif-data"
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"webp-data"


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeEmailClient:
    """Stands in for azure.communication.email.aio.EmailClient."""

    sent_messages = []
    send_kwargs = []
    result = {"id": "msg-123", "status": "Succeeded"}
    error = None
    result_error = None
    result_delay = 0

    @classmethod
    def from_connection_string(cls, connection_string):
        return cls()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def begin_send(self, message, **kwargs):
        if FakeEmailClient.error:
            raise FakeEmailClient.error
        FakeEmailClient.sent_messages.append(message)
        FakeEmailClient.send_kwargs.append(kwargs)
        return SimpleNamespace(result=self._result)

    async def _result(self):
        if FakeEmailClient.result_delay:
            await asyncio.sleep(FakeEmailClient.result_delay)
        if FakeEmailClient.result_error:
            raise FakeEmailClient.result_error
        return FakeEmailClient.result


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_email_client(monkeypatch):
    FakeEmailClient.sent_messages = []
    FakeEmailClient.send_kwargs = []
    FakeEmailClient.result = {"id": "msg-123", "status": "Succeeded"}
    FakeEmailClient.error = None
    FakeEmailClient.result_error = None
    FakeEmailClient.result_delay = 0

    monkeypatch.setattr(email_router, "EmailClient", FakeEmailClient)
    monkeypatch.setattr(email_router, "AZURE_EMAIL_CONNECTION_STRING", "endpoint=fake")
    monkeypatch.setattr(email_router, "EMAIL_SENDER_ADDRESS", SENDER)
    monkeypatch.setattr(email_router, "EMAIL_SUPPORT_RECIPIENTS", [SUPPORT_INBOX])
    return FakeEmailClient


def make_client(role="user", email=SUBMITTER):
    app = FastAPI()
    app.include_router(email_router.router, prefix="/api/v1/email")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="user-1", role=role, email=email
    )
    return TestClient(app)


@pytest.fixture()
def client(fake_email_client):
    return make_client()


ISSUE_FORM = {
    "email": SUBMITTER,
    "description": "Chat freezes after uploading a PDF",
    "stepsToReproduce": "1. Open a chat\n2. Upload a PDF",
}
SUGGESTION_FORM = {
    "email": SUBMITTER,
    "description": "Add a dark mode toggle to the sidebar",
}

# (path, form data, expected subject, expected title) for behaviour both share
ENDPOINTS = [
    pytest.param(
        "/api/v1/email/issue",
        ISSUE_FORM,
        "CANCHAT Issue Report",
        "Issue Report",
        id="issue",
    ),
    pytest.param(
        "/api/v1/email/suggestion",
        SUGGESTION_FORM,
        "CANCHAT Suggestion",
        "Suggestion",
        id="suggestion",
    ),
]


def user_attachments(message):
    """Attachments other than the inline logo."""
    return [a for a in message["attachments"] if "contentId" not in a]


# ── behaviour shared by both endpoints ────────────────────────────────────────


@pytest.mark.parametrize("path, form, subject, title", ENDPOINTS)
class TestSharedBehaviour:
    def test_sends_to_support_inbox(
        self, client, fake_email_client, path, form, subject, title
    ):
        response = client.post(path, data=form)

        assert response.status_code == 200
        assert response.json() == {"success": True, "messageId": "msg-123"}

        [message] = fake_email_client.sent_messages
        assert message["senderAddress"] == SENDER
        assert message["recipients"] == {"to": [{"address": SUPPORT_INBOX}]}
        assert message["replyTo"] == [{"address": SUBMITTER}]
        assert message["content"]["subject"] == subject

    def test_sends_to_every_support_recipient(
        self, client, fake_email_client, monkeypatch, path, form, subject, title
    ):
        recipients = [SUPPORT_INBOX, "second@example.gc.ca"]
        monkeypatch.setattr(email_router, "EMAIL_SUPPORT_RECIPIENTS", recipients)
        client.post(path, data=form)

        message = fake_email_client.sent_messages[0]
        assert message["recipients"] == {
            "to": [{"address": SUPPORT_INBOX}, {"address": "second@example.gc.ca"}]
        }

    def test_no_recipients_returns_500(
        self, client, fake_email_client, monkeypatch, path, form, subject, title
    ):
        monkeypatch.setattr(email_router, "EMAIL_SUPPORT_RECIPIENTS", [])
        response = client.post(path, data=form)

        assert response.status_code == 500
        assert response.json()["detail"] == "Email service is not configured."
        assert fake_email_client.sent_messages == []

    def test_body_includes_title_and_submitter(
        self, client, fake_email_client, path, form, subject, title
    ):
        client.post(path, data=form)

        content = fake_email_client.sent_messages[0]["content"]
        assert title in content["html"]
        assert title in content["plainText"]
        assert form["description"] in content["html"]
        assert form["description"] in content["plainText"]
        assert SUBMITTER in content["plainText"]

    def test_submitter_is_a_mailto_link_with_reply_subject(
        self, client, fake_email_client, path, form, subject, title
    ):
        client.post(path, data=form)

        html = fake_email_client.sent_messages[0]["content"]["html"]
        reply_subject = ("Re: " + subject).replace(":", "%3A").replace(" ", "%20")
        assert f'href="mailto:{SUBMITTER}?subject={reply_subject}"' in html

    def test_escapes_user_input_in_html(
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {**form, "description": "<script>alert(1)</script>"}
        client.post(path, data=form)

        content = fake_email_client.sent_messages[0]["content"]
        assert "<script>" not in content["html"]
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content["html"]
        # Plain text is not HTML, so it keeps the input as typed.
        assert "<script>alert(1)</script>" in content["plainText"]

    def test_escapes_submitter_inside_link(
        self, fake_email_client, path, form, subject, title
    ):
        make_client(email='x" onmouseover="alert(1)').post(path, data=form)

        html = fake_email_client.sent_messages[0]["content"]["html"]
        assert 'onmouseover="alert(1)"' not in html

    def test_submitter_cannot_inject_mailto_params(
        self, fake_email_client, path, form, subject, title
    ):
        make_client(email="x@example.gc.ca?bcc=evil@example.com").post(path, data=form)

        html = fake_email_client.sent_messages[0]["content"]["html"]
        assert 'href="mailto:x@example.gc.ca?bcc=' not in html
        assert 'href="mailto:x@example.gc.ca%3Fbcc%3Devil@example.com?subject=' in html

    def test_ignores_client_supplied_email(
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {**form, "email": "attacker@example.com"}
        response = client.post(path, data=form)

        assert response.status_code == 200
        message = fake_email_client.sent_messages[0]
        assert message["replyTo"] == [{"address": SUBMITTER}]
        assert "attacker@example.com" not in message["content"]["html"]
        assert "attacker@example.com" not in message["content"]["plainText"]

    def test_accepts_description_at_max_length(
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {**form, "description": "a" * email_router.MAX_TEXT_LENGTH}
        response = client.post(path, data=form)

        assert response.status_code == 200

    def test_rejects_description_over_max_length(
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {**form, "description": "a" * (email_router.MAX_TEXT_LENGTH + 1)}
        response = client.post(path, data=form)

        assert response.status_code == 422
        assert fake_email_client.sent_messages == []

    def test_body_leaves_out_environment_and_version(
        self, client, fake_email_client, path, form, subject, title
    ):
        client.post(path, data=form)

        content = fake_email_client.sent_messages[0]["content"]
        assert "Environment" not in content["plainText"]
        assert "App Version" not in content["plainText"]

    def test_embeds_logo_as_inline_attachment(
        self, client, fake_email_client, path, form, subject, title
    ):
        client.post(path, data=form)

        message = fake_email_client.sent_messages[0]
        logo = message["attachments"][0]
        assert logo["contentId"] == email_router.LOGO_CID
        assert logo["contentType"] == "image/png"
        assert f'src="cid:{email_router.LOGO_CID}"' in message["content"]["html"]
        assert user_attachments(message) == []

    def test_attaches_uploaded_files(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [
            ("files", ("screenshot.png", PNG, "image/png")),
            ("files", ("photo.jpg", JPEG, "image/jpeg")),
        ]
        client.post(path, data=form, files=files)

        attachments = user_attachments(fake_email_client.sent_messages[0])
        assert attachments == [
            {
                "name": "screenshot.png",
                "contentType": "image/png",
                "contentInBase64": base64.b64encode(PNG).decode(),
            },
            {
                "name": "photo.jpg",
                "contentType": "image/jpeg",
                "contentInBase64": base64.b64encode(JPEG).decode(),
            },
        ]

    def test_skips_empty_files(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [
            ("files", ("empty.png", b"", "image/png")),
            ("files", ("screenshot.png", PNG, "image/png")),
        ]
        client.post(path, data=form, files=files)

        attachments = user_attachments(fake_email_client.sent_messages[0])
        assert [a["name"] for a in attachments] == ["screenshot.png"]

    @pytest.mark.parametrize(
        "content, expected_type",
        [
            (PNG, "image/png"),
            (JPEG, "image/jpeg"),
            (GIF, "image/gif"),
            (WEBP, "image/webp"),
        ],
    )
    def test_accepts_supported_image_types(
        self,
        client,
        fake_email_client,
        path,
        form,
        subject,
        title,
        content,
        expected_type,
    ):
        files = [("files", ("image", content, "application/octet-stream"))]
        response = client.post(path, data=form, files=files)

        assert response.status_code == 200
        [attachment] = user_attachments(fake_email_client.sent_messages[0])
        assert attachment["contentType"] == expected_type

    @pytest.mark.parametrize(
        "filename, content, claimed_type",
        [
            ("notes.txt", b"plain text", "text/plain"),
            ("disguised.png", b"MZ\x90\x00 executable", "image/png"),
            ("icon.svg", b'<svg onload="alert(1)"></svg>', "image/svg+xml"),
            ("page.html", b"<html><script></script></html>", "image/png"),
        ],
    )
    def test_rejects_files_that_are_not_supported_images(
        self,
        client,
        fake_email_client,
        path,
        form,
        subject,
        title,
        filename,
        content,
        claimed_type,
    ):
        files = [("files", (filename, content, claimed_type))]
        response = client.post(path, data=form, files=files)

        assert response.status_code == 400
        assert response.json()["detail"] == (
            "Only PNG, JPEG, GIF and WebP images can be attached."
        )
        assert fake_email_client.sent_messages == []

    def test_uses_detected_type_not_client_content_type(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [("files", ("screenshot.png", PNG, "text/html"))]
        client.post(path, data=form, files=files)

        [attachment] = user_attachments(fake_email_client.sent_messages[0])
        assert attachment["contentType"] == "image/png"

    def test_rejects_more_than_max_files(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [
            ("files", (f"shot-{i}.png", PNG, "image/png"))
            for i in range(email_router.MAX_FILES + 1)
        ]
        response = client.post(path, data=form, files=files)

        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"You can only upload up to {email_router.MAX_FILES} files."
        )
        assert fake_email_client.sent_messages == []

    def test_strips_paths_from_filenames(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [
            ("files", ("../../etc/shot.png", PNG, "image/png")),
            ("files", ("C:\\Users\\jane\\photo.jpg", JPEG, "image/jpeg")),
        ]
        client.post(path, data=form, files=files)

        attachments = user_attachments(fake_email_client.sent_messages[0])
        assert [a["name"] for a in attachments] == ["shot.png", "photo.jpg"]

    def test_omits_attachments_over_size_limit(
        self, client, fake_email_client, monkeypatch, path, form, subject, title
    ):
        monkeypatch.setattr(email_router, "MAX_ATTACHMENT_BYTES", len(PNG) + 5)
        files = [
            ("files", ("small.png", PNG, "image/png")),
            ("files", ("huge.png", PNG + b"x" * 50, "image/png")),
        ]
        response = client.post(path, data=form, files=files)

        assert response.status_code == 200
        message = fake_email_client.sent_messages[0]
        assert [a["name"] for a in user_attachments(message)] == ["small.png"]
        assert "huge.png" in message["content"]["plainText"]
        assert "Attachments omitted" in message["content"]["plainText"]

    def test_polls_with_short_interval(
        self, client, fake_email_client, path, form, subject, title
    ):
        client.post(path, data=form)

        assert fake_email_client.send_kwargs[0]["polling_interval"] == (
            email_router.EMAIL_POLLING_INTERVAL_SECONDS
        )

    def test_send_timeout_returns_504(
        self, client, fake_email_client, monkeypatch, path, form, subject, title
    ):
        monkeypatch.setattr(email_router, "EMAIL_SEND_TIMEOUT_SECONDS", 0.01)
        fake_email_client.result_delay = 1
        response = client.post(path, data=form)

        assert response.status_code == 504
        assert response.json()["detail"] == "Timed out sending email."

    def test_poller_error_returns_502(
        self, client, fake_email_client, path, form, subject, title
    ):
        fake_email_client.result_error = HttpResponseError("Operation failed")
        response = client.post(path, data=form)

        assert response.status_code == 502
        assert response.json()["detail"] == "Failed to send email."

    def test_unverified_user_is_rejected(
        self, fake_email_client, path, form, subject, title
    ):
        response = make_client(role="pending").post(path, data=form)

        assert response.status_code == 401
        assert fake_email_client.sent_messages == []

    def test_not_configured_returns_500(
        self, client, fake_email_client, monkeypatch, path, form, subject, title
    ):
        monkeypatch.setattr(email_router, "AZURE_EMAIL_CONNECTION_STRING", "")
        response = client.post(path, data=form)

        assert response.status_code == 500
        assert response.json()["detail"] == "Email service is not configured."
        assert fake_email_client.sent_messages == []

    def test_failed_send_returns_502(
        self, client, fake_email_client, path, form, subject, title
    ):
        fake_email_client.result = {
            "id": "msg-123",
            "status": "Failed",
            "error": {"message": "Recipient rejected"},
        }
        response = client.post(path, data=form)

        assert response.status_code == 502
        assert response.json()["detail"] == "Failed to send email."

    def test_client_error_returns_generic_500(
        self, client, fake_email_client, path, form, subject, title
    ):
        fake_email_client.error = RuntimeError("secret connection details")
        response = client.post(path, data=form)

        assert response.status_code == 500
        detail = response.json()["detail"]
        assert detail == "An unexpected error occurred while processing your request."
        assert "secret" not in detail


# ── endpoint-specific fields ──────────────────────────────────────────────────


class TestIssueFields:
    def test_includes_steps_to_reproduce(self, client, fake_email_client):
        client.post("/api/v1/email/issue", data=ISSUE_FORM)

        content = fake_email_client.sent_messages[0]["content"]
        assert "Steps to Reproduce" in content["html"]
        assert "1. Open a chat\n2. Upload a PDF" in content["plainText"]

    def test_missing_steps_returns_422(self, client, fake_email_client):
        form = {k: v for k, v in ISSUE_FORM.items() if k != "stepsToReproduce"}
        response = client.post("/api/v1/email/issue", data=form)

        assert response.status_code == 422
        assert fake_email_client.sent_messages == []

    def test_rejects_steps_over_max_length(self, client, fake_email_client):
        form = {
            **ISSUE_FORM,
            "stepsToReproduce": "a" * (email_router.MAX_TEXT_LENGTH + 1),
        }
        response = client.post("/api/v1/email/issue", data=form)

        assert response.status_code == 422
        assert fake_email_client.sent_messages == []


class TestSuggestionFields:
    def test_labels_description_as_suggestion(self, client, fake_email_client):
        client.post("/api/v1/email/suggestion", data=SUGGESTION_FORM)

        content = fake_email_client.sent_messages[0]["content"]
        assert (
            "Suggestion:\nAdd a dark mode toggle to the sidebar" in content["plainText"]
        )
        assert "Steps to Reproduce" not in content["plainText"]


# ── helpers ───────────────────────────────────────────────────────────────────


class TestSafeFilename:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("shot\r\n.png", "shot.png"),
            ("tab\there.png", "tabhere.png"),
            ("nul\x00byte.png", "nulbyte.png"),
            ("../../etc/passwd", "passwd"),
            ("C:\\Users\\jane\\photo.jpg", "photo.jpg"),
            ("", "attachment-2"),
            (None, "attachment-2"),
            ("\r\n", "attachment-2"),
            ("a" * 300 + ".png", "a" * 255),
        ],
    )
    def test_cleans_client_filenames(self, filename, expected):
        assert email_router.safe_filename(filename, 2) == expected
