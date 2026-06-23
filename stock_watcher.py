#!/usr/bin/env python3
"""Autonomous stock watcher for Ravensburger / Disney Lorcana product pages.

Polls a list of product URLs, determines whether each is in stock, persists the
last-known state in git-backed snapshots, and fires a notification (ntfy + optional
email/desktop) whenever availability changes: out of stock, in stock, or only a
few left.

Designed to be run on a schedule (every 3 minutes via cron-job.org → GitHub
Actions, or locally via launchd). It has no third-party dependencies; it uses
only the Python standard library.

Configuration (SMTP credentials, recipients) is read from environment variables,
optionally loaded from a local `.env` file living next to this script. Secrets
are never hardcoded and never logged in full.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

# --- Products to watch ------------------------------------------------------
# Edit this list to add/remove products. The `name` is only used in messages.
PRODUCTS = [
    {
        "name": "Disney Lorcana TCG: Wilds Unknown Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-wilds-unknownbooster-pack-display-11098887",
    },
    {
        "name": "Disney Lorcana TCG: Winterspell Booster Pack Display - 24 Count",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-winterspell-booster-pack-display-24-count-11098881",
    },
    {
        "name": "Disney Lorcana TCG: Whispers in the Well Booster Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-whispers-in-the-well-booster-display--11098812",
    },
    {
        "name": "Disney Lorcana TCG: Fabled Booster Display Box",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-fabled-booster-display-box-11098639",
    },
    {
        "name": "Disney Lorcana TCG: Reign of Jafar Booster Display Box - 24 Count",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-reign-of-jafar-booster-display-box-24-count-11098558",
    },
    {
        "name": "Disney Lorcana TCG: Archazia's Island Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-archazias-island-booster-pack-display-11098557",
    },
    {
        "name": "Disney Lorcana TCG: Azurite Sea Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-azurite-sea-booster-pack-display--11098466",
    },
    {
        "name": "Disney Lorcana TCG: Shimmering Skies Booster Pack Display - 24 Count",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-shimmering-skies-booster-pack-display-24-count-11098455",
    },
    {
        "name": "Disney Lorcana TCG: Ursula's Return Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-ursulas-return-booster-pack-display-11098342",
    },
    {
        "name": "Disney Lorcana TCG: Into the Inklands Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-into-the-inklands-booster-pack-display-11098312",
    },
    {
        "name": "Disney Lorcana TCG: Wilds Unknown Sleeved Booster Pack",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-wilds-unknownsleeved-booster-pack-11098862",
    },
    {
        "name": "Disney Lorcana TCG: Wilds Unknown Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/disney-lorcana-tcg-wilds-unknownillumineers-trove--11098864",
    },
    {
        "name": "Disney Lorcana TCG: Winterspell Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/winterspell-illumineers-trove-english-11098840",
    },
    {
        "name": "Disney Lorcana TCG: Winterspell Sleeved Booster Pack",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/winterspell-sleeved-booster-pack-english-11098838",
    },
    {
        "name": "Disney Lorcana TCG: Winterspell Collection Starter Set",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/starter-decks/winterspell-collection-starter-set-11090026",
    },
    {
        "name": "Disney Lorcana TCG: Whispers in the Well Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/whispers-in-the-well-illumineers-trove-11098786",
    },
    {
        "name": "Disney Lorcana TCG: Whispers in the Well Sleeved Booster Packs",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/whispers-in-the-well-sleeved-booster-packs-11098784",
    },
    {
        "name": "Disney Lorcana TCG: Fabled Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/fabled-illumineers-trove-11098613",
    },
    {
        "name": "Disney Lorcana TCG: Reign of Jafar Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/reign-of-jafar-illumineers-trove-english-11098510",
    },
    {
        "name": "Disney Lorcana TCG: Archazia's Island Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/archazias-island-illumineers-trove-11098509",
    },
    {
        "name": "Disney Lorcana TCG: Azurite Sea Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/azurite-sea-illumineers-trove-11098432",
    },
    {
        "name": "Disney Lorcana TCG: Shimmering Skies Sleeved Booster Packs",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/shimmering-skies-sleeved-booster-packs-11098394",
    },
    {
        "name": "Disney Lorcana TCG: Shimmering Skies Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/-shimmering-skies-trove-11098396",
    },
    {
        "name": "Disney Lorcana TCG: Ursula's Return Illumineer's Trove",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/trove-packs/ursulas-return-trove-pack-11098352",
    },
    {
        "name": "Disney Lorcana TCG: Ursula's Return Sleeved Booster Packs",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/ursulas-return-sleeved-booster-packs-11098350",
    },
    {
        "name": "Disney Lorcana TCG: Into the Inklands Sleeved Booster Packs",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/into-the-inklands-sleeved-booster-packs-11098290",
    },
    {
        "name": "Disney Lorcana TCG: The First Chapter Booster Pack",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/the-first-chapter-booster-pack-11098172",
    },
]

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = SCRIPT_DIR / "state"
STATE_FILE = STATE_DIR / "current.json"
STATE_HISTORY_DIR = STATE_DIR / "history"
LEGACY_STATE_FILE = SCRIPT_DIR / "state.json"
ENV_FILE = SCRIPT_DIR / ".env"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("stock_watcher")


# --- Config / env -----------------------------------------------------------
def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines from a .env file into os.environ.

    Existing environment variables take precedence (so launchd / shell exports
    can override the file). Lines starting with '#' and blank lines are ignored.
    """
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        log.warning("Could not read .env file: %s", exc)


