"""Text helpers: URL normalisation and description hashing (the two dedupe keys)."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_|gh_src|source$|src$|ref$|refid$|trk|fbclid$|gclid$|lever-source|lever-origin)", re.I)
_WS = re.compile(r"\s+")
_TAG = re.compile(r"<[^>]+>")


def normalise_url(url: str) -> str:
    """Lower-case scheme/host, drop fragment, tracking params and trailing slash. Keeps ids like gh_jid."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING.match(k)]
    query.sort()
    path = parts.path.rstrip("/") or "/"
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def normalise_ws(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def description_hash(text: str) -> str:
    """SHA-256 of the whitespace-normalised, case-folded description."""
    return hashlib.sha256(normalise_ws(text).casefold().encode("utf-8")).hexdigest()


def html_to_text(raw: str) -> str:
    """Good-enough HTML → text for ATS descriptions (no external parser needed)."""
    s = html.unescape(raw or "")
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h\d|/div)\s*/?>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()
