#!/usr/bin/env python3
"""Autonomous stock watcher for Ravensburger / Disney Lorcana product pages.

Polls a list of product URLs, determines whether each is in stock, persists the
last-known state, and fires a notification (email + macOS desktop) only when a
product transitions from out-of-stock to in-stock.

Designed to be run on a schedule (GitHub Actions every 5 minutes, or locally
via launchd). It has no third-party dependencies; it uses only the Python
standard library.

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
        "name": "Disney Lorcana TCG: Fabled Booster Display Box",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-fabled-booster-display-box-11098639",
    },
    {
        "name": "Disney Lorcana TCG: Wilds Unknown Booster Pack Display",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-wilds-unknownbooster-pack-display-11098887",
    },
    {
        "name": "Disney Lorcana TCG: Winterspell Booster Pack Display - 24 Count",
        "url": "https://www.ravensburger.us/en-US/products/disney-lorcana/boosters/disney-lorcana-tcg-winterspell-booster-pack-display-24-count-11098881",
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
            "in_stock": entry.get("in_stock"),
            "signal": entry.get("signal"),
        }
    return signature


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


def parse_stock_status(html: str) -> tuple[bool | None, str]:
    """Return (in_stock, raw_signal).

    in_stock is True/False when determinable, or None if the page could not be
    interpreted (treated as "unknown" and ignored for transition detection).
    """
    match = _AVAILABILITY_RE.search(html)
    if match:
        availability = match.group(1)
        normalized = availability.lower()
        # schema.org: InStock, LimitedAvailability, PreOrder, BackOrder,
        # OnlineOnly, etc. are purchasable; OutOfStock / SoldOut / Discontinued
        # are not.
        out_states = {"outofstock", "soldout", "discontinued"}
        in_stock = normalized not in out_states
        return in_stock, f"schema.org/{availability}"

    # Fallback to visible text if structured data is missing.
    if _OUT_OF_STOCK_TEXT_RE.search(html):
        return False, "text:currently out of stock"

    # Could not find a reliable out-of-stock signal. Don't assume in-stock to
    # avoid false alarms if the page layout changes.
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


def send_ntfy(title: str, message: str, click_url: str | None = None) -> bool:
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
        "Tags": "shopping_cart",
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


def notify_in_stock(name: str, url: str, signal: str) -> None:
    subject = f"IN STOCK: {name}"
    body = (
        f"{name} is now IN STOCK!\n\n"
        f"Buy it here: {url}\n\n"
        f"Detected signal: {signal}\n"
        f"Time: {datetime.now(timezone.utc).astimezone().isoformat()}\n"
    )
    log.info("ALERT: %s is now in stock (%s)", name, signal)
    if env_bool("ENABLE_DESKTOP_NOTIFICATION", True):
        send_desktop_notification("Back in stock!", f"{name} — open to buy")
    send_ntfy(f"IN STOCK: {name}", f"{name} is now in stock. Tap to buy.", url)
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

    in_stock, signal = parse_stock_status(html)
    if in_stock is None:
        log.warning("Could not determine stock status for %s (signal=%s)", name, signal)
        return

    prev = state.get(key, {})
    prev_in_stock = prev.get("in_stock")

    status_str = "IN STOCK" if in_stock else "out of stock"
    log.info("%s: %s (%s)", name, status_str, signal)

    # Transition out-of-stock (or unknown/first-run-as-out) -> in-stock.
    if in_stock and prev_in_stock is not True:
        notify_in_stock(name, url, signal)

    state[key] = {
        "name": name,
        "in_stock": in_stock,
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
    previous_state = json.loads(json.dumps(state))
    for product in PRODUCTS:
        check_product(product, state)
    save_state(state, before=previous_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
