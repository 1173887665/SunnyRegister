"""Install pinned Camoufox runtime assets without GitHub API discovery."""

from __future__ import annotations

import os
import platform
import shutil
import time
from pathlib import Path
from zipfile import ZipFile

import requests
from camoufox.addons import DefaultAddons, maybe_download_addons
from camoufox.locale import ALLOW_GEOIP, MMDB_FILE
from camoufox.pkgman import ARCH_MAP, INSTALL_DIR


CAMOUFOX_VERSION = os.getenv("CAMOUFOX_BROWSER_VERSION", "152.0.4")
CAMOUFOX_RELEASE = os.getenv("CAMOUFOX_BROWSER_RELEASE", "beta.28")
CAMOUFOX_REPOSITORY = "https://github.com/daijro/camoufox/releases/download"
MMDB_URL = "https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-City.mmdb"


def platform_architecture() -> str:
    machine = platform.machine().lower()
    if machine not in ARCH_MAP:
        raise RuntimeError(f"Unsupported Camoufox architecture: {machine}")
    return ARCH_MAP[machine]


def browser_asset_url(architecture: str) -> str:
    filename = f"camoufox-{CAMOUFOX_VERSION}-{CAMOUFOX_RELEASE}-lin.{architecture}.zip"
    return f"{CAMOUFOX_REPOSITORY}/v{CAMOUFOX_VERSION}-{CAMOUFOX_RELEASE}/{filename}"


def download_with_resume(url: str, target: Path, attempts: int = 12) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_size: int | None = None
    for attempt in range(1, attempts + 1):
        offset = target.stat().st_size if target.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
                if response.status_code == 416 and expected_size == offset:
                    return
                response.raise_for_status()
                append = offset > 0 and response.status_code == 206
                if not append:
                    offset = 0
                content_range = response.headers.get("Content-Range", "")
                if "/" in content_range:
                    expected_size = int(content_range.rsplit("/", 1)[1])
                elif response.headers.get("Content-Length"):
                    expected_size = offset + int(response.headers["Content-Length"])
                mode = "ab" if append else "wb"
                with target.open(mode) as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            actual_size = target.stat().st_size
            if expected_size is None or actual_size == expected_size:
                return
            raise IOError(f"incomplete download: {actual_size}/{expected_size} bytes")
        except (OSError, requests.RequestException) as exc:
            if attempt == attempts:
                raise RuntimeError(f"download failed after {attempts} attempts: {url}") from exc
            print(f"Download interrupted ({exc}); resuming attempt {attempt + 1}/{attempts}", flush=True)
            time.sleep(min(attempt * 2, 15))


def install_browser() -> None:
    architecture = platform_architecture()
    archive = Path("/tmp/camoufox.zip")
    download_with_resume(browser_asset_url(architecture), archive)
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as package:
        package.extractall(INSTALL_DIR)
    (INSTALL_DIR / "version.json").write_text(
        f'{{"version":"{CAMOUFOX_VERSION}","release":"{CAMOUFOX_RELEASE}"}}',
        encoding="utf-8",
    )
    for path in INSTALL_DIR.rglob("*"):
        path.chmod(0o755)


def install_geoip_database() -> None:
    if not ALLOW_GEOIP:
        return
    target = Path(MMDB_FILE)
    download_with_resume(MMDB_URL, target)


def main() -> None:
    install_browser()
    install_geoip_database()
    maybe_download_addons(list(DefaultAddons))


if __name__ == "__main__":
    main()
