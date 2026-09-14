"""Keep source URL credentials out of intake records and subprocess logs."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


def require_public_source(value: str) -> None:
    parsed = urlsplit(value.strip())
    if parsed.scheme in {"http", "https"} and (
        parsed.username is not None or parsed.password is not None
    ):
        raise ValueError("参考链接不能包含用户名或密码；请提供不含登录凭据的来源链接")


def source_label(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme in {"http", "https"}:
        # Also sanitize URLs returned by upstream metadata, not only user input.
        authority = parsed.netloc.rsplit("@", 1)[-1]
        query = ""
        # Retain only the public video identity, never tokens/tracking parameters.
        if (parsed.hostname or "").lower() in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"} and parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                query = urlencode({"v": video_id})
        return urlunsplit((parsed.scheme, authority, parsed.path, query, ""))
    return Path(value).expanduser().name


def redact_source_log(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            return source_label(match.group(0))
        except ValueError:
            return "[redacted-source-url]"

    return re.sub(r"https?://[^\s<>\"']+", replace, value, flags=re.IGNORECASE)
