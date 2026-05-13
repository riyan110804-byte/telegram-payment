from __future__ import annotations

import json
import os
import re
import sys

import requests
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    username = os.getenv("SAWERIA_USERNAME", "").strip().strip("\"'").strip("@").strip("/")
    if not username:
        raise SystemExit("SAWERIA_USERNAME belum diisi.")
    response = requests.get(
        f"https://saweria.co/{username}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30,
    )
    if not response.ok:
        raise SystemExit(f"Gagal buka Saweria: HTTP {response.status_code}")
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        response.text,
        re.DOTALL,
    )
    if not match:
        raise SystemExit("Tidak menemukan __NEXT_DATA__ di halaman Saweria.")
    data = json.loads(match.group(1))
    user_id = data.get("props", {}).get("pageProps", {}).get("data", {}).get("id")
    if not user_id:
        raise SystemExit("Tidak menemukan user id Saweria.")
    print(user_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise
