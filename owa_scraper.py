"""
Outlook Web Email Scraper — core scraping module.

CLI usage (fallback):
    python owa_scraper.py --login
    python owa_scraper.py

Programmatic usage (from app.py / Flask):
    from owa_scraper import login_flow, run_scrape
    login_flow(session_file=Path('owa_session.json'))
    output_path = run_scrape(config, progress_cb=my_fn)
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ====== CLI DEFAULTS (used only when run directly) ======
OWA_URL = "https://outlook.office.com/mail/"

SENDERS = [
    "aws-marketing-email-replies@amazon.com",
    "noreply@github.com",
    "oracle-acct_ww@oracle.com",
]

MAX_EMAILS_PER_SENDER = 100
LOOKBACK_MONTHS = 3
OUTPUT_DIR = Path("./output")
SESSION_FILE = Path("./owa_session.json")
HEADLESS = False
# ========================================================


def login_flow(session_file: Path = SESSION_FILE):
    """Open a real browser window so the user can sign in. Saves session for reuse."""
    print("Opening browser. Sign in to Outlook — the window will close automatically.")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(OWA_URL)

        print("Waiting for sign-in... (up to 5 minutes)")
        try:
            page.wait_for_selector(
                '[aria-label*="Search" i], [placeholder*="Search" i]',
                timeout=300_000,
            )
            print("Inbox detected. Saving session.")
        except PWTimeout:
            print("Did not detect inbox in time. Saving session anyway.")

        context.storage_state(path=str(session_file))
        browser.close()
    print(f"Session saved to {session_file}.")


def _get_search_input(page):
    """
    Return the real search text <input> in OWA.
    Must use specific input selectors to avoid matching the 'Exit search' button.
    """
    candidates = [
        'input[type="search"]',
        'input[aria-label*="Search" i]',
        'input[placeholder*="Search" i]',
        '[role="combobox"] input',
        'input[id*="search" i]',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        if loc.count() > 0:
            try:
                if loc.is_visible():
                    return loc
            except Exception:
                continue
    return None


def safe_text(page, selector: str) -> str:
    """Return inner text of the first matching element, or empty string."""
    try:
        loc = page.locator(selector).first
        if loc.count() > 0:
            return loc.inner_text(timeout=2000).strip()
    except Exception:
        pass
    return ""


def scrape_sender(
    page,
    sender: str,
    max_emails: int = 100,
    log=print,
    mode: str = "count",
    lookback_months: int = 3,
):
    """Search for emails from one sender (same scroll/count logic as Desktop owa_scraper.py)."""
    log(f"\n--- Searching: {sender} ---")
    emails = []
    cutoff = None
    if mode == "months":
        cutoff = (pd.Timestamp.now() - pd.DateOffset(months=lookback_months)).normalize()
        log(f"  Date range: last {lookback_months} month(s) (on or after {cutoff.date()})")

    try:
        page.keyboard.press("Escape")
        time.sleep(0.8)
        page.keyboard.press("Escape")
        time.sleep(0.8)
    except Exception:
        pass

    exit_btn = page.locator(
        'button[title*="Exit search" i], button[aria-label*="Exit search" i]'
    ).first
    if exit_btn.count() > 0:
        try:
            exit_btn.click()
            time.sleep(1.5)
        except Exception:
            pass

    search_input = _get_search_input(page)
    if search_input is None:
        log(f"  Could not find search input for {sender}, skipping.")
        return emails

    search_input.click()
    search_input.fill("")
    search_input.type(f"from:{sender}", delay=30)
    search_input.press("Enter")

    time.sleep(3)

    seen_ids: set = set()
    last_count = -1
    stagnant_rounds = 0
    stop_scrolling = False

    while len(emails) < max_emails and stagnant_rounds < 3 and not stop_scrolling:
        rows = page.locator('div[role="option"], div[role="listitem"]').all()
        saw_old_in_batch = False

        for row in rows:
            if len(emails) >= max_emails:
                break

            try:
                row_aria = row.get_attribute("aria-label") or ""
                row_id = (
                    row.get_attribute("data-convid")
                    or row_aria
                    or (row.text_content() or "")[:80]
                )
            except Exception:
                continue

            if not row_id or row_id in seen_ids:
                continue
            seen_ids.add(row_id)

            # List row date is best for sort order (newest -> oldest)
            parsed_date = _date_from_row_aria(row_aria)

            try:
                row.click()
                time.sleep(1.2)

                subject = (
                    safe_text(page, '[role="heading"][aria-level="2"]')
                    or safe_text(page, '[aria-label*="Subject" i]')
                )
                from_field = safe_text(page, '[aria-label*="From" i]')
                date_field = safe_text(
                    page,
                    '[aria-label*="Received" i], [aria-label*="Sent" i]',
                )
                body = safe_text(
                    page,
                    '[aria-label*="Message body" i], [role="document"]',
                )

                if pd.isna(parsed_date):
                    parsed_date = _parse_owa_date(date_field)

                if cutoff is not None and pd.notna(parsed_date):
                    if parsed_date.normalize() < cutoff:
                        saw_old_in_batch = True
                        continue

                if cutoff is not None and pd.isna(parsed_date):
                    log(f"  [?] Could not parse date, keeping: {(subject or '')[:40]}")

                emails.append({
                    "sender_searched": sender,
                    "from": from_field,
                    "subject": subject,
                    "date": date_field,
                    "parsed_date": (
                        parsed_date.isoformat()
                        if pd.notna(parsed_date)
                        else ""
                    ),
                    "body": (body or "")[:5000],
                    "scraped_at": datetime.now().isoformat(timespec="seconds"),
                })
                log(f"  [{len(emails)}] {(subject or '(no subject)')[:60]}")
            except Exception as exc:
                log(f"  skipped a row: {exc}")
                continue

        if saw_old_in_batch:
            log(
                f"  Reached emails older than {lookback_months} month(s), "
                f"keeping {len(emails)} in-range email(s) and moving on."
            )
            stop_scrolling = True
            break

        try:
            page.mouse.wheel(0, 2000)
        except Exception:
            pass
        time.sleep(1.5)

        if len(emails) == last_count:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        last_count = len(emails)

    if cutoff is not None:
        log(f"  Collected {len(emails)} in-range email(s) from {sender}")
    else:
        log(f"  Collected {len(emails)} emails from {sender}")
    return emails


def _parse_owa_date(d_str) -> "pd.Timestamp":
    """Parse OWA date strings (list rows, reading pane, relative labels)."""
    if pd.isna(d_str) or not str(d_str).strip():
        return pd.NaT
    d_str = str(d_str).strip()
    now = pd.Timestamp.now()
    lower = d_str.lower()

    if lower == "yesterday":
        return now.normalize() - pd.Timedelta(days=1)
    if lower == "today":
        return now.normalize()

    import re
    from dateutil.parser import parse as du_parse

    # Strip OWA prefixes like "Received: Mon 3/3/2024 10:15 AM"
    cleaned = re.sub(
        r"^(received|sent|date)\s*:\s*",
        "",
        d_str,
        flags=re.IGNORECASE,
    ).strip()

    # Pull first recognizable date token if the string is noisy
    token_match = re.search(
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"
        r"|\b(\d{4}-\d{1,2}-\d{1,2})\b"
        r"|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?\b"
        r"|\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b",
        cleaned,
        re.IGNORECASE,
    )
    to_parse = token_match.group(0) if token_match else cleaned

    try:
        parsed = du_parse(to_parse, fuzzy=True, default=now.to_pydatetime())
        parsed = pd.Timestamp(parsed)
        if parsed > now + pd.Timedelta(days=1):
            parsed -= pd.DateOffset(weeks=1)
        return parsed
    except Exception:
        return pd.to_datetime(cleaned, errors="coerce")


def _date_from_row_aria(aria: str) -> "pd.Timestamp":
    """Try to parse a date from a conversation row aria-label."""
    if not aria:
        return pd.NaT
    import re
    match = re.search(
        r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"
        r"|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b",
        aria,
        re.IGNORECASE,
    )
    if not match:
        return pd.NaT
    return _parse_owa_date(match.group(0))


def run_scrape(config: dict = None, progress_cb=None) -> "Path | None":
    """
    Run the full scrape and save an Excel file.

    config keys:
        senders         list[str]   — email addresses to search
        mode            str         — 'count' or 'months'
        max_emails      int         — max emails per sender (used when mode='count')
        lookback_months int         — keep emails from last N months (used when mode='months')
        output_dir      Path        — where to save the .xlsx
        session_file    Path        — path to owa_session.json
        headless        bool        — run browser headlessly

    progress_cb: callable(str) — receives log messages in real time
    Returns: Path to the generated .xlsx, or None if no emails found.
    """
    if config is None:
        config = {}

    senders     = config.get('senders', SENDERS)
    mode        = config.get('mode', 'count')
    max_emails  = int(config.get('max_emails', MAX_EMAILS_PER_SENDER))
    lookback    = int(config.get('lookback_months', LOOKBACK_MONTHS))
    output_dir  = Path(config.get('output_dir', OUTPUT_DIR))
    session_file = Path(config.get('session_file', SESSION_FILE))
    headless    = config.get('headless', HEADLESS)

    def log(msg: str):
        print(msg)
        if progress_cb:
            progress_cb(msg)

    if not session_file.exists():
        log("No session found. Please log in first.")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    all_emails = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(session_file))
        page = context.new_page()
        page.goto(OWA_URL)

        try:
            page.wait_for_selector(
                '[aria-label*="Search" i], [placeholder*="Search" i]',
                timeout=30_000,
            )
        except PWTimeout:
            log("⚠️ Session looks expired. Please log in again.")
            browser.close()
            return None

        scrape_limit = max_emails if mode == "count" else 5000

        for idx, sender in enumerate(senders):
            try:
                emails = scrape_sender(
                    page,
                    sender,
                    max_emails=scrape_limit,
                    log=log,
                    mode=mode,
                    lookback_months=lookback,
                )
                all_emails.extend(emails)
                if progress_cb:
                    progress_cb(
                        f"  ✅ {sender}: {len(emails)} email(s) found",
                        sender_idx=idx + 1,
                    )
            except Exception as exc:
                log(f"Failed on {sender}: {exc}")

        browser.close()

    if not all_emails:
        log("No emails collected.")
        return None

    df = pd.DataFrame(all_emails)

    if "parsed_date" in df.columns and df["parsed_date"].astype(str).str.len().gt(0).any():
        df["parsed_date"] = pd.to_datetime(df["parsed_date"], errors="coerce")
    else:
        df["parsed_date"] = df["date"].apply(_parse_owa_date)

    if mode == "months":
        cutoff = (pd.Timestamp.now() - pd.DateOffset(months=lookback)).normalize()
        before = len(df)
        in_range = df["parsed_date"].notna() & (
            df["parsed_date"].dt.normalize() >= cutoff
        )
        undated = df["parsed_date"].isna()
        df = df[in_range | undated].copy()
        dropped = before - len(df)
        if dropped:
            log(f"  ℹ️  {dropped} email(s) outside the date range were excluded.")
        if len(df) > 0:
            log(f"  ✅ {len(df)} email(s) kept within the last {lookback} month(s).")
    # For 'count' mode: already limited per sender during scraping.

    if df.empty:
        if mode == "months":
            log(
                f"No emails found within the last {lookback} month(s). "
                "Try a longer date range."
            )
        else:
            log("No emails collected.")
        return None

    df["month"] = df["parsed_date"].dt.strftime("%Y-%m")

    totals = df.groupby("sender_searched").size().reset_index(name="email_count")
    totals = totals.sort_values("email_count", ascending=False)

    dated = df.dropna(subset=["parsed_date"])
    if not dated.empty:
        monthly = (
            dated.groupby(["sender_searched", "month"])
            .size()
            .unstack(fill_value=0)
            .sort_index(axis=1)
        )
        monthly["TOTAL"] = monthly.sum(axis=1)
        monthly = monthly.sort_values("TOTAL", ascending=False).reset_index()
    else:
        monthly = pd.DataFrame(columns=["sender_searched", "TOTAL"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = output_dir / f"emails_{timestamp}.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        totals.to_excel(writer, sheet_name="Summary", index=False)
        monthly.to_excel(writer, sheet_name="Monthly Counts", index=False)
        df.drop(columns=["parsed_date"]).to_excel(writer, sheet_name="All Emails", index=False)

    log(f"\n✅ Saved {len(df)} emails → {xlsx_path.name}")
    log("\nTotal per sender:")
    log(totals.to_string(index=False))

    # Build a summary list for the UI results panel
    summary = [
        {"sender": row["sender_searched"], "count": int(row["email_count"])}
        for _, row in totals.iterrows()
    ]
    return xlsx_path, summary


def main():
    parser = argparse.ArgumentParser(description="OWA Email Scraper")
    parser.add_argument("--login", action="store_true",
                        help="Open browser for sign-in and save session")
    args = parser.parse_args()

    if args.login:
        login_flow()
    else:
        run_scrape()


if __name__ == "__main__":
    main()
