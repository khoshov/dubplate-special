import base64
import hashlib
import hmac


def make_sig(name: str, secret: str, salt: str) -> str:
    """
    HMAC-SHA256(name) с ключом (secret + salt), urlsafe base64 без '='.
    """
    key = (secret + salt).encode("utf-8")
    msg = name.encode("utf-8")
    digest = hmac.new(key=key, msg=msg, digestmod=hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def check_sig(name: str, sig: str, secret: str, salt: str) -> bool:
    exp = make_sig(name, secret, salt)
    return hmac.compare_digest(exp, sig)
