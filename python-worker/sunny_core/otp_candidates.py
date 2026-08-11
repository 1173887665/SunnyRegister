from __future__ import annotations

import base64
import hashlib
import html
import json
import quopri
import re
import unicodedata
from typing import Any
from urllib.parse import unquote


_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_SEPARATED_CODE = re.compile(r"(?<!\d)(\d(?:[\s\u200b\u200c\u200d\u2060\-_.:/]*\d){5})(?!\d)")
_CONTEXT = re.compile(r"openai|chatgpt|verification|security\s*code|one[- ]time|\botp\b|验证码", re.I)
_CODE_KEY = re.compile(r"code|otp|verification|verify|验证码", re.I)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _text_variants(value: str) -> list[str]:
    raw = str(value or "")
    variants = [raw]
    if re.search(r"\\u[0-9a-fA-F]{4}|\\/", raw):
        decoded = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), raw).replace("\\/", "/")
        if decoded and decoded != raw:
            variants.append(decoded)
    for decoded in (unquote(raw), quopri.decodestring(raw.encode("utf-8")).decode("utf-8", errors="replace")):
        if decoded and decoded not in variants:
            variants.append(decoded)
    compact = re.sub(r"\s+", "", raw)
    if len(compact) >= 24 and len(compact) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact):
        try:
            decoded = base64.b64decode(compact, validate=True).decode("utf-8")
            if decoded:
                variants.append(decoded)
        except Exception:
            pass
    return variants


def _collect(value: Any, path: str, output: list[tuple[str, str]]) -> None:
    if isinstance(value, str):
        output.append((path, value))
    elif isinstance(value, int) and 100000 <= value <= 999999 and _CODE_KEY.search(path):
        output.append((path, str(value)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect(item, f"{path}[{index}]", output)
    elif isinstance(value, dict):
        for key, item in value.items():
            _collect(item, f"{path}.{key}", output)


def extract_otp_candidates(raw: str) -> list[dict[str, Any]]:
    value = str(raw or "")
    sources: list[tuple[str, str]] = [("$raw", value)]
    for index, match in enumerate(re.finditer(r"(?is)<script\b[^>]*>(.*?)</script\s*>", value)):
        script = html.unescape(match.group(1) or "")
        if script.strip():
            sources.append((f"$.script[{index}]", script))
    for index, match in enumerate(re.finditer(r"(?is)\b(?:data|value|content|title|aria-label)\s*=\s*(['\"])(.*?)\1", value)):
        attribute = html.unescape(match.group(2) or "")
        if attribute.strip():
            sources.append((f"$.attribute[{index}]", attribute))
    try:
        _collect(json.loads(value), "$", sources)
    except Exception:
        pass
    found: dict[str, dict[str, Any]] = {}
    for source_index, (path, source) in enumerate(sources):
        for variant in _text_variants(source):
            normalized = html.unescape(variant)
            normalized = re.sub(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", normalized)
            normalized = re.sub(r"(?s)<[^>]+>", " ", normalized)
            for match in _CODE.finditer(normalized):
                code = "".join(str(unicodedata.digit(char)) if char.isdigit() else char for char in match.group(1))
                start, end = max(0, match.start() - 120), min(len(normalized), match.end() + 120)
                context = re.sub(r"\s+", " ", normalized[start:end]).strip()
                score = (40 if _CONTEXT.search(context) else 0) + (40 if _CODE_KEY.search(path) else 0)
                score += 30 if normalized.strip() == match.group(1) else 0
                score -= min(source_index, 20) * 0.01
                key = _fingerprint(f"{match.group(1)}|{context}")
                candidate = {"code": code, "key": key, "score": score}
                if key not in found or score > found[key]["score"]:
                    found[key] = candidate
            for match in _SEPARATED_CODE.finditer(normalized):
                if _CODE.fullmatch(match.group(1)):
                    continue
                digits = "".join(str(unicodedata.digit(char)) for char in match.group(1) if char.isdigit())
                if len(digits) != 6:
                    continue
                start, end = max(0, match.start() - 120), min(len(normalized), match.end() + 120)
                context = re.sub(r"\s+", " ", normalized[start:end]).strip()
                score = (40 if _CONTEXT.search(context) else 0) + (40 if _CODE_KEY.search(path) else 0) - 5
                score -= min(source_index, 20) * 0.01
                key = _fingerprint(f"{digits}|{context}")
                candidate = {"code": digits, "key": key, "score": score}
                if key not in found or score > found[key]["score"]:
                    found[key] = candidate
    return sorted(found.values(), key=lambda item: item["score"], reverse=True)
