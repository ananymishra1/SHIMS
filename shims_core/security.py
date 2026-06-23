from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict
from fastapi import HTTPException, Request
from .settings import settings


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    iterations = 200000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f'pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        alg, iters, salt_b64, digest_b64 = stored.split('$', 3)
        if alg != 'pbkdf2_sha256':
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, int(iters))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))


def create_token(payload: Dict[str, Any], ttl: int = 86400) -> str:
    body = dict(payload)
    body['exp'] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(',', ':'), sort_keys=True).encode()
    sig = hmac.new(settings.secret_key.encode(), raw, hashlib.sha256).digest()
    return _b64(raw) + '.' + _b64(sig)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        body_b64, sig_b64 = token.split('.', 1)
        raw = _unb64(body_b64)
        sig = _unb64(sig_b64)
        expected = hmac.new(settings.secret_key.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError('bad signature')
        body = json.loads(raw.decode())
        if int(body.get('exp', 0)) < int(time.time()):
            raise ValueError('expired')
        return body
    except Exception as exc:
        raise HTTPException(status_code=401, detail='Invalid session') from exc


def require_bridge(request: Request) -> None:
    token = request.headers.get('x-shims-bridge-token', '')
    if not token or token != settings.bridge_token:
        raise HTTPException(status_code=403, detail='Invalid bridge token')
