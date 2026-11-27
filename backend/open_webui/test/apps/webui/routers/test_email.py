"""
Unit tests for the email router (/api/v1/email/issue and /api/v1/email/suggestion).

The router is mounted on a minimal FastAPI app, the signed-in user is faked by
overriding get_current_user (so the real get_verified_user role check still
runs), and the Azure EmailClient is replaced with a fake that records the
message instead of sending it. No database, network or Azure account is used.
"""

import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import email as email_router
from open_webui.utils.auth import get_current_user

SUPPORT_INBOX = "support@example.gc.ca"
SENDER = "DoNotReply@example.azurecomm.net"
SUBMITTER = "jane.doe@example.gc.ca"


# ── fakes ─────────────────────────────────────────────────────────────────────


class FakeEmailClient:
    """Stands in for azure.communication.email.aio.EmailClient."""

    sent_messages = []
    result = {"id": "msg-123", "status": "Succeeded"}
    error = None

    @classmethod
    def from_connection_string(cls, connection_string):
        return cls()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def begin_send(self, message):
        if FakeEmailClient.error:
            raise FakeEmailClient.error
        FakeEmailClient.sent_messages.append(message)
        return SimpleNamespace(result=self._result)

    async def _result(self):
        return FakeEmailClient.result


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_email_client(monkeypatch):
    FakeEmailClient.sent_messages = []
    FakeEmailClient.result = {"id": "msg-123", "status": "Succeeded"}
    FakeEmailClient.error = None

    monkeypatch.setattr(email_router, "EmailClient", FakeEmailClient)
    monkeypatch.setattr(email_router, "AZURE_EMAIL_CONNECTION_STRING", "endpoint=fake")
    monkeypatch.setattr(email_router, "EMAIL_SENDER_ADDRESS", SENDER)
    monkeypatch.setattr(email_router, "EMAIL_SUPPORT_RECIPIENTS", [SUPPORT_INBOX])
    return FakeEmailClient


def make_client(role="user"):
    app = FastAPI()
    app.include_router(email_router.router, prefix="/api/v1/email")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="user-1", role=role
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
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {**form, "email": 'x" onmouseover="alert(1)'}
        client.post(path, data=form)

        html = fake_email_client.sent_messages[0]["content"]["html"]
        assert 'onmouseover="alert(1)"' not in html

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
            ("files", ("screenshot.png", b"png-bytes", "image/png")),
            ("files", ("log.txt", b"log line", "text/plain")),
        ]
        client.post(path, data=form, files=files)

        attachments = user_attachments(fake_email_client.sent_messages[0])
        assert attachments == [
            {
                "name": "screenshot.png",
                "contentType": "image/png",
                "contentInBase64": base64.b64encode(b"png-bytes").decode(),
            },
            {
                "name": "log.txt",
                "contentType": "text/plain",
                "contentInBase64": base64.b64encode(b"log line").decode(),
            },
        ]

    def test_skips_empty_files(
        self, client, fake_email_client, path, form, subject, title
    ):
        files = [
            ("files", ("empty.txt", b"", "text/plain")),
            ("files", ("log.txt", b"log line", "text/plain")),
        ]
        client.post(path, data=form, files=files)

        attachments = user_attachments(fake_email_client.sent_messages[0])
        assert [a["name"] for a in attachments] == ["log.txt"]

    def test_missing_email_returns_422(
        self, client, fake_email_client, path, form, subject, title
    ):
        form = {k: v for k, v in form.items() if k != "email"}
        response = client.post(path, data=form)

        assert response.status_code == 422
        assert fake_email_client.sent_messages == []

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


class TestSuggestionFields:
    def test_labels_description_as_suggestion(self, client, fake_email_client):
        client.post("/api/v1/email/suggestion", data=SUGGESTION_FORM)

        content = fake_email_client.sent_messages[0]["content"]
        assert (
            "Suggestion:\nAdd a dark mode toggle to the sidebar" in content["plainText"]
        )
        assert "Steps to Reproduce" not in content["plainText"]
