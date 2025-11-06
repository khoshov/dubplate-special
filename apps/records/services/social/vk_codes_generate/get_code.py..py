# get_code.py
# Назначение: сгенерировать PKCE, вывести URL авторизации, сохранить code_verifier в pkce.json

import json
import hashlib
import secrets
import base64
import urllib.parse
import webbrowser
from pathlib import Path

# --- ХАРДКОД ---
CLIENT_ID = "54269764"
REDIRECT_URI = "https://oauth.vk.com/blank.html"
SCOPES = "wall,photos,groups,offline,docs,video,"

AUTH_URL = "https://id.vk.com/authorize"
PKCE_FILE = Path("pkce.json")

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

def main() -> None:
    # PKCE
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())

    # Сохраняем verifier для следующего шага
    PKCE_FILE.write_text(json.dumps({"code_verifier": code_verifier}, ensure_ascii=False, indent=2), encoding="utf-8")

    # Авторизационная ссылка
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\nОткройте ссылку, разрешите доступ и скопируйте параметр 'code' из адресной строки:")
    print(url)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("\nСохранён file: pkce.json (для второго шага).")

if __name__ == "__main__":
    main()
