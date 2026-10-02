"""Tests for EmailService's Postmark send path and SMTP fallback."""

import smtplib
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.config import BackendSettings
from backend.services.email_service import EmailService


@pytest.fixture
def service() -> EmailService:
    return EmailService(
        BackendSettings(
            environment="development",
            google_cloud_project="test-project",
            postmark_server_token="tok",
            postmark_from_email="Nomad Karaoke <decide@example.com>",
        )
    )


def _response(status_code: int, json_body: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://api.postmarkapp.com/email")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(
        status_code, text="<html><center><h1>403 Forbidden</h1></center></html>", request=request
    )


@pytest.fixture
def mock_post():
    with patch("backend.services.email_service.httpx.AsyncClient") as client_cls:
        post = AsyncMock()
        client_cls.return_value.__aenter__.return_value.post = post
        yield post


@pytest.fixture
def mock_smtp():
    with patch("backend.services.email_service.smtplib.SMTP") as smtp_cls:
        smtp = smtp_cls.return_value
        yield smtp_cls, smtp


async def test_api_success_does_not_use_smtp(service, mock_post, mock_smtp):
    mock_post.return_value = _response(200, {"MessageID": "abc"})

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is True
    mock_smtp[0].assert_not_called()


async def test_html_403_falls_back_to_smtp(service, mock_post, mock_smtp):
    """The prod failure mode: nginx HTML 403 from Postmark's edge."""
    smtp_cls, smtp = mock_smtp
    mock_post.return_value = _response(403)

    assert await service._send("user@example.com", "Ünïcode", "<p>x</p>") is True
    smtp_cls.assert_called_once_with("smtp.postmarkapp.com", 587, timeout=15)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("tok", "tok")
    msg = smtp.send_message.call_args.args[0]
    assert msg["To"] == "user@example.com"
    assert msg["From"] == "Nomad Karaoke <decide@example.com>"
    assert msg["Subject"] == "Ünïcode"
    assert msg["X-PM-Message-Stream"] == "outbound"
    assert msg.get_body(("html",)).get_content().strip() == "<p>x</p>"


@pytest.mark.parametrize(
    "status,body",
    [
        (403, {"ErrorCode": 10, "Message": "Bad or missing API token"}),
        (422, {"ErrorCode": 406, "Message": "Inactive recipient"}),
    ],
)
async def test_json_postmark_errors_do_not_fall_back(service, mock_post, mock_smtp, status, body):
    mock_post.return_value = _response(status, body)

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is False
    mock_smtp[0].assert_not_called()


async def test_connect_error_falls_back_to_smtp(service, mock_post, mock_smtp):
    mock_post.side_effect = httpx.ConnectError("unreachable")

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is True
    mock_smtp[1].send_message.assert_called_once()


async def test_read_timeout_does_not_fall_back(service, mock_post, mock_smtp):
    """Postmark may already have accepted it; resending could duplicate."""
    mock_post.side_effect = httpx.ReadTimeout("no response")

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is False
    mock_smtp[0].assert_not_called()


async def test_smtp_failure_returns_false(service, mock_post, mock_smtp):
    mock_post.return_value = _response(403)
    mock_smtp[1].login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is False


async def test_smtp_header_injection_returns_false(service, mock_post, mock_smtp):
    mock_post.return_value = _response(403)

    assert await service._send("user@example.com", "Subj\nBcc: evil@example.com", "<p>x</p>") is False
    mock_smtp[1].send_message.assert_not_called()


async def test_quit_failure_after_send_still_reports_success(service, mock_post, mock_smtp):
    """Postmark already accepted the message; a failed QUIT must not trigger a resend."""
    mock_post.return_value = _response(403)
    mock_smtp[1].quit.side_effect = smtplib.SMTPServerDisconnected("gone")

    assert await service._send("user@example.com", "Subj", "<p>x</p>") is True
    mock_smtp[1].close.assert_called_once()
