# get_tokens.py
# Назначение: принять redirect-URL (или code/device_id), обменять на access_token и refresh_token, вывести в терминал.

import json
import requests
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# --- ХАРДКОД ---
CLIENT_ID = "54269764"
CLIENT_SECRET = "DpmErRLNOCdK7vP7OnSJ"
REDIRECT_URI = "https://oauth.vk.com/blank.html"

TOKEN_URL = "https://id.vk.com/oauth2/auth"
PKCE_FILE = Path("pkce.json")


def main() -> int:
    # 1) Просим вставить ПОЛНЫЙ redirect-URL (удобнее: там сразу есть и code, и device_id)
    raw = input(
        "Вставьте ВЕСЬ redirect-URL после разрешения (начинается с https://oauth.vk.com/blank.html?...):\n> "
    ).strip()

    code = ""
    device_id = ""

    if raw.startswith("http"):
        q = parse_qs(urlparse(raw).query)
        code = (q.get("code", [""])[0] or "").strip()
        device_id = (q.get("device_id", [""])[0] or "").strip()
    else:
        # Если вставили только код — спросим device_id отдельно
        code = raw
        device_id = input("Введите значение device_id из того же URL (?device_id=...): ").strip()

    # На всякий случай убираем возможный хвост '=code_v2'
    if code.endswith("=code_v2"):
        code = code[: -len("=code_v2")]

    if not code:
        print("❌ Не найден параметр 'code'. Запустите get_code.py снова и скопируйте полный URL.")
        return 2
    if not device_id:
        print("❌ Не найден параметр 'device_id'. Скопируйте полный URL редиректа и повторите.")
        return 3

    # 2) Читаем code_verifier из pkce.json
    if not PKCE_FILE.exists():
        print("❌ Не найден pkce.json. Сначала выполните: python get_code.py")
        return 4
    code_verifier = json.loads(PKCE_FILE.read_text(encoding="utf-8")).get("code_verifier", "")
    if not code_verifier:
        print("❌ В pkce.json отсутствует code_verifier. Перезапустите get_code.py")
        return 5

    # 3) Обмен кода на токены (важно: device_id обязателен для VK ID)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,  # для вашего приложения секрет обязателен
        "code_verifier": code_verifier,
        "device_id": device_id,
    }

    print("\n🔄 Отправляю запрос к VK ID...")
    resp = requests.post(TOKEN_URL, data=data, timeout=30)
    text = resp.text

    # 4) Разбор ответа
    try:
        payload = resp.json()
    except Exception:
        print("❌ Некорректный ответ VK ID (не JSON):", text[:500])
        return 6

    if resp.status_code >= 400 or "error" in payload:
        print("❌ Ошибка VK ID:", text)
        return 7

    access_token = payload.get("access_token", "")
    refresh_token = payload.get("refresh_token", "")
    expires_in = payload.get("expires_in")
    user_id = payload.get("user_id")

    print("\n=== ✅ РЕЗУЛЬТАТ ===")
    print("ACCESS TOKEN :", access_token)
    print("REFRESH TOKEN:", refresh_token)
    print("DEVICE_ID:", device_id)
    if expires_in is not None:
        print("EXPIRES IN  :", expires_in)
    if user_id is not None:
        print("USER ID     :", user_id)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
