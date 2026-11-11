from datetime import datetime

from pydantic import BaseModel, Field


class TelegramPostItem(BaseModel):
    rfq_id: str
    title: str | None = None
    status: str
    error: str | None = None
    message_id: int | None = None
    created_at: datetime


class TelegramSettingsView(BaseModel):
    """What the admin sees. The token itself never leaves the server."""

    enabled: bool = False
    # Requests and answers inside Telegram, in private chats with the bot.
    assistant_enabled: bool = False
    has_token: bool = False
    token_hint: str | None = None
    bot_username: str | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    site_url: str
    default_site_url: str
    updated_at: datetime | None = None
    sent_count: int = 0
    failed_count: int = 0
    recent: list[TelegramPostItem] = []
    can_update: bool = False


class TelegramSettingsUpdate(BaseModel):
    enabled: bool = False
    assistant_enabled: bool = False
    # Empty keeps the saved token.
    bot_token: str | None = Field(default=None, max_length=128)
    channel_id: str | None = Field(default=None, max_length=128)
    site_url: str | None = Field(default=None, max_length=255)


class TelegramActionResult(BaseModel):
    ok: bool = True
    status: str | None = None


class TelegramAccountView(BaseModel):
    """A person's link to the bot, as their profile page shows it."""

    # The bot takes requests and answers; false hides the whole card.
    available: bool = False
    bot_username: str | None = None
    linked: bool = False
    telegram_username: str | None = None
    telegram_name: str | None = None
    linked_at: datetime | None = None


class TelegramLinkStart(BaseModel):
    # Opens the bot with a one-time code; pressing Start there links the account.
    url: str
    expires_at: datetime
