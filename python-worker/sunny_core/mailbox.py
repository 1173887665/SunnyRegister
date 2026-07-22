from __future__ import annotations

import base64
import dataclasses
import email as email_pkg
import imaplib
import os
import re
import socket
import ssl
import time
from datetime import datetime
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
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_MESSAGES_URL = "https://graph.microsoft.com/v1.0/me/messages"


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

GRAPH_TOKEN_ENDPOINTS = [
    {"name": "GRAPH-LIVE", "url": "https://login.live.com/oauth20_token.srf", "scope": GRAPH_SCOPE},
    {"name": "GRAPH-CONSUMERS", "url": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", "scope": GRAPH_SCOPE},
    {"name": "GRAPH-COMMON", "url": "https://login.microsoftonline.com/common/oauth2/v2.0/token", "scope": GRAPH_SCOPE},
]


def _request_outlook_access_token(account: MailAccount, endpoint: dict[str, str], proxies, log: Callable[[str], None] | None = None) -> str:
    data = {
        "client_id": account.client_id,
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
    }
    if endpoint.get("scope"):
        data["scope"] = endpoint["scope"]
    if endpoint.get("resource"):
        data["resource"] = endpoint["resource"]
    if log:
        log(f"[{account.email}] Try Outlook token endpoint {endpoint['name']}")
    resp = requests.post(endpoint["url"], data=data, headers={"Accept": "application/json"}, timeout=20, proxies=proxies)
    payload = resp.json() if resp.text else {}
    if resp.ok and payload.get("access_token"):
        if log:
            log(f"[{account.email}] Outlook token endpoint {endpoint['name']} succeeded")
        return str(payload["access_token"])
    msg = payload.get("error_description") or payload.get("error") or f"HTTP {resp.status_code}"
    raise RuntimeError(str(msg))


def refresh_hotmail_access_token(account: MailAccount, proxy_url: str = "", log: Callable[[str], None] | None = None) -> tuple[str, str]:
    errors: list[str] = []
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    for endpoint in TOKEN_ENDPOINTS:
        try:
            return _request_outlook_access_token(account, endpoint, proxies, log), str(endpoint["name"])
        except Exception as exc:
            errors.append(f"{endpoint['name']}: {exc}")
            if log:
                log(f"[{account.email}] Outlook token endpoint {endpoint['name']} failed: {exc}")
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
        self.graph_access_token = ""
        self.graph_proxies: dict[str, str] | None = None
        self.seen: set[str] = set()

    def connect(self, access_token: str | None = None) -> None:
        self.log(f"[{self.account.email}] Connecting Outlook mailbox for OTP")
        if access_token is not None:
            self._connect_with_access_token_routes(access_token, "provided")
            return
        errors: list[str] = []
        request_routes = [None]
        if self.proxy_url:
            request_routes.append({"http": self.proxy_url, "https": self.proxy_url})
        if self._connect_graph_routes(request_routes, errors):
            return
        for endpoint in TOKEN_ENDPOINTS:
            for request_proxies in request_routes:
                route_name = "proxy" if request_proxies else "direct"
                try:
                    token = _request_outlook_access_token(self.account, endpoint, request_proxies, self.log)
                    self._connect_with_access_token_routes(token, f"{endpoint['name']} token-{route_name}")
                    return
                except Exception as exc:
                    errors.append(f"{endpoint['name']}/{route_name}: {exc}")
                    self.log(f"[{self.account.email}] Outlook IMAP connect via {endpoint['name']}/{route_name} failed: {exc}")
                    self.close()
                    time.sleep(0.5)
        raise RuntimeError("All Outlook Graph/IMAP auth attempts failed -> " + " | ".join(errors))

    def _connect_graph_routes(self, request_routes, errors: list[str]) -> bool:
        for endpoint in GRAPH_TOKEN_ENDPOINTS:
            for request_proxies in request_routes:
                route_name = "proxy" if request_proxies else "direct"
                try:
                    token = _request_outlook_access_token(self.account, endpoint, request_proxies, self.log)
                    self._graph_request(token, request_proxies, limit=1)
                    self.graph_access_token = token
                    self.graph_proxies = request_proxies
                    self.log(f"[{self.account.email}] Outlook Graph connected via {endpoint['name']}/{route_name}")
                    return True
                except Exception as exc:
                    errors.append(f"{endpoint['name']}/{route_name}: {exc}")
                    self.log(f"[{self.account.email}] Outlook Graph connect via {endpoint['name']}/{route_name} failed: {exc}")
        return False

    def _graph_request(self, access_token: str, proxies, limit: int) -> list[dict[str, Any]]:
        params = {
            "$top": str(max(1, min(50, limit))),
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body,isRead",
        }
        response = requests.get(
            GRAPH_MESSAGES_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Prefer": 'outlook.body-content-type="html"',
            },
            timeout=25,
            proxies=proxies,
        )
        if not response.ok:
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message") or payload.get("error")
            except Exception:
                detail = response.text[:300]
            raise RuntimeError(f"Graph HTTP {response.status_code}: {detail}")
        payload = response.json()
        return list(payload.get("value") or [])

    def _graph_messages(self, limit: int) -> list[dict[str, Any]]:
        if not self.graph_access_token:
            return []
        messages = self._graph_request(self.graph_access_token, self.graph_proxies, limit)
        return [self._graph_message_item(message) for message in messages]

    def _graph_message_item(self, message: dict[str, Any]) -> dict[str, Any]:
        sender = message.get("from", {}).get("emailAddress", {}) or {}
        sender_text = str(sender.get("address") or "")
        if sender.get("name"):
            sender_text = f"{sender['name']} <{sender_text}>" if sender_text else str(sender["name"])
        recipients = []
        for recipient in message.get("toRecipients") or []:
            address = recipient.get("emailAddress", {}) or {}
            if address.get("address"):
                recipients.append(str(address["address"]))
        body_info = message.get("body") or {}
        body_raw = str(body_info.get("content") or message.get("bodyPreview") or "")
        body_text = html_to_text(body_raw) if str(body_info.get("contentType") or "").lower() == "html" else body_raw
        subject = str(message.get("subject") or "")
        return {
            "id": str(message.get("id") or ""),
            "email": self.account.email,
            "folder": "Graph",
            "subject": subject,
            "from": sender_text,
            "to": ", ".join(recipients),
            "date": str(message.get("receivedDateTime") or ""),
            "body": body_text,
            "body_preview": str(message.get("bodyPreview") or body_text)[:1200],
            "raw_html": body_raw,
            "otp": extract_otp(subject + "\n" + body_text),
            "source": "graph",
        }

    def _imap_proxy_candidates(self) -> list[str]:
        dedicated_proxy = os.getenv("OUTLOOK_IMAP_PROXY", "").strip()
        fallback_proxy = dedicated_proxy or self.proxy_url
        direct_first = os.getenv("OUTLOOK_IMAP_DIRECT_FIRST", "false").strip().lower() not in {"0", "false", "no", "off"}
        candidates = ["", fallback_proxy] if direct_first else [fallback_proxy, ""]
        return list(dict.fromkeys(candidate for candidate in candidates if candidate or candidate == ""))

    def _connect_with_access_token_routes(self, access_token: str, token_endpoint: str) -> None:
        errors: list[str] = []
        for proxy_url in self._imap_proxy_candidates():
            route_name = "IPv4 direct" if not proxy_url else ("dedicated proxy" if os.getenv("OUTLOOK_IMAP_PROXY", "").strip() else "task proxy")
            try:
                self._connect_with_access_token(access_token, token_endpoint, proxy_url)
                self.log(f"[{self.account.email}] Outlook IMAP route selected: {route_name}")
                return
            except Exception as exc:
                errors.append(f"{route_name}: {exc}")
                self.log(f"[{self.account.email}] Outlook IMAP {route_name} failed: {exc}")
                self.close()
        raise RuntimeError("Outlook IMAP network routes failed -> " + " | ".join(errors))

    def _connect_with_access_token(self, access_token: str, token_endpoint: str, proxy_url: str = "") -> None:
        auth = f"user={self.account.email}\x01auth=Bearer {access_token}\x01\x01"
        if proxy_url:
            self.imap = self._connect_imap_via_proxy(proxy_url)
        else:
            self.imap = self._connect_imap_direct_ipv4()
        self.imap.authenticate("XOAUTH2", lambda _: auth.encode("utf-8"))
        try:
            self.imap.sock.settimeout(30)
        except Exception:
            pass
        self.log(f"[{self.account.email}] Outlook IMAP connected via {token_endpoint}")

    def _connect_imap_direct_ipv4(self) -> imaplib.IMAP4_SSL:
        errors: list[str] = []
        addresses = socket.getaddrinfo(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, socket.AF_INET, socket.SOCK_STREAM)
        for family, socktype, proto, _canonname, address in addresses:
            raw = socket.socket(family, socktype, proto)
            raw.settimeout(20)
            try:
                raw.connect(address)
                tls_sock = ssl.create_default_context().wrap_socket(raw, server_hostname=OUTLOOK_IMAP_HOST)
                tls_sock.settimeout(20)
                return ProxiedIMAP4SSL(OUTLOOK_IMAP_HOST, OUTLOOK_IMAP_PORT, tls_sock, timeout=20)
            except Exception as exc:
                errors.append(f"{address[0]}:{address[1]}: {exc}")
                try:
                    raw.close()
                except Exception:
                    pass
        raise OSError("Outlook IMAP IPv4 connection failed -> " + " | ".join(errors))

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
        self.graph_access_token = ""
        self.graph_proxies = None

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
        if self.graph_access_token:
            items = self._graph_messages(1)
            return items[0] if items else {"email": self.account.email, "empty": True, "source": "graph"}
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
        if self.imap is None and not self.graph_access_token:
            self.connect()
        started = time.time()
        last_notice = 0.0
        while time.time() - started < timeout:
            if self.graph_access_token:
                code = self._scan_graph(min_timestamp)
                if code:
                    return code
            for folder in ("INBOX", "Junk", "Junk Email"):
                if self.imap is None:
                    break
                code = self._scan_folder(folder, min_timestamp)
                if code:
                    return code
            if time.time() - last_notice >= 20:
                remain = max(0, int(timeout - (time.time() - started)))
                self.log(f"[{self.account.email}] Still waiting for OpenAI email OTP, about {remain}s left")
                last_notice = time.time()
            time.sleep(5)
        raise TimeoutError("Timed out waiting for OpenAI email OTP")

    def _scan_graph(self, min_timestamp: float) -> str:
        try:
            for item in self._graph_messages(30):
                key = f"graph:{item.get('id', '')}"
                if key in self.seen:
                    continue
                try:
                    received = datetime.fromisoformat(str(item.get("date") or "").replace("Z", "+00:00")).timestamp()
                except Exception:
                    received = time.time()
                if received + 30 < min_timestamp:
                    continue
                haystack = f"{item.get('subject', '')}\n{item.get('from', '')}\n{item.get('body', '')}"
                if not re.search(r"openai|chatgpt", haystack, flags=re.I):
                    continue
                self.seen.add(key)
                code = extract_otp(haystack)
                if code:
                    self.log(f"[{self.account.email}] Received OpenAI OTP from Graph ({len(code)} digits, redacted)")
                    return code
        except Exception as exc:
            self.log(f"[{self.account.email}] Outlook Graph OTP scan failed: {exc}")
        return ""

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
                    self.log(f"[{self.account.email}] Received OpenAI OTP ({len(code)} digits, redacted)")
                    return code
        except Exception:
            return ""
        return ""


def latest_outlook_mail(email: str, client_id: str, refresh_token: str, proxy_url: str = "") -> dict[str, Any]:
    account = MailAccount(email, "", client_id, refresh_token, "")
    reader = HotmailReader(account, lambda _m: None, proxy_url)
    try:
        reader.connect()
        msg = reader.latest_message()
        msg["mail_protocol"] = "graph" if reader.graph_access_token else "imap"
        return msg
    finally:
        reader.close()
