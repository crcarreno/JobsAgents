import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

from core.exceptions import ConfigurationError

INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore (all|any|the) (previous|prior|above) (instructions|rules)"),
    re.compile(r"(?i)disregard (the|all|any) (system|previous) (prompt|instructions)"),
    re.compile(r"(?i)(reveal|print|show|leak).{0,30}(system prompt|api key|secret|token|password)"),
    re.compile(r"(?i)(exfiltrate|send).{0,40}(secret|credential|token|api key)"),
    re.compile(r"(?i)you are now (a|an) .{0,30}(admin|root|developer mode)"),
    re.compile(r"(?i)(curl|wget|invoke-webrequest).{0,60}(http|ftp)"),
    re.compile(r"(?i)rm\s+-rf\s+/"),
    re.compile(r"(?i)cat\s+/(etc|proc|root|home)"),
]

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"oc_sk_[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)password\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]

UNTRUSTED_BEGIN = "<<<UNTRUSTED_TASK_CONTENT>>>"
UNTRUSTED_END = "<<<END_UNTRUSTED_TASK_CONTENT>>>"


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="scrypt")


def verify_password(password_hash: str, password: str) -> bool:
    return check_password_hash(password_hash, password)


def _fernet(secret_key: str) -> Fernet:
    if not secret_key:
        raise ConfigurationError("APP_SECRET_KEY is required to encrypt secrets")
    digest = hashlib.sha256(f"jobagents:{secret_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str, secret_key: str) -> str:
    if value is None:
        return ""
    return _fernet(secret_key).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, secret_key: str) -> str:
    if not token:
        return ""
    try:
        return _fernet(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        raise ConfigurationError("Unable to decrypt stored secret; check APP_SECRET_KEY")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def sanitize_untrusted_text(text: str, max_chars: int = 20000) -> str:
    if text is None:
        return ""
    cleaned = text.replace(UNTRUSTED_BEGIN, " ").replace(UNTRUSTED_END, " ")
    cleaned = "".join(ch for ch in cleaned if ch == "\n" or ch == "\t" or ch >= " ")
    return cleaned[:max_chars]


def wrap_untrusted(text: str, max_chars: int = 20000) -> str:
    return f"{UNTRUSTED_BEGIN}\n{sanitize_untrusted_text(text, max_chars)}\n{UNTRUSTED_END}"


def detect_prompt_injection(text: str) -> list[str]:
    if not text:
        return []
    hits = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            hits.append(pattern.pattern)
    return hits


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sign_payload(payload: str, secret_key: str) -> str:
    return hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
