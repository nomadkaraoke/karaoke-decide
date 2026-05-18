"""Email service for sending transactional emails via Postmark."""

import logging

import httpx

from backend.config import BackendSettings
from backend.i18n import get_locale_prefix, t

logger = logging.getLogger(__name__)


POSTMARK_API_URL = "https://api.postmarkapp.com/email"


class EmailService:
    """Service for sending emails via Postmark's REST API."""

    def __init__(self, settings: BackendSettings):
        self.settings = settings

    @property
    def is_configured(self) -> bool:
        """Check if Postmark is properly configured."""
        return bool(self.settings.postmark_server_token)

    async def _send(
        self,
        to_email: str,
        subject: str,
        html_content: str,
    ) -> bool:
        """Send a single email via Postmark's REST API.

        Returns True on 2xx, False otherwise. Logs Postmark's structured
        error (ErrorCode/Message) when available so suppressions or
        unverified-signature errors surface clearly.
        """
        payload = {
            "From": self.settings.postmark_from_email,
            "To": to_email,
            "Subject": subject,
            "HtmlBody": html_content,
            "MessageStream": "outbound",
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": self.settings.postmark_server_token,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(POSTMARK_API_URL, json=payload, headers=headers)
        except httpx.HTTPError:
            logger.exception(f"Failed to send email to {to_email} via Postmark")
            return False

        if 200 <= response.status_code < 300:
            logger.info(f"Email sent to {to_email} via Postmark")
            return True

        try:
            err = response.json()
            logger.error(
                f"Postmark returned status {response.status_code}: "
                f"ErrorCode={err.get('ErrorCode')} Message={err.get('Message')}"
            )
        except ValueError:
            logger.error(f"Postmark returned status {response.status_code}: {response.text[:200]}")
        return False

    async def send_magic_link(self, email: str, token: str, locale: str = "en") -> bool:
        """Send a magic link email to the user.

        Args:
            email: Recipient email address
            token: Magic link token
            locale: Language code for email content (default: "en")

        Returns:
            True if email was sent (or logged in dev mode), False on error

        Raises:
            RuntimeError: If email service is not configured in production
        """
        locale_prefix = get_locale_prefix(locale)
        magic_link_url = f"{self.settings.frontend_url}{locale_prefix}/auth/verify?token={token}"

        if not self.is_configured:
            if self.settings.is_production:
                logger.error("POSTMARK_SERVER_TOKEN is not configured in production!")
                raise RuntimeError("Email service is not configured. Please contact support.")
            logger.info("=" * 60)
            logger.info("MAGIC LINK (Postmark not configured - dev mode)")
            logger.info(f"To: {email}")
            logger.info(f"Locale: {locale}")
            logger.info(f"Link: {magic_link_url}")
            logger.info("=" * 60)
            return True

        return await self._send(
            to_email=email,
            subject=t(locale, "emails.magicLink.subject"),
            html_content=self._build_magic_link_html(magic_link_url, locale),
        )

    def _build_magic_link_html(self, magic_link_url: str, locale: str = "en") -> str:
        """Build the HTML content for the magic link email."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{t(locale, "emails.magicLink.subject")}</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #7c3aed; margin-bottom: 10px;">{t(locale, "emails.magicLink.heading")}</h1>
            </div>

            <div style="background: #f9fafb; border-radius: 8px; padding: 30px; margin-bottom: 30px;">
                <h2 style="margin-top: 0;">{t(locale, "emails.magicLink.title")}</h2>
                <p>{t(locale, "emails.magicLink.description")}</p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{magic_link_url}"
                       style="display: inline-block; background: #7c3aed; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                        {t(locale, "emails.magicLink.button")}
                    </a>
                </div>

                <p style="color: #666; font-size: 14px;">
                    {t(locale, "emails.magicLink.copyLink")}<br>
                    <a href="{magic_link_url}" style="color: #7c3aed; word-break: break-all;">{magic_link_url}</a>
                </p>
            </div>

            <div style="text-align: center; color: #999; font-size: 12px;">
                <p>{t(locale, "emails.magicLink.footer")}</p>
                <p>&copy; {t(locale, "emails.magicLink.copyright")} - <a href="https://nomadkaraoke.com" style="color: #999;">nomadkaraoke.com</a></p>
            </div>
        </body>
        </html>
        """

    async def send_sync_complete_email(
        self,
        to_email: str,
        songs_matched: int,
        artists_stored: int,
        services: list[str],
        locale: str = "en",
    ) -> bool:
        """Send an email when sync completes.

        Args:
            to_email: Recipient email address.
            songs_matched: Number of songs matched to catalog.
            artists_stored: Number of artists stored.
            services: List of service names that were synced.
            locale: Language code for email content (default: "en").

        Returns:
            True if email was sent successfully.

        Note:
            Unlike send_magic_link, this doesn't raise in production if email
            is not configured - sync completion emails are optional notifications.
        """
        frontend_url = self.settings.frontend_url
        # Format services list with proper grammar (Oxford comma for 3+ items)
        if not services:
            services_str = "your music services"
        elif len(services) == 1:
            services_str = services[0]
        elif len(services) == 2:
            services_str = f"{services[0]} and {services[1]}"
        else:
            services_str = ", ".join(services[:-1]) + f", and {services[-1]}"

        if not self.is_configured:
            if self.settings.is_production:
                logger.warning("POSTMARK_SERVER_TOKEN not configured - skipping sync complete email")
            else:
                logger.info("=" * 60)
                logger.info("SYNC COMPLETE EMAIL (Postmark not configured - dev mode)")
                logger.info(f"To: {to_email}")
                logger.info(f"Locale: {locale}")
                logger.info(f"Songs matched: {songs_matched}")
                logger.info(f"Artists stored: {artists_stored}")
                logger.info(f"Services: {services_str}")
                logger.info("=" * 60)
            return True

        return await self._send(
            to_email=to_email,
            subject=t(locale, "emails.syncComplete.subject"),
            html_content=self._build_sync_complete_html(
                songs_matched, artists_stored, services_str, frontend_url, locale
            ),
        )

    def _build_sync_complete_html(
        self,
        songs_matched: int,
        artists_stored: int,
        services_str: str,
        frontend_url: str,
        locale: str = "en",
    ) -> str:
        """Build the HTML content for the sync complete email."""
        locale_prefix = get_locale_prefix(locale)
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{t(locale, "emails.syncComplete.subject")}</title>
        </head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #7c3aed; margin-bottom: 10px;">{t(locale, "emails.syncComplete.heading")}</h1>
            </div>

            <div style="background: #f9fafb; border-radius: 8px; padding: 30px; margin-bottom: 30px;">
                <h2 style="margin-top: 0;">{t(locale, "emails.syncComplete.title")}</h2>
                <p>{t(locale, "emails.syncComplete.description", services=services_str)}</p>

                <div style="background: white; border-radius: 6px; padding: 20px; margin: 20px 0;">
                    <div style="display: flex; justify-content: space-around; text-align: center;">
                        <div>
                            <div style="font-size: 32px; font-weight: bold; color: #7c3aed;">{songs_matched}</div>
                            <div style="color: #666; font-size: 14px;">{t(locale, "emails.syncComplete.songsFound")}</div>
                        </div>
                        <div>
                            <div style="font-size: 32px; font-weight: bold; color: #7c3aed;">{artists_stored}</div>
                            <div style="color: #666; font-size: 14px;">{t(locale, "emails.syncComplete.artistsAnalyzed")}</div>
                        </div>
                    </div>
                </div>

                <p>{t(locale, "emails.syncComplete.cta")}</p>

                <div style="text-align: center; margin: 30px 0;">
                    <a href="{frontend_url}{locale_prefix}/my/songs"
                       style="display: inline-block; background: #7c3aed; color: white; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                        {t(locale, "emails.syncComplete.button")}
                    </a>
                </div>
            </div>

            <div style="text-align: center; color: #999; font-size: 12px;">
                <p>&copy; {t(locale, "emails.syncComplete.copyright")} - <a href="https://nomadkaraoke.com" style="color: #999;">nomadkaraoke.com</a></p>
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
