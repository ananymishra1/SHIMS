from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from html.parser import HTMLParser
from typing import Optional

from .config import settings

PBKDF2_ROUNDS = 210_000


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ROUNDS)
    return f'pbkdf2_sha256${PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_s, salt_b64, hash_b64 = encoded.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(rounds_s))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def sign_value(value: str, max_age_seconds: int = 60 * 60 * 12) -> str:
    ts = str(int(time.time()))
    body = f'{value}.{ts}'
    sig = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f'{body}.{sig}'


def unsign_value(signed: str, max_age_seconds: int = 60 * 60 * 12) -> Optional[str]:
    try:
        value, ts, sig = signed.rsplit('.', 2)
        body = f'{value}.{ts}'
        expected = hmac.new(settings.secret_key.encode(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(time.time()) - int(ts) > max_age_seconds:
            return None
        return value
    except Exception:
        return None


def constant_time_token_ok(token: str) -> bool:
    return hmac.compare_digest(token or '', settings.bridge_token)


def new_id(prefix: str = 'shims') -> str:
    return f'{prefix}_{secrets.token_urlsafe(12)}'


# -----------------------------------------------------------------------------
# HTML sanitizer for rich-text editor content
# -----------------------------------------------------------------------------
_ALLOWED_TAGS = {
    'p', 'br', 'hr', 'div', 'span', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'b', 'i', 'u', 'strong', 'em', 'sub', 'sup', 'strike', 's',
    'ul', 'ol', 'li', 'blockquote', 'pre', 'code',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    'a', 'img', 'font',
}
_ALLOWED_ATTRS = {'href', 'src', 'alt', 'title', 'class', 'style', 'colspan', 'rowspan', 'target'}
_SAFE_URL_RE = re.compile(r'^(https?|mailto|tel|#|/)[^\s]*$', re.I)


class _HTMLSanitizer(HTMLParser):
    """Fast standard-library HTML sanitizer. Strips tags/attributes/JS events."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        if tag not in _ALLOWED_TAGS:
            self._skip_depth = 1
            return
        attr_parts = []
        for name, value in attrs:
            name = name.lower()
            if name not in _ALLOWED_ATTRS:
                continue
            if name.startswith('on'):
                continue
            if value is None:
                attr_parts.append(name)
                continue
            # Only allow safe URLs in href/src.
            if name in {'href', 'src'}:
                if not _SAFE_URL_RE.match(value.strip()):
                    continue
            # Escape quotes to avoid attribute injection.
            safe_value = value.replace('&', '&amp;').replace('"', '&quot;')
            attr_parts.append(f'{name}="{safe_value}"')
        self._out.append(f'<{tag}' + (' ' + ' '.join(attr_parts) if attr_parts else '') + '>')

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in _ALLOWED_TAGS:
            self._out.append(f'</{tag}>')

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._out.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f'&{name};')

    def handle_charref(self, name: str) -> None:
        if self._skip_depth:
            return
        self._out.append(f'&#{name};')


def sanitize_html(html: str | None) -> str:
    """Return sanitized HTML safe to render inline.

    Removes scripts, event handlers, disallowed tags/attributes, and unsafe URLs.
    """
    if not html:
        return ''
    parser = _HTMLSanitizer()
    try:
        parser.feed(html)
    except Exception:
        # On malformed input, fall back to escaping everything.
        return html.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return ''.join(parser._out)


def strip_html_for_pdf(html: str | None) -> str:
    """Convert rich HTML into plain text suitable for ReportLab PDF output.

    Preserves paragraph/line breaks from <p>, <br>, <li> etc. and removes all
    other tags so unsupported markup does not crash the PDF renderer.
    """
    if not html:
        return ''
    # Normalize tags to lower case for easier replacement.
    text = str(html)
    # Convert common block/line tags to newlines before stripping remaining tags.
    text = re.sub(r'</(p|div|h[1-6]|li|tr|pre)>', '\n', text, flags=re.I)
    text = re.sub(r'<(br|br/|br\s*/?)\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<li\b[^>]*>', '\n- ', text, flags=re.I)
    text = re.sub(r'</?ol\b[^>]*>', '\n', text, flags=re.I)
    text = re.sub(r'</?ul\b[^>]*>', '\n', text, flags=re.I)
    # Strip all remaining tags.
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common entities.
    text = text.replace('&nbsp;', ' ').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&').replace('&quot;', '"')
    # Collapse excessive blank lines.
    lines = [line.strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line).strip()
