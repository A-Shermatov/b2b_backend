"""What a request tells about the device it came from.

Used by the journal and the device registry. Everything here is read from the
request headers or from the report the site sends about its own browser; none of
it is proof of identity: a client can send any header it likes. It is a signal
for an administrator ("a new device", "one device, several accounts"), not a
factor to let somebody in or keep them out.
"""

import hashlib
import re
from dataclasses import asdict, dataclass

USER_AGENT_LIMIT = 500
#: The id a browser makes for itself and sends as X-Device-Id (a UUID).
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
#: A device with no id of its own is told apart by its address and headers.
DERIVED_PREFIX = "h-"
#: What the bot's assistant names as its "browser": the person acts from a chat.
TELEGRAM_AGENT = "Telegram"

_WINDOWS_NT = {
    "6.3": "8.1",
    "6.2": "8",
    "6.1": "7",
    "6.0": "Vista",
    "5.2": "XP",
    "5.1": "XP",
}
#: Programs that are not a person at a browser, with the name shown in the journal.
_TOOLS: tuple[tuple[str, str], ...] = (
    (r"googlebot", "Googlebot"),
    (r"bingbot", "Bingbot"),
    (r"yandex(?:bot|images|metrika|mobilebot|accessibilitybot)", "YandexBot"),
    (r"duckduckbot", "DuckDuckBot"),
    (r"baiduspider", "Baiduspider"),
    (r"telegrambot", "TelegramBot"),
    (r"whatsapp", "WhatsApp"),
    (r"facebookexternalhit|facebot", "Facebook"),
    (r"twitterbot", "Twitterbot"),
    (r"slackbot", "Slackbot"),
    (r"discordbot", "Discordbot"),
    (r"gptbot|chatgpt-user|oai-searchbot", "OpenAI"),
    (r"claudebot|claude-user|anthropic", "Anthropic"),
    (r"ahrefsbot", "AhrefsBot"),
    (r"semrushbot", "SemrushBot"),
    (r"mj12bot", "MJ12bot"),
    (r"petalbot", "PetalBot"),
    (r"uptimerobot", "UptimeRobot"),
    (r"headlesschrome", "Headless Chrome"),
    (r"phantomjs", "PhantomJS"),
    (r"python-requests", "python-requests"),
    (r"python-httpx", "python-httpx"),
    (r"aiohttp", "aiohttp"),
    (r"python-urllib|python/", "Python"),
    (r"curl", "curl"),
    (r"wget", "Wget"),
    (r"go-http-client", "Go"),
    (r"okhttp", "OkHttp"),
    (r"apache-httpclient|java/", "Java"),
    (r"postmanruntime", "Postman"),
    (r"insomnia", "Insomnia"),
    (r"axios", "axios"),
    (r"node-fetch|undici|node\.js", "Node.js"),
    (r"scrapy", "Scrapy"),
    (r"libwww-perl", "Perl"),
    (r"guzzlehttp|php", "PHP"),
    (r"ruby", "Ruby"),
    (r"nmap|masscan|zgrab|nikto|sqlmap|nuclei|wpscan|dirbuster|gobuster", "Сканер"),
)
_TOOL_RULES = tuple((re.compile(pattern, re.I), name) for pattern, name in _TOOLS)
_GENERIC_BOT = re.compile(r"bot\b|crawler|spider|crawl|slurp|scan|monitor|preview|fetch|http-?client", re.I)

#: Browsers, most specific first: nearly all of them also say "Chrome" and "Safari".
_BROWSERS: tuple[tuple[str, str], ...] = (
    (r"(?:Edg|EdgA|EdgiOS)/([\d.]+)", "Edge"),
    (r"YaBrowser/([\d.]+)", "Яндекс Браузер"),
    (r"(?:OPR|OPT|Opera)/([\d.]+)", "Opera"),
    (r"SamsungBrowser/([\d.]+)", "Samsung Internet"),
    (r"MiuiBrowser/([\d.]+)", "Mi Browser"),
    (r"HuaweiBrowser/([\d.]+)", "Huawei Browser"),
    (r"UCBrowser/([\d.]+)", "UC Browser"),
    (r"Vivaldi/([\d.]+)", "Vivaldi"),
    (r"DuckDuckGo/([\d.]+)", "DuckDuckGo"),
    (r"(?:Firefox|FxiOS)/([\d.]+)", "Firefox"),
    (r"(?:Chrome|CriOS)/([\d.]+)", "Chrome"),
    (r"Version/([\d.]+).*Safari/", "Safari"),
    (r"(?:MSIE |Trident/.*rv:)([\d.]+)", "Internet Explorer"),
)
_BROWSER_RULES = tuple((re.compile(pattern), name) for pattern, name in _BROWSERS)
#: Apps that open pages inside themselves; shown next to the engine they use.
_IN_APP = (
    (re.compile(r"Telegram", re.I), "Telegram"),
    (re.compile(r"Instagram"), "Instagram"),
    (re.compile(r"FBAN|FBAV"), "Facebook"),
    (re.compile(r"; wv\)"), "WebView"),
)


