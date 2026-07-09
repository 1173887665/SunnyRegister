from __future__ import annotations

import base64
import dataclasses
import email as email_pkg
import imaplib
import re
import socket
import ssl
import time
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable
from urllib.parse import unquote, urlparse

import requests

from .proxy import normalize_proxy_url


OUTLOOK_IMAP_HOST = "outlook.office365.com"
OUTLOOK_IMAP_PORT = 993
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"


@dataclasses.dataclass
class MailAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str
    raw: str
    account_type: str = "free"
    openai_rt: str = ""


def parse_account_line(line: str) -> MailAccount:
    parts = [p.strip() for p in str(line or "").strip().split("----")]
    if len(parts) < 4:
        raise ValueError("Invalid mailbox line; expected email----password----client_id----refresh_token")
    email, password, client_id, refresh_token = parts[:4]
    if not email or "@" not in email or not client_id or not refresh_token:
        raise ValueError("email / client_id / refresh_token must not be empty")
    openai_rt = ""
    for part in parts[4:]:
        low = part.lower()
        if low.startswith(("rt_token=", "openai_rt=")):
            openai_rt = part.split("=", 1)[1].strip()
    return MailAccount(
        email=email,
        password=password,
        client_id=client_id,
        refresh_token=refresh_token,
        raw="----".join(parts[:4]),
        account_type="plus" if openai_rt else "free",
        openai_rt=openai_rt,
    )


def account_from_row(row: dict[str, Any]) -> MailAccount:
    raw = row.get("raw") or "----".join([
        row.get("email", ""),
        row.get("password", ""),
        row.get("client_id", ""),
        row.get("refresh_token", ""),
    ])
    account = parse_account_line(raw)
    account.openai_rt = row.get("openai_rt") or account.openai_rt
    account.account_type = row.get("account_type") or account.account_type
    return account


def extract_otp(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or ""))
    for pattern in (r"(?<!\d)(\d{6})(?!\d)", r"(?<!\d)(\d{5})(?!\d)", r"(?<!\d)(\d{4})(?!\d)"):
        match = re.search(pattern, normalized)
        if match:
            return match.group(1)
    return ""


TOKEN_ENDPOINTS = [
    {"name": "LIVE", "url": "https://login.live.com/oauth20_token.srf", "scope": ""},
    {"name": "LIVE+scope", "url": "https://login.live.com/oauth20_token.srf", "scope": IMAP_SCOPE},
    {"name": "V1-COMMON", "url": "https://login.microsoftonline.com/common/oauth2/token", "scope": "", "resource": "https://outlook.office.com/"},
    {"name": "V1-CONSUMERS", "url": "https://login.microsoftonline.com/consumers/oauth2/token", "scope": "", "resource": "https://outlook.office.com/"},
    {"name": "CONSUMERS", "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "scope": IMAP_SCOPE},
    {"name": "CONSUMERS-noscope", "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "scope": ""},
    {"name": "COMMON", "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": IMAP_SCOPE},
    {"name": "COMMON-noscope", "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": ""},
]


def refresh_hotmail_access_token(account: MailAccount, proxy_url: str = "", log: Callable[[str], None] | None = None) -> tuple[str, str]:
    errors: list[str] = []
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    for endpoint in TOKEN_ENDPOINTS:
        data = {
            "client_id": account.client_id,
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
        }
        if endpoint.get("scope"):
            data["scope"] = endpoint["scope"]
        if endpoint.get("resource"):
            data["resource"] = endpoint["resource"]
        try:
            if log:
                log(f"[{account.email}] Try Outlook token endpoint {endpoint['name']}")
            resp = requests.post(endpoint["url"], data=data, headers={"Accept": "application/json"}, timeout=20, proxies=proxies)
            payload = resp.json() if resp.text else {}
            if resp.ok and payload.get("access_token"):
                if log:
                    log(f"[{account.email}] Outlook token endpoint {endpoint['name']} succeeded")
                return str(payload["access_token"]), str(endpoint["name"])
            msg = payload.get("error_description") or payload.get("error") or f"HTTP {resp.status_code}"
            errors.append(f"{endpoint['name']}: {msg}")
            if log:
                log(f"[{account.email}] Outlook token endpoint {endpoint['name']} failed: {msg}")
        except Exception as exc:
            errors.append(f"{endpoint['name']}: {exc}")
            if log:
                log(f"[{account.email}] Outlook token endpoint {endpoint['name']} exception: {exc}")
    raise RuntimeError("All Outlook token endpoints failed -> " + " | ".join(errors))


def decode_header_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def html_to_text(value: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text))


def extract_message_text(msg) -> str:
    parts: list[str] = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        parts.append(html_to_text(text) if part.get_content_type() == "text/html" else text)
    return "\n".join(parts)


class ProxiedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, proxied_socket: socket.socket, timeout: float | None = None):
        self._proxied_socket = proxied_socket
        super().__init__(host=host, port=port, timeout=timeout)

    def open(self, host: str = "", port: int = 0, timeout: float | None = None):
        self.host = host
        self.port = port
        self.sock = self._proxied_socket
        self.file = self.sock.makefile("rb")


