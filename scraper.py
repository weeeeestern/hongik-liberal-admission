import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://fm.hongik.ac.kr/fm/0401.do"
NOTIFIED_FILE = Path("notified.json")
KEYWORDS = ["학과 진입", "진입", "자율전공 진입", "admission", "진입 신청"]
REQUEST_TIMEOUT = 20

# Gmail SMTP settings requested by user.
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
USE_STARTTLS = True


def load_notified_urls() -> set:
    if not NOTIFIED_FILE.exists():
        return set()

    try:
        data = json.loads(NOTIFIED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(item for item in data if isinstance(item, str))
    except json.JSONDecodeError:
        pass

    return set()


def save_notified_urls(urls: set) -> None:
    NOTIFIED_FILE.write_text(
        json.dumps(sorted(urls), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_notice_list() -> List[Dict[str, str]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; HongikNoticeScraper/1.0)",
    }
    response = requests.get(BASE_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # If the website layout changes, adjust these selectors first:
    # current live structure uses .bn-list-common.documents-list tbody tr,
    # title text in span.b-title, and detail link in .b-title-box a[href].
    rows = soup.select(".bn-list-common.documents-list tbody tr")

    notices = []
    for row in rows:
        link_el = row.select_one(".b-title-box a[href]")
        title_el = row.select_one(".b-title-box .b-title")

        if not link_el:
            continue

        href = link_el.get("href", "").strip()
        if not href:
            continue

        title = title_el.get_text(" ", strip=True) if title_el else link_el.get_text(" ", strip=True)
        notice_url = urljoin(BASE_URL, href)
        notices.append({"title": title, "url": notice_url})

    return notices


def fetch_notice_content(notice_url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; HongikNoticeScraper/1.0)",
    }
    response = requests.get(notice_url, headers=headers, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    content_el = soup.select_one(".bn-view-common .b-content-box .fr-view")
    if content_el:
        return content_el.get_text(" ", strip=True)

    # Fallback for unexpected structure changes.
    return soup.get_text(" ", strip=True)


def contains_keyword(text: str) -> bool:
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in KEYWORDS)


def send_email_alert(title: str, url: str) -> None:
    sender = os.getenv("SMTP_SENDER")
    password = os.getenv("SMTP_PASSWORD")
    receiver = os.getenv("SMTP_RECEIVER")

    if not sender or not password or not receiver:
        raise RuntimeError("Missing SMTP_SENDER, SMTP_PASSWORD, or SMTP_RECEIVER environment variable.")

    msg = MIMEMultipart()
    msg["Subject"] = f"[Hongik Notice Alert] {title}"
    msg["From"] = sender
    msg["To"] = receiver

    body = f"새 공지 키워드 감지\n\n제목: {title}\n링크: {url}"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if USE_STARTTLS:
            server.starttls()
        server.login(sender, password)
        server.send_message(msg)


def send_slack_alert(title: str, url: str) -> None:
    webhook_url = os.getenv("SLACK_WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("Missing SLACK_WEBHOOK_URL environment variable.")

    payload = {
        "text": "*Hongik Notice Keyword Alert*\n"
        f"• *Title:* {title}\n"
        f"• *Link:* {url}"
    }
    response = requests.post(webhook_url, json=payload, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()


def main() -> int:
    notified_urls = load_notified_urls()

    try:
        notices = fetch_notice_list()
    except Exception as exc:
        print(f"[ERROR] Failed to fetch notice list: {exc}")
        return 1

    matched_notices = []

    for notice in notices:
        title = notice["title"]
        url = notice["url"]

        if url in notified_urls:
            continue

        try:
            title_matched = contains_keyword(title)
            content_matched = False

            if not title_matched:
                content_text = fetch_notice_content(url)
                content_matched = contains_keyword(content_text)

            if title_matched or content_matched:
                matched_notices.append(notice)
        except Exception as exc:
            print(f"[WARN] Failed to inspect notice content ({url}): {exc}")

    if not matched_notices:
        print("No new keyword-matching notices found.")
        save_notified_urls(notified_urls)
        return 0

    for notice in matched_notices:
        title = notice["title"]
        url = notice["url"]

        print(f"[ALERT] {title} -> {url}")

        email_ok = True
        slack_ok = True

        try:
            send_email_alert(title, url)
        except Exception as exc:
            email_ok = False
            print(f"[ERROR] Email notification failed for {url}: {exc}")

        try:
            send_slack_alert(title, url)
        except Exception as exc:
            slack_ok = False
            print(f"[ERROR] Slack notification failed for {url}: {exc}")

        if email_ok or slack_ok:
            notified_urls.add(url)
        else:
            print(f"[WARN] Skipping state update for {url} because all notifications failed.")

    save_notified_urls(notified_urls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
