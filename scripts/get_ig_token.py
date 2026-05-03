"""
Instagram Business Login OAuth token script
Run: python scripts/get_ig_token.py
"""
import urllib.parse
import httpx
import sys
import time
import webbrowser
from pathlib import Path

APP_ID     = "970106702122425"
APP_SECRET = "689cbc6a89466367af5b5a70bdc49ee4"
REDIRECT   = "https://runner-thirty-bucket.ngrok-free.dev/callback"
SCOPES     = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_messages"


def main():
    print("[1] Using Flask dashboard at localhost:5001/callback")

    auth_url = (
        "https://api.instagram.com/oauth/authorize"
        f"?client_id={APP_ID}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT)}"
        f"&scope={SCOPES}"
        f"&response_type=code"
    )
    print(f"[2] Opening browser...")
    print(f"    {auth_url}")
    webbrowser.open(auth_url)
    print("[3] Waiting for auth (algo__kr 로그인 후 허용 클릭)...")

    code = None
    for i in range(300):
        try:
            r = httpx.get("http://localhost:5001/callback/code", timeout=3)
            d = r.json()
            if "code" in d:
                code = d["code"]
                break
        except Exception:
            pass
        time.sleep(1)
        if i % 30 == 0 and i > 0:
            print(f"    still waiting... {i}s")

    if not code:
        print("[ERROR] Timeout")
        sys.exit(1)

    print(f"[OK] Code received: {code[:20]}...")

    # Short-lived token
    print("[4] Exchanging code for token...")
    r = httpx.post(
        "https://api.instagram.com/oauth/access_token",
        data={
            "client_id":     APP_ID,
            "client_secret": APP_SECRET,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT,
            "code":          code,
        },
        timeout=15,
    )
    data = r.json()
    print(f"    {data}")
    if "access_token" not in data:
        print("[ERROR] Token exchange failed")
        sys.exit(1)

    short_token = data["access_token"]
    ig_user_id = str(data.get("user_id", ""))
    print(f"[OK] Short token (ig_user_id: {ig_user_id})")

    # Long-lived token (60 days)
    print("[5] Getting long-lived token...")
    r2 = httpx.get(
        "https://graph.instagram.com/access_token",
        params={
            "grant_type":    "ig_exchange_token",
            "client_secret": APP_SECRET,
            "access_token":  short_token,
        },
        timeout=15,
    )
    data2 = r2.json()
    print(f"    {data2}")
    final_token = data2.get("access_token", short_token)
    print(f"[OK] Long-lived token (expires: {data2.get('expires_in', 'unknown')}s)")

    # Update .env
    env_path = Path(__file__).parent.parent / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    new_lines = []
    replaced_token = replaced_uid = False
    for line in lines:
        if line.startswith("IG_ACCESS_TOKEN="):
            new_lines.append(f"IG_ACCESS_TOKEN={final_token}")
            replaced_token = True
        elif line.startswith("IG_USER_ID=") and ig_user_id:
            new_lines.append(f"IG_USER_ID={ig_user_id}")
            replaced_uid = True
        else:
            new_lines.append(line)

    if not replaced_token:
        new_lines.append(f"IG_ACCESS_TOKEN={final_token}")
    if not replaced_uid and ig_user_id:
        new_lines.append(f"IG_USER_ID={ig_user_id}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("[DONE] .env updated!")
    print(f"       IG_USER_ID      = {ig_user_id}")
    print(f"       IG_ACCESS_TOKEN = {final_token[:40]}...")


if __name__ == "__main__":
    main()
