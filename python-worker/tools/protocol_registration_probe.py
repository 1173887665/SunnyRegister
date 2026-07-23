from __future__ import annotations

import json
import os
import re
import sys

from sunny_core.mailbox import MailAccount
from sunny_core.protocol_auth import ProtocolRegistrationFlow


def main() -> int:
    raw = os.environ.pop("SUNNY_PROTOCOL_CREDENTIAL", "").strip().replace("\\*", "*")
    proxy_url = os.environ.pop("SUNNY_PROTOCOL_PROXY", "").strip()
    parts = raw.split("----", 3)
    if len(parts) != 4 or not all(parts):
        print(json.dumps({"ok": False, "error": "invalid credential format"}))
        return 2
    email, password, client_id, refresh_token = parts
    account = MailAccount(email, password, client_id, refresh_token, raw)
    logs: list[str] = []

    def log(message: str) -> None:
        sanitized = str(message).replace(password, "[redacted]").replace(refresh_token, "[redacted]")
        sanitized = re.sub(r"\b\d{6}\b", "[otp-redacted]", sanitized)
        logs.append(sanitized)
        print(sanitized, flush=True)

    flow = ProtocolRegistrationFlow(account, proxy_url, log)
    try:
        result = flow.run()
    except Exception as exc:
        message = str(exc).replace(password, "[redacted]").replace(refresh_token, "[redacted]")
        message = re.sub(r"\b\d{6}\b", "[otp-redacted]", message)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": message,
                    "traffic": getattr(exc, "traffic", flow.traffic.snapshot()),
                    "log_count": len(logs),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "auth_action": result.get("auth_action"),
                "plan_type": result.get("plan_type"),
                "has_access_token": bool(result.get("access_token")),
                "has_session": bool(result.get("session_json")),
                "traffic": result.get("protocol_traffic"),
                "log_count": len(logs),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
