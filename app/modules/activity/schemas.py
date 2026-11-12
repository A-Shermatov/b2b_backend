"""What the site reports about itself: its browser, and the pages a person opens.

Everything here comes from the client and is kept as a signal, never trusted as a
fact. Values that are too long are cut, not refused: one odd field must not lose
the rest of a report.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

# A page that opens sends two or three events, and the site cuts a longer queue
# into batches of this size: every event becomes a row of the journal.
MAX_EVENTS = 10
MAX_QUERY_KEYS = 20


def _text(limit: int):
    def clip(value):
        if value is None:
            return None
        text = str(value).strip()
        # Control characters have no place in a name or a path.
        text = "".join(ch for ch in text if ch.isprintable())
        return text[:limit] or None

    return BeforeValidator(clip)


def _number(low: float, high: float, whole: bool = True):
    def bound(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number != number or number < low or number > high:  # NaN or out of range
            return None
        return int(number) if whole else round(number, 2)

    return BeforeValidator(bound)


def _flag(value):
    return value if isinstance(value, bool) else None


def _texts(limit: int, count: int):
    def clip(value):
        if not isinstance(value, (list, tuple)):
            return []
        items = []
        for item in value[:count]:
            text = "".join(ch for ch in str(item).strip() if ch.isprintable())[:limit]
            if text:
                items.append(text)
        return items

    return BeforeValidator(clip)


Flag = Annotated[bool | None, BeforeValidator(_flag)]


class DeviceReport(BaseModel):
    """What a browser can tell about the machine it runs on."""

    model_config = ConfigDict(extra="ignore")

    platform: Annotated[str | None, _text(60)] = None
    platform_version: Annotated[str | None, _text(40)] = None
    architecture: Annotated[str | None, _text(20)] = None
    bitness: Annotated[str | None, _text(8)] = None
    model: Annotated[str | None, _text(80)] = None
    mobile: Flag = None
    #: «Chromium 142», «Microsoft Edge 142».
    brands: Annotated[list[str], _texts(60, 8)] = []
    language: Annotated[str | None, _text(35)] = None
    languages: Annotated[list[str], _texts(35, 10)] = []
    timezone: Annotated[str | None, _text(64)] = None
    #: Minutes east of UTC.
    timezone_offset: Annotated[int | None, _number(-900, 900)] = None
    screen_width: Annotated[int | None, _number(1, 20000)] = None
    screen_height: Annotated[int | None, _number(1, 20000)] = None
    viewport_width: Annotated[int | None, _number(1, 20000)] = None
    viewport_height: Annotated[int | None, _number(1, 20000)] = None
    pixel_ratio: Annotated[float | None, _number(0.1, 16, whole=False)] = None
    color_depth: Annotated[int | None, _number(1, 64)] = None
    cpu_cores: Annotated[int | None, _number(1, 1024)] = None
    memory_gb: Annotated[float | None, _number(0.1, 4096, whole=False)] = None
    touch_points: Annotated[int | None, _number(0, 64)] = None
    gpu: Annotated[str | None, _text(200)] = None
    gpu_vendor: Annotated[str | None, _text(100)] = None
    #: «4g», «wifi»: what the browser calls its connection.
    connection: Annotated[str | None, _text(20)] = None
    cookies: Flag = None
    #: Installed to the home screen and opened as an app.
    standalone: Flag = None
    #: The browser is driven by a program (Selenium, Playwright), not by a hand.
    webdriver: Flag = None
    dark: Flag = None
    #: Where the person came to the site from.
    referrer: Annotated[str | None, _text(300)] = None


class PageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: «view» when a page is opened, «leave» when the person moves on.
    type: Literal["view", "leave"]
    #: The id the browser gave this view; «leave» names the view it ends.
    id: UUID
    path: Annotated[str | None, _text(500)] = None
    query: dict[str, str] | None = None
    title: Annotated[str | None, _text(200)] = None
    #: The page the person came from.
    referrer: Annotated[str | None, _text(300)] = None
    #: Time on the page, and the part of it the tab was actually on screen.
    seconds: Annotated[int | None, _number(0, 86_400)] = None
    active_seconds: Annotated[int | None, _number(0, 86_400)] = None
    #: How long ago it happened, by the browser's own clock (events wait a little
    #: before they are sent). The server's clock gives the time itself.
    ago_ms: Annotated[int | None, _number(0, 3_600_000)] = None


class ActivityBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device: DeviceReport | None = None
    events: list[PageEvent] = Field(default_factory=list, max_length=MAX_EVENTS)


class ActivityAccepted(BaseModel):
    accepted: int
