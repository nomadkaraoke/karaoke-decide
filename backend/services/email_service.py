"""Email service for sending transactional emails via SendGrid."""

import logging

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from backend.config import BackendSettings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via SendGrid."""

    def __init__(self, settings: BackendSettings):
        self.settings = settings
        self._client: SendGridAPIClient | None = None

    @property
    def is_configured(self) -> bool:
        """Check if SendGrid is properly configured."""
        return bool(self.settings.sendgrid_api_key)

    @property
    def client(self) -> SendGridAPIClient | None:
        """Get or create SendGrid client. Returns None if not configured."""
        if not self.is_configured:
            return None
        if self._client is None:
            self._client = SendGridAPIClient(api_key=self.settings.sendgrid_api_key)
        return self._client

    async def send_magic_link(self, email: str, token: str) -> bool:
        """Send a magic link email to the user.

        Args:
            email: Recipient email address
            token: Magic link token

        Returns:
            True if email was sent (or logged in dev mode), False on error
        """
        magic_link_url = f"{self.settings.frontend_url}/auth/verify?token={token}"

        # Dev mode: log to console instead of sending
        if not self.is_configured:
            logger.info("=" * 60)
            logger.info("MAGIC LINK (SendGrid not configured - dev mode)")
            logger.info(f"To: {email}")
            logger.info(f"Link: {magic_link_url}")
            logger.info("=" * 60)
            return True

        # Production mode: send via SendGrid
        message = Mail(
            from_email=self.settings.sendgrid_from_email,
            to_emails=email,
            subject="Sign in to Nomad Karaoke Decide",
            html_content=self._build_magic_link_html(magic_link_url),
        )

        try:
            response = self.client.send(message)  # type: ignore[union-attr]
            if response.status_code >= 200 and response.status_code < 300:
                logger.info(f"Magic link email sent to {email}")
                return True
            else:
                logger.error(f"SendGrid error: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to send magic link email: {e}")
            return False

    def _build_magic_link_html(self, magic_link_url: str) -> str:
        """Build the HTML content for the magic link email."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Sign in to Nomad Karaoke Decide</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #7c3aed; margin-bottom: 10px;">Nomad Karaoke Decide</h1>
            </div>

            <div style="background: #f9fafb; border-radius: 8px; padding: 30px; margin-bottom: 30px;">
                <h2 style="margin-top: 0;">Sign in to your account</h2>
                <p>Click the button below to sign in. This link will expire in 15 minutes.</p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{magic_link_url}"
                       style="display: inline-block; background: #7c3aed; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                        Sign In
                    </a>
                </div>

                <p style="color: #666; font-size: 14px;">
                    Or copy and paste this link into your browser:<br>
                    <a href="{magic_link_url}" style="color: #7c3aed; word-break: break-all;">{magic_link_url}</a>
                </p>
            </div>

            <div style="text-align: center; color: #999; font-size: 12px;">
                <p>If you didn't request this email, you can safely ignore it.</p>
                <p>&copy; Nomad Karaoke - <a href="https://nomadkaraoke.com" style="color: #999;">nomadkaraoke.com</a></p>
            </div>
        </body>
        </html>
        """


# Singleton instance (lazy initialization)
_email_service: EmailService | None = None


def get_email_service(settings: BackendSettings | None = None) -> EmailService:
    """Get the email service instance.

    Args:
        settings: Optional settings override (for testing)

    Returns:
        EmailService instance
    """
    global _email_service
    if _email_service is None or settings is not None:
        if settings is None:
            from backend.config import get_backend_settings

            settings = get_backend_settings()
        _email_service = EmailService(settings)
    return _email_service
