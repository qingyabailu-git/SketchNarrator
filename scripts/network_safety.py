"""Bounded diagnostics and credential transport rules for optional services."""
from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlsplit


def safe_diagnostic(value: object, limit: int = 500) -> str:
    text = str(value)
    for name, secret in os.environ.items():
        if secret and re.search(r'(?:KEY|TOKEN|SECRET|PASSWORD)$', name, re.I):
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'(?i)\bBearer\s+[^\s"<>]+', 'Bearer [redacted]', text)
    text = re.sub(r'(?i)(https?://)[^\s/@]+:[^\s/@]+@', r'\1[redacted]@', text)
    text = re.sub(r'(https?://[^\s?#]+)[?#][^\s]*', r'\1?[redacted]', text)
    return text[:limit]


def require_credential_transport(url: str, authenticated: bool) -> None:
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == 'localhost'
    if authenticated and parsed.scheme != 'https' and not (parsed.scheme == 'http' and loopback):
        raise ValueError('携带凭据的非回环服务必须使用 HTTPS')