class HotmailReader:
    """Outlook XOAUTH2 IMAP reader rewritten from the register-machine implementation."""

    def __init__(self, account: MailAccount, log: Callable[[str], None] | None, proxy_url: str = ""):
        self.account = account
        self.log = log or (lambda _m: None)
        self.proxy_url = proxy_url
        self.imap: imaplib.IMAP4_SSL | None = None
        self.seen: set[str] = set()

    def connect(self, access_token: str | None = None) -> None:
        self.log(f"[{self.account.email}] Connecting Outlook IMAP for OTP")
        if access_token is None:
            access_token, _ = refresh_hotmail_access_token(self.account, self.proxy_url, self.log)
        auth = f"user={self.account.email}\x01auth=Bearer {access_token}\x01\x01"
        if self.proxy_url:
            self.imap = self._connect_imap_via_proxy(self.proxy_url)
        else:
            self.imap = imaplib.IMAP4_SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, timeout=20)
            try:
                self.imap.sock.settimeout(20)
            except Exception:
                pass
        self.imap.authenticate("XOAUTH2", lambda _: auth.encode("utf-8"))
        try:
            self.imap.sock.settimeout(30)
        except Exception:
            pass
        self.log(f"[{self.account.email}] Outlook IMAP connected")

    def _connect_imap_via_proxy(self, proxy_url: str) -> imaplib.IMAP4_SSL:
        proxy_url = normalize_proxy_url(proxy_url)
        parsed = urlparse(proxy_url)
        if parsed.scheme in {"socks5", "socks5h"}:
            return self._connect_imap_via_socks5(parsed)
        if parsed.scheme != "http" or not parsed.hostname:
            raise RuntimeError(f"IMAP proxy only supports HTTP CONNECT or SOCKS5: {proxy_url}")
        raw = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=30)
        target = f"{OUTLOOK_IMAP_HOST}:{OUTLOOK_IMAP_PORT}"
        request = [f"CONNECT {target} HTTP/1.1", f"Host: {target}", "Proxy-Connection: keep-alive"]
        if parsed.username:
            token = base64.b64encode(f"{unquote(parsed.username)}:{unquote(parsed.password or '')}".encode("utf-8")).decode("ascii")
            request.append(f"Proxy-Authorization: Basic {token}")
        raw.sendall(("\r\n".join(request) + "\r\n\r\n").encode("latin1"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 65536:
            chunk = raw.recv(4096)
            if not chunk:
                break
            response += chunk
        status = response.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
        if " 200 " not in f" {status} ":
            raw.close()
            raise RuntimeError(f"IMAP proxy CONNECT failed: {status}")
        tls_sock = ssl.create_default_context().wrap_socket(raw, server_hostname=OUTLOOK_IMAP_HOST)
        try:
            tls_sock.settimeout(20)
        except Exception:
            pass
        return ProxiedIMAP4SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, tls_sock, timeout=20)

    def _connect_imap_via_socks5(self, parsed) -> imaplib.IMAP4_SSL:
        if not parsed.hostname:
            raise RuntimeError("SOCKS5 proxy host is empty")
        raw = socket.create_connection((parsed.hostname, parsed.port or 1080), timeout=30)
        try:
            username = unquote(parsed.username or "")
            password = unquote(parsed.password or "")
            if username:
                raw.sendall(b"\x05\x02\x00\x02")
            else:
                raw.sendall(b"\x05\x01\x00")
            resp = raw.recv(2)
            if len(resp) != 2 or resp[0] != 5 or resp[1] == 0xFF:
                raise RuntimeError("SOCKS5 greeting failed")
            if resp[1] == 0x02:
                ub = username.encode("utf-8")
                pb = password.encode("utf-8")
                if len(ub) > 255 or len(pb) > 255:
                    raise RuntimeError("SOCKS5 username/password is too long")
                raw.sendall(b"\x01" + bytes([len(ub)]) + ub + bytes([len(pb)]) + pb)
                auth = raw.recv(2)
                if len(auth) != 2 or auth[1] != 0:
                    raise RuntimeError("SOCKS5 authentication failed")
            host = OUTLOOK_IMAP_HOST.encode("idna")
            port = OUTLOOK_IMAP_PORT.to_bytes(2, "big")
            raw.sendall(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port)
            head = raw.recv(4)
            if len(head) != 4 or head[1] != 0:
                raise RuntimeError(f"SOCKS5 CONNECT failed: {head!r}")
            atyp = head[3]
            if atyp == 1:
                raw.recv(4)
            elif atyp == 3:
                size = raw.recv(1)[0]
                raw.recv(size)
            elif atyp == 4:
                raw.recv(16)
            raw.recv(2)
            tls_sock = ssl.create_default_context().wrap_socket(raw, server_hostname=OUTLOOK_IMAP_HOST)
            try:
                tls_sock.settimeout(20)
            except Exception:
                pass
            return ProxiedIMAP4SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, tls_sock, timeout=20)
        except Exception:
            raw.close()
            raise

    def close(self) -> None:
        if self.imap:
            try:
                self.imap.logout()
            except Exception:
                pass
        self.imap = None

    def _select_folder(self, folder: str) -> bool:
        assert self.imap is not None
        for name in (folder, f'"{folder}"'):
            try:
                status, _ = self.imap.select(name, readonly=True)
                if status == "OK":
                    return True
            except Exception:
                continue
        return False

    def latest_message(self) -> dict[str, Any]:
        assert self.imap is not None
        for folder in ("INBOX", "Junk", "Junk Email"):
            try:
                if not self._select_folder(folder):
                    continue
                status, data = self.imap.search(None, "ALL")
                if status != "OK" or not data or not data[0]:
                    continue
                msg_id = data[0].split()[-1]
                status, msg_data = self.imap.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw = next((item[1] for item in msg_data if isinstance(item, tuple)), None)
                if not raw:
                    continue
                msg = email_pkg.message_from_bytes(raw)
                subject = decode_header_text(msg.get("Subject"))
                body = extract_message_text(msg)
                return {
                    "email": self.account.email,
                    "folder": folder,
                    "subject": subject,
                    "from": decode_header_text(msg.get("From")),
                    "date": msg.get("Date"),
                    "body_preview": body[:1200],
                    "otp": extract_otp(subject + "\n" + body),
                }
            except Exception:
                continue
        return {"email": self.account.email, "empty": True}

    def wait_for_code(self, min_timestamp: float, timeout: int = 180) -> str:
        if self.imap is None:
            self.connect()
        started = time.time()
        last_notice = 0.0
        while time.time() - started < timeout:
            for folder in ("INBOX", "Junk", "Junk Email"):
                code = self._scan_folder(folder, min_timestamp)
                if code:
                    return code
            if time.time() - last_notice >= 20:
                remain = max(0, int(timeout - (time.time() - started)))
                self.log(f"[{self.account.email}] Still waiting for OpenAI email OTP, about {remain}s left")
                last_notice = time.time()
            time.sleep(5)
        raise TimeoutError("Timed out waiting for OpenAI email OTP")

    def _scan_folder(self, folder: str, min_timestamp: float) -> str:
        assert self.imap is not None
        try:
            if not self._select_folder(folder):
                return ""
            status, data = self.imap.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                return ""
            for msg_id in reversed(data[0].split()[-30:]):
                key = f"{folder}:{msg_id.decode(errors='ignore')}"
                if key in self.seen:
                    continue
                status, msg_data = self.imap.fetch(msg_id, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw = next((item[1] for item in msg_data if isinstance(item, tuple)), None)
                if not raw:
                    continue
                msg = email_pkg.message_from_bytes(raw)
                try:
                    mail_time = parsedate_to_datetime(msg.get("Date")).timestamp() if msg.get("Date") else time.time()
                except Exception:
                    mail_time = time.time()
                if mail_time + 30 < min_timestamp:
                    continue
                subject = decode_header_text(msg.get("Subject"))
                sender = decode_header_text(msg.get("From"))
                body = extract_message_text(msg)
                haystack = f"{subject}\n{sender}\n{body}"
                if not re.search(r"openai|chatgpt", haystack, flags=re.I):
                    continue
                self.seen.add(key)
                code = extract_otp(haystack)
                if code:
                    self.log(f"[{self.account.email}] Received OpenAI OTP {code}")
                    return code
        except Exception:
            return ""
        return ""


def latest_outlook_mail(email: str, client_id: str, refresh_token: str, proxy_url: str = "") -> dict[str, Any]:
    account = MailAccount(email, "", client_id, refresh_token, "")
    access, endpoint = refresh_hotmail_access_token(account, proxy_url)
    reader = HotmailReader(account, lambda _m: None, proxy_url)
    try:
        reader.connect(access)
        msg = reader.latest_message()
        msg["token_endpoint"] = endpoint
        return msg
    finally:
        reader.close()