@dataclass(frozen=True)
class DeviceTraits:
    os_name: str | None = None
    os_version: str | None = None
    browser_name: str | None = None
    browser_version: str | None = None
    #: desktop, mobile, tablet, messenger, bot or other.
    device_type: str = "other"
    device_model: str | None = None
    is_bot: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _short_version(value: str | None, parts: int = 2) -> str | None:
    """«142.0.7444.60» → «142», «17.5.1» → «17.5»: trailing zeros say nothing."""
    if not value:
        return None
    numbers = [piece for piece in re.split(r"[._]", value) if piece.isdigit()][:parts]
    while len(numbers) > 1 and numbers[-1] == "0":
        numbers.pop()
    return ".".join(numbers) or None


def _unquote(value: str | None) -> str | None:
    text = (value or "").strip().strip('"').strip()
    return text or None


def _major(value: str | None) -> int | None:
    match = re.match(r"\s*\"?(\d+)", value or "")
    return int(match.group(1)) if match else None


def _windows_version(nt: str, platform_version: str | None) -> str:
    if nt != "10.0":
        return _WINDOWS_NT.get(nt, nt)
    # Windows 10 and 11 send the same «Windows NT 10.0». Only the browser's own
    # report tells them apart: its platform version is 13 or more on Windows 11.
    major = _major(platform_version)
    if major is None:
        return "10/11"
    return "11" if major >= 13 else "10"


def _tool(agent: str) -> tuple[str, str | None] | None:
    for rule, name in _TOOL_RULES:
        match = rule.search(agent)
        if match:
            version = re.search(r"[/ ]v?(\d+(?:\.\d+)*)", agent[match.end() :][:20])
            return name, _short_version(version.group(1)) if version else None
    if _GENERIC_BOT.search(agent) and "Mozilla/5.0 (" not in agent:
        return agent.split("/", 1)[0].strip()[:40] or "Бот", None
    if _GENERIC_BOT.search(agent) and re.search(r"compatible;\s*([^;/)]+)", agent):
        return re.search(r"compatible;\s*([^;/)]+)", agent).group(1).strip()[:40], None
    return None


def _os(agent: str, platform_version: str | None) -> tuple[str | None, str | None, str | None]:
    """(name, version, model) of the system named in a User-Agent."""
    match = re.search(r"Windows NT ([\d.]+)", agent)
    if match:
        return "Windows", _windows_version(match.group(1), platform_version), None
    match = re.search(r"Android(?: ([\d.]+))?", agent)
    if match:
        model = re.search(r"Android[^;)]*;\s*([^;)]+?)(?:\s+Build/[^;)]*)?[;)]", agent)
        name = model.group(1).strip() if model else None
        # New Chrome hides the model behind «K»; a locale is not a model either.
        if name and (name in {"K", "Mobile", "Tablet", "wv"} or re.fullmatch(r"[a-z]{2}[-_][A-Za-z]{2}", name)):
            name = None
        version = _short_version(platform_version) if _major(platform_version) else None
        return "Android", version or _short_version(match.group(1)), name
    match = re.search(r"(iPhone|iPad|iPod).*? OS (\d+(?:_\d+)*)", agent)
    if match:
        system = "iPadOS" if match.group(1) == "iPad" else "iOS"
        return system, _short_version(match.group(2)), match.group(1)
    match = re.search(r"Mac OS X (\d+(?:[._]\d+)*)", agent)
    if match:
        # Browsers froze this at 10.15.7; the report carries the real one.
        reported = _short_version(platform_version) if _major(platform_version) else None
        return "macOS", reported or _short_version(match.group(1)), None
    if "CrOS" in agent:
        return "ChromeOS", None, None
    if "HarmonyOS" in agent:
        return "HarmonyOS", None, None
    if re.search(r"Linux|X11|Ubuntu|Fedora", agent):
        return "Linux", None, None
    return None, None, None