def env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# --- State ------------------------------------------------------------------
def _read_json_file(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _stock_signature(state: dict) -> dict[str, dict[str, object]]:
    """Comparable view of stock status per product URL (ignores last_checked)."""
    signature: dict[str, dict[str, object]] = {}
    for url, entry in state.items():
        signature[url] = {
            "stock_level": entry_stock_level(entry),
            "signal": entry.get("signal"),
        }
    return signature


def entry_stock_level(entry: dict) -> str | None:
    """Return out/in/low for a state entry, migrating legacy in_stock booleans."""
    level = entry.get("stock_level")
    if level in {"out", "in", "low"}:
        return level
    if "in_stock" in entry:
        return "in" if entry["in_stock"] else "out"
    return None


def stock_level_label(level: str) -> str:
    return {"out": "out of stock", "in": "in stock", "low": "only a few left"}[level]


def stock_status_changed(before: dict, after: dict) -> bool:
    return _stock_signature(before) != _stock_signature(after)


def load_state() -> dict:
    for path in (STATE_FILE, LEGACY_STATE_FILE):
        if not path.exists():
            continue
        try:
            state = _read_json_file(path)
            if path is LEGACY_STATE_FILE:
                log.info("Migrating legacy state file to %s", STATE_FILE)
                save_state(state, before={})
            return state
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read state file %s, starting fresh: %s", path, exc)
            return {}
    return {}


def save_history_snapshot(state: dict) -> None:
    """Persist an immutable snapshot when stock status changes."""
    STATE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = STATE_HISTORY_DIR / f"{timestamp}.json"
    tmp = snapshot.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(snapshot)
        log.info("Saved state history snapshot: %s", snapshot.name)
    except OSError as exc:
        log.error("Could not write history snapshot: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def save_state(state: dict, before: dict | None = None) -> None:
    """Write current state atomically; archive a snapshot when stock status changes."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    previous = before if before is not None else (load_state() if STATE_FILE.exists() else {})
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
        if stock_status_changed(previous, state):
            save_history_snapshot(state)
    except OSError as exc:
        log.error("Could not write state file: %s", exc)
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# --- Fetch / parse ----------------------------------------------------------
def fetch_html(url: str) -> str:
    # SECURITY-REVIEW: external HTTP GET to a fixed, code-defined product URL.
    # URLs are not user-controlled at runtime; only read, never executed.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


_AVAILABILITY_RE = re.compile(
    r'"availability"\s*:\s*"https?://schema\.org/([A-Za-z]+)"', re.IGNORECASE
)
_OUT_OF_STOCK_TEXT_RE = re.compile(r"currently out of stock", re.IGNORECASE)
_UNAVAILABLE_TEXT_RE = re.compile(r"\bunavailable\b", re.IGNORECASE)
_IN_STOCK_TEXT_RE = re.compile(r"\bin stock\b", re.IGNORECASE)
_FEW_LEFT_TEXT_RE = re.compile(r"only a few left", re.IGNORECASE)


def parse_stock_status(html: str) -> tuple[str | None, str]:
    """Return (stock_level, raw_signal).

    stock_level is out/in/low when determinable, or None if the page could not
    be interpreted (treated as "unknown" and ignored for transition detection).
    """
    match = _AVAILABILITY_RE.search(html)
    if match:
        availability = match.group(1)
        normalized = availability.lower()
        signal = f"schema.org/{availability}"
        out_states = {"outofstock", "soldout", "discontinued"}
        if normalized in out_states:
            return "out", signal
        if normalized == "limitedavailability":
            return "low", signal
        # Ravensburger often lags schema.org updates; trust visible stock text.
        if _OUT_OF_STOCK_TEXT_RE.search(html):
            return "out", "text:currently out of stock"
        if _UNAVAILABLE_TEXT_RE.search(html):
            return "out", "text:unavailable"
        if _FEW_LEFT_TEXT_RE.search(html):
            return "low", "text:only a few left"
        return "in", signal

    # Fallback to visible text if structured data is missing.
    if _OUT_OF_STOCK_TEXT_RE.search(html):
        return "out", "text:currently out of stock"
    if _UNAVAILABLE_TEXT_RE.search(html):
        return "out", "text:unavailable"
    if _FEW_LEFT_TEXT_RE.search(html):
        return "low", "text:only a few left"
    if _IN_STOCK_TEXT_RE.search(html):
        return "in", "text:in stock"

    return None, "unknown"


# --- Notifications ----------------------------------------------------------
def send_desktop_notification(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        safe_title = title.replace('"', "'")
        safe_message = message.replace('"', "'")
        script = (
            f'display notification "{safe_message}" with title "{safe_title}" '
            f'sound name "Glass"'
        )
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("Desktop notification failed: %s", exc)


def send_email(subject: str, body: str) -> bool:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    recipients = os.environ.get("ALERT_TO", "")
    sender = os.environ.get("ALERT_FROM", user or "")

    to_list = [addr.strip() for addr in recipients.split(",") if addr.strip()]

    if not (host and to_list and sender):
        log.info(
            "Email not configured (need SMTP_HOST, ALERT_TO, and a sender); "
            "skipping email."
        )
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    use_tls = env_bool("SMTP_USE_TLS", True)  # STARTTLS on submission port
    use_ssl = env_bool("SMTP_USE_SSL", port == 465)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    msg.set_content(body)

    try:
        # SECURITY-REVIEW: outbound SMTP using credentials from env vars only.
        if use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=REQUEST_TIMEOUT, context=context) as server:
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=REQUEST_TIMEOUT) as server:
                server.ehlo()
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        log.info("Email sent to %s", ", ".join(to_list))
        return True
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # Never log credentials; only the error type/message.
        log.error("Failed to send email: %s", exc)
        return False


def send_ntfy(
    title: str,
    message: str,
    click_url: str | None = None,
    *,
    tags: str = "shopping_cart",
) -> bool:
    """Send a push notification via ntfy (https://ntfy.sh) — no credentials.

    Subscribe to the same NTFY_TOPIC in the ntfy mobile/web app to receive it.
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    endpoint = f"{server}/{topic}"

    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "high",
        "Tags": tags,
    }
    if click_url:
        headers["Click"] = click_url

    # Optional token auth for protected/self-hosted topics.
    token = os.environ.get("NTFY_TOKEN", "").strip()
    if token:
        # SECURITY-REVIEW: bearer token read from env only; never logged.
        headers["Authorization"] = f"Bearer {token}"

    try:
        # SECURITY-REVIEW: outbound POST to a fixed ntfy endpoint; topic is the
        # only identifier and is read from env, not user input at runtime.
        req = urllib.request.Request(
            endpoint, data=message.encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
        if ok:
            log.info("Push sent via ntfy to topic '%s'", topic)
        return ok
    except (urllib.error.URLError, OSError) as exc:
        log.error("Failed to send ntfy push: %s", exc)
        return False


def notify_stock_change(
    name: str,
    url: str,
    signal: str,
    *,
    stock_level: str,
    prev_stock_level: str,
) -> None:
    purchasable = stock_level in {"in", "low"}
    if stock_level == "low":
        subject = f"LOW STOCK: {name}"
        short = f"{name} — only a few left!"
        ntfy_title = f"LOW STOCK: {name}"
        ntfy_message = f"{name} is down to only a few left. Tap to buy."
        desktop_title = "Low stock!"
        desktop_message = f"{name} — only a few left"
        tags = "warning"
    elif stock_level == "in":
        subject = f"IN STOCK: {name}"
        short = f"{name} is now IN STOCK."
        ntfy_title = f"IN STOCK: {name}"
        ntfy_message = f"{name} is now in stock. Tap to buy."
        desktop_title = "Back in stock!"
        desktop_message = f"{name} — open to buy"
        tags = "shopping_cart"
    else:
        subject = f"OUT OF STOCK: {name}"
        short = f"{name} is now OUT OF STOCK."
        ntfy_title = f"OUT OF STOCK: {name}"
        ntfy_message = f"{name} is no longer in stock."
        desktop_title = "Sold out"
        desktop_message = f"{name} — now unavailable"
        tags = "warning"

    body = (
        f"{short}\n\n"
        f"Previous status: {stock_level_label(prev_stock_level)}\n"
        f"Current status: {stock_level_label(stock_level)}\n"
        f"Product page: {url}\n\n"
        f"Detected signal: {signal}\n"
        f"Time: {datetime.now(timezone.utc).astimezone().isoformat()}\n"
    )
    log.info(
        "ALERT: %s stock changed %s -> %s (%s)",
        name,
        prev_stock_level,
        stock_level,
        signal,
    )
    if env_bool("ENABLE_DESKTOP_NOTIFICATION", True):
        send_desktop_notification(desktop_title, desktop_message)
    send_ntfy(ntfy_title, ntfy_message, url if purchasable else None, tags=tags)
    send_email(subject, body)


# --- Main -------------------------------------------------------------------
def check_product(product: dict, state: dict) -> None:
    name = product["name"]
    url = product["url"]
    key = url

    try:
        html = fetch_html(url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.warning("Fetch failed for %s: %s", name, exc)
        return

    stock_level, signal = parse_stock_status(html)
    if stock_level is None:
        log.warning("Could not determine stock status for %s (signal=%s)", name, signal)
        return

    prev = state.get(key, {})
    prev_stock_level = entry_stock_level(prev)

    status_str = stock_level_label(stock_level).upper()
    if prev_stock_level is None:
        log.info("%s: %s (%s) [no saved state — baselining]", name, status_str, signal)
    elif prev_stock_level == stock_level:
        log.info(
            "%s: %s (%s) [unchanged; saved state was %s]",
            name,
            status_str,
            signal,
            stock_level_label(prev_stock_level),
        )
    else:
        log.info(
            "%s: %s (%s) [changed from saved %s]",
            name,
            status_str,
            signal,
            stock_level_label(prev_stock_level),
        )

    # Notify only on a real delta from a known previous status (skip first run
    # for newly added products so we don't spam alerts for the current state).
    if prev_stock_level is not None and prev_stock_level != stock_level:
        notify_stock_change(
            name,
            url,
            signal,
            stock_level=stock_level,
            prev_stock_level=prev_stock_level,
        )

    state[key] = {
        "name": name,
        "stock_level": stock_level,
        "in_stock": stock_level in {"in", "low"},
        "signal": signal,
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    load_env_file(ENV_FILE)

    args = sys.argv[1:]
    if "--test" in args or "--test-email" in args:
        results = []
        if env_bool("ENABLE_DESKTOP_NOTIFICATION", True):
            send_desktop_notification("Stock Watcher", "Test notification")
            results.append("desktop notification fired")

        ntfy_ok = send_ntfy(
            "Stock Watcher test",
            "If you see this on your phone, ntfy push notifications work.",
            PRODUCTS[0]["url"],
        )
        if os.environ.get("NTFY_TOPIC", "").strip():
            results.append("ntfy push: " + ("OK" if ntfy_ok else "FAILED"))

        email_ok = None
        if os.environ.get("SMTP_HOST"):
            email_ok = send_email(
                "Stock Watcher test email",
                "If you're reading this, your Stock Watcher email setup works.",
            )
            results.append("email: " + ("OK" if email_ok else "FAILED"))

        print("Test results -> " + "; ".join(results) if results else "No channels configured.")
        any_remote_failed = (ntfy_ok is False and os.environ.get("NTFY_TOPIC", "").strip()) or email_ok is False
        return 1 if any_remote_failed else 0

    state = load_state()
    if STATE_FILE.exists():
        log.info(
            "Loaded saved state for %d product(s) from %s",
            len(state),
            STATE_FILE.name,
        )
    else:
        log.info("No saved state file yet — products will be baselined this run")
    previous_state = json.loads(json.dumps(state))
    for product in PRODUCTS:
        check_product(product, state)
    save_state(state, before=previous_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
