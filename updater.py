"""
자동 업데이트 엔진.

동작:
  1) 실행 시 업데이트 매니페스트(JSON) URL 을 조회
  2) 매니페스트의 version 이 현재 APP_VERSION 보다 높으면 새 버전으로 판단
  3) 설치파일(Setup.exe)을 임시폴더로 내려받아 실행 → (설치파일이 실행 중 앱을
     자동 종료하고 최신 버전으로 교체)

매니페스트 형식(예):
  {
    "version": "1.0.3",
    "url": "https://example.com/downloads/EcountInventory_Setup.exe",
    "notes": "재고 컬럼 추가"
  }

업데이트 URL 은 config.json 의 "update_url" 로 지정한다. 비어 있으면 검사하지 않는다.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any

import requests

from version import APP_VERSION


def parse_version(v: str) -> tuple[int, ...]:
    """'1.0.10' -> (1, 0, 10). 숫자 외 문자는 무시."""
    out = []
    for part in str(v).split("."):
        digits = re.sub(r"\D", "", part)
        out.append(int(digits) if digits else 0)
    return tuple(out)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def check_for_update(update_url: str, timeout: int = 10) -> dict[str, Any] | None:
    """일반 매니페스트 URL 방식. 업데이트가 있으면 dict, 없으면 None."""
    if not update_url:
        return None
    try:
        resp = requests.get(update_url, timeout=timeout)
        resp.raise_for_status()
        manifest = resp.json()
    except (requests.RequestException, ValueError):
        return None

    latest = str(manifest.get("version", "")).strip()
    if latest and is_newer(latest):
        return manifest
    return None


GITHUB_API = "https://api.github.com"


def check_github_release(repo: str, timeout: int = 10) -> dict[str, Any] | None:
    """GitHub Releases 방식.

    repo: "owner/name" (예: "jsoo/EcountInventory")
    최신 릴리스의 tag_name 을 버전으로, .exe 에셋(가능하면 'Setup' 포함)을
    다운로드 URL 로 사용한다. 업데이트가 있으면 {version,url,notes}, 없으면 None.
    """
    repo = (repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        return None
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "EcountInventory-Updater",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        rel = resp.json()
    except (requests.RequestException, ValueError):
        return None

    tag = str(rel.get("tag_name", "")).strip()  # 예: "v1.0.3"
    if not tag or not is_newer(tag):
        return None

    # .exe 에셋 선택 (이름에 'setup' 포함 우선)
    assets = rel.get("assets") or []
    exe_assets = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    setup_assets = [a for a in exe_assets if "setup" in str(a.get("name", "")).lower()]
    chosen = (setup_assets or exe_assets)
    dl = chosen[0].get("browser_download_url", "") if chosen else ""

    return {
        "version": tag,
        "url": dl,
        "notes": str(rel.get("name") or rel.get("body") or "").strip(),
    }


def check(cfg: dict[str, Any] | None, timeout: int = 10) -> dict[str, Any] | None:
    """설정에 따라 GitHub(github_repo) 또는 URL 매니페스트(update_url)로 검사."""
    cfg = cfg or {}
    repo = str(cfg.get("github_repo", "")).strip()
    if repo:
        return check_github_release(repo, timeout)
    return check_for_update(str(cfg.get("update_url", "")).strip(), timeout)


def download_installer(url: str, timeout: int = 120,
                       progress=None) -> str:
    """설치파일을 임시폴더로 내려받고 경로를 반환한다.

    progress(received_bytes, total_bytes) 콜백을 선택적으로 호출.
    """
    dst = os.path.join(tempfile.gettempdir(), "EcountInventory_Setup.exe")
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        received = 0
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)
                    received += len(chunk)
                    if progress:
                        progress(received, total)
    return dst


def launch_installer(path: str) -> None:
    """설치파일을 실행한다(Windows). 설치파일이 실행 중 앱을 종료하고 업데이트한다."""
    os.startfile(path)  # noqa: S606  (Windows 전용)