def _device_type(agent: str, system: str | None, mobile_hint: str | None) -> str:
    if re.search(r"iPad|Tablet|PlayBook|Silk", agent) or (
        system == "Android" and "Mobile" not in agent
    ):
        return "tablet"
    if mobile_hint == "?1" or re.search(r"Mobi|iPhone|iPod|Android", agent):
        return "mobile"
    if system in {"Windows", "macOS", "Linux", "ChromeOS"}:
        return "desktop"
    return "other"


def parse_user_agent(user_agent: str | None, hints: dict | None = None) -> DeviceTraits:
    """Read the system, browser and kind of device from a User-Agent.

    ``hints`` are the browser's own words about itself: the Sec-CH-UA-* headers
    or the report from the page (``platform``, ``platform_version``, ``model``,
    ``mobile``). They refine what the frozen User-Agent no longer says.
    """
    agent = (user_agent or "").strip()[:USER_AGENT_LIMIT]
    hints = hints or {}
    if not agent:
        return DeviceTraits()
    if agent == TELEGRAM_AGENT:
        return DeviceTraits(browser_name="Telegram", device_type="messenger")

    tool = _tool(agent)
    if tool:
        system, version, _ = _os(agent, None)
        return DeviceTraits(
            os_name=system,
            os_version=version,
            browser_name=tool[0],
            browser_version=tool[1],
            device_type="bot",
            is_bot=True,
        )

    platform_version = _unquote(hints.get("platform_version"))
    system, version, model = _os(agent, platform_version)
    if system is None:
        system = _unquote(hints.get("platform"))

    browser = browser_version = None
    for rule, name in _BROWSER_RULES:
        match = rule.search(agent)
        if match:
            browser, browser_version = name, _short_version(match.group(1), parts=1)
            break
    for rule, name in _IN_APP:
        if rule.search(agent):
            browser = f"{name} ({browser})" if browser and browser != name else name
            break

    reported_model = _unquote(hints.get("model"))
    return DeviceTraits(
        os_name=system,
        os_version=version,
        browser_name=browser,
        browser_version=browser_version,
        device_type=_device_type(agent, system, _unquote(hints.get("mobile"))),
        device_model=(reported_model or model or None),
        is_bot=False,
    )


def header_hints(headers) -> dict:
    """The low-entropy client hints a browser sends by itself."""
    return {
        "platform": headers.get("sec-ch-ua-platform"),
        "platform_version": headers.get("sec-ch-ua-platform-version"),
        "model": headers.get("sec-ch-ua-model"),
        "mobile": headers.get("sec-ch-ua-mobile"),
    }


def clean_uuid(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    return text if _UUID.match(text) else None


def is_identified(device_key: str | None) -> bool:
    """True when the browser named itself; false for a key made from its headers."""
    return bool(device_key) and not device_key.startswith(DERIVED_PREFIX)


def device_key_for(device_id: str | None, ip: str | None, user_agent: str | None, language: str | None) -> str:
    """The key a device goes by in the journal.

    The site gives every browser an id of its own and sends it with each request.
    A client that sends none (a script, a bot, a callback from another server) is
    told apart by its address and headers: rougher, and marked as such by the prefix.
    """
    own = clean_uuid(device_id)
    if own:
        return own
    raw = "|".join([ip or "-", (user_agent or "")[:USER_AGENT_LIMIT], (language or "")[:100]])
    return DERIVED_PREFIX + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:40]


#: The traits that stay the same when a browser forgets its id (cleared storage,
#: a private window, another profile): the machine itself.
_FINGERPRINT_FIELDS = (
    "platform",
    "architecture",
    "bitness",
    "model",
    "gpu",
    "screen_width",
    "screen_height",
    "color_depth",
    "pixel_ratio",
    "cpu_cores",
    "memory_gb",
    "touch_points",
    "timezone",
    "languages",
)


def fingerprint(report: dict | None) -> str | None:
    """A hash of the machine's own traits, to find it again under a new id.

    Two identical office laptops give the same hash, so this says "looks like
    the same machine", never "is".
    """
    if not report:
        return None
    # Too little to tell machines apart without the screen or the graphics card.
    if not report.get("gpu") and not report.get("screen_width"):
        return None
    raw = "|".join(str(report.get(field, "")) for field in _FINGERPRINT_FIELDS)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]
