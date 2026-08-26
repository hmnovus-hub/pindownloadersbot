from __future__ import annotations

import os
from pathlib import Path

from instagram_downloader import InstagramDownloader


def main() -> None:
    target = Path("secrets/instagram_cookies.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    InstagramDownloader._export_comet_cookies(target)
    os.chmod(target, 0o600)
    print(f"Cookies Instagram экспортированы в {target}")
    print("Никому не отправляй этот файл и не добавляй его в Git.")


if __name__ == "__main__":
    main()

