"""공유 데이터 백엔드 — 비공개 GitHub 저장소에 users.json / daily_status.json 보관.

- 토큰/관리자 해시는 _secret.py(로컬 전용, exe 내장)에서 읽는다.
- 비밀번호는 PBKDF2-SHA256(200k, salt)로 해시 저장(평문 미보관).
- 회원가입은 status="pending"로 등록되고, 관리자가 승인해야 로그인 가능.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

import requests

try:                                  # 로컬 전용 비밀값 (없으면 백엔드 비활성)
    from _secret import (DATA_REPO, DATA_TOKEN,
                         ADMIN_USERNAME, ADMIN_SALT, ADMIN_HASH)
except Exception:                     # pragma: no cover
    DATA_REPO = DATA_TOKEN = ""
    ADMIN_USERNAME = "THEFEELKOREA"
    ADMIN_SALT = ADMIN_HASH = ""

API = "https://api.github.com"
USERS_PATH = "users.json"
DAILY_PATH = "daily_status.json"
ADMIN_PATH = "admin.json"          # 관리자 비밀번호 변경분(임베드 해시 대체)
PBKDF2_ITERS = 200_000
PW_MAX_DAYS = 30                   # 비밀번호 변경 주기(일)


def _today_str() -> str:
    return time.strftime("%Y-%m-%d")


def _days_since(date_str) -> "int | None":
    try:
        y, m, d = (int(x) for x in str(date_str)[:10].split("-"))
        from datetime import date
        return (date.today() - date(y, m, d)).days
    except Exception:   # noqa: BLE001
        return None


class BackendError(Exception):
    """네트워크/저장소 오류."""


class AuthError(Exception):
    """인증/가입 관련 사용자 메시지 오류."""


def backend_enabled() -> bool:
    return bool(DATA_REPO and DATA_TOKEN)


# ---------------- 비밀번호 해시 ----------------
def hash_password(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                             bytes.fromhex(salt), PBKDF2_ITERS)
    return salt, dk.hex()


def verify_password(pw: str, salt: str, hexhash: str) -> bool:
    if not salt or not hexhash:
        return False
    try:
        _, h = hash_password(pw, salt)
    except ValueError:
        return False
    return secrets.compare_digest(h, hexhash)


# ---------------- GitHub Contents API ----------------
def _headers() -> dict:
    return {
        "Authorization": f"Bearer {DATA_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_file(path: str):
    """(data, sha) 반환. 파일이 없으면 (None, None)."""
    if not backend_enabled():
        raise BackendError("백엔드가 설정되지 않았습니다(토큰 없음).")
    try:
        r = requests.get(f"{API}/repos/{DATA_REPO}/contents/{path}",
                         headers=_headers(), timeout=20)
    except requests.RequestException as e:
        raise BackendError(f"네트워크 오류: {e}")
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        raise BackendError(f"조회 실패({r.status_code}): {r.text[:200]}")
    j = r.json()
    try:
        raw = base64.b64decode(j.get("content", "")).decode("utf-8")
        data = json.loads(raw) if raw.strip() else None
    except (ValueError, json.JSONDecodeError):
        data = None
    return data, j.get("sha")


def _put_file(path: str, data, sha, message: str) -> str:
    body = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode()
    payload = {"message": message, "content": body}
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(f"{API}/repos/{DATA_REPO}/contents/{path}",
                         headers=_headers(), json=payload, timeout=20)
    except requests.RequestException as e:
        raise BackendError(f"네트워크 오류: {e}")
    if r.status_code not in (200, 201):
        raise BackendError(f"저장 실패({r.status_code}): {r.text[:200]}")
    return r.json()["content"]["sha"]


# ---------------- 사용자 저장소 ----------------
def _load_users():
    data, sha = _get_file(USERS_PATH)
    if not isinstance(data, dict):
        data = {"users": []}
    data.setdefault("users", [])
    return data, sha


def _find(users: list, username: str):
    uname = (username or "").strip().lower()
    for u in users:
        if str(u.get("username", "")).strip().lower() == uname:
            return u
    return None


def authenticate(username: str, password: str) -> dict:
    """성공 시 {username, role, status} 반환. 실패 시 AuthError."""
    username = (username or "").strip()
    if not username or not password:
        raise AuthError("아이디와 비밀번호를 입력하세요.")
    # 관리자 — 변경분(admin.json)이 있으면 그것으로, 없으면 임베드 해시로 검증
    if username.lower() == ADMIN_USERNAME.lower():
        ov, _sha = _load_admin()
        a_salt = ov.get("salt") or ADMIN_SALT
        a_hash = ov.get("hash") or ADMIN_HASH
        if not verify_password(password, a_salt, a_hash):
            raise AuthError("비밀번호가 올바르지 않습니다.")
        days = _days_since(ov.get("pw_changed_at")) if ov.get("pw_changed_at") else None
        return {"username": ADMIN_USERNAME, "name": "임정수", "role": "admin",
                "status": "approved", "notice": "",
                "pw_days": days, "pw_expired": bool(days is not None and days >= PW_MAX_DAYS)}
    data, sha = _load_users()
    u = _find(data["users"], username)
    if not u or not verify_password(password, u.get("salt", ""), u.get("hash", "")):
        raise AuthError("아이디 또는 비밀번호가 올바르지 않습니다.")
    st = u.get("status", "pending")
    if st == "pending":
        raise AuthError("관리자 승인 대기 중입니다. 승인 후 로그인할 수 있습니다.")
    if st == "rejected":
        raise AuthError("가입이 거절되었습니다. 관리자에게 문의하세요.")
    # 승인 후 첫 로그인 시 1회 안내 메시지(approved_notified 플래그)
    notice = ""
    if st == "approved" and not u.get("approved_notified"):
        u["approved_notified"] = True
        try:
            _put_file(USERS_PATH, data, sha, f"notify {username}")
        except BackendError:
            pass
        notice = ("관리자 승인이 완료되었습니다. 가입을 환영합니다!\n"
                  "이제 모든 기능을 사용할 수 있습니다.")
    days = _days_since(u.get("pw_changed_at") or u.get("created_at"))
    return {"username": u["username"], "name": u.get("name", ""),
            "role": u.get("role", "user"), "status": st, "notice": notice,
            "pw_days": days, "pw_expired": bool(days is not None and days >= PW_MAX_DAYS)}


def _load_admin():
    """admin.json (관리자 비밀번호 변경분) → (dict, sha). 없으면 ({}, None)."""
    if not backend_enabled():
        return {}, None
    try:
        data, sha = _get_file(ADMIN_PATH)
    except BackendError:
        return {}, None
    return (data if isinstance(data, dict) else {}), sha


def register(username: str, password: str, name: str = "", phone: str = "") -> None:
    """회원가입 — status='pending'으로 등록(관리자 승인 필요)."""
    username = (username or "").strip()
    name = (name or "").strip()
    phone = (phone or "").strip()
    if len(username) < 3:
        raise AuthError("아이디는 3자 이상이어야 합니다.")
    if not name:
        raise AuthError("사용자 이름을 입력하세요.")
    if not phone:
        raise AuthError("휴대폰 번호를 입력하세요.")
    if len(password) < 4:
        raise AuthError("비밀번호는 4자 이상이어야 합니다.")
    if username.lower() == ADMIN_USERNAME.lower():
        raise AuthError("사용할 수 없는 아이디입니다.")
    salt, h = hash_password(password)

    def modify(data):
        if _find(data["users"], username):
            raise AuthError("이미 존재하는 아이디입니다.")
        data["users"].append({
            "username": username, "name": name, "phone": phone, "salt": salt, "hash": h,
            "role": "user", "status": "pending", "approved_notified": False,
            "created_at": time.strftime("%Y-%m-%d %H:%M"), "pw_changed_at": _today_str(),
        })
    _commit_users(modify, f"register {username}")


def _commit_users(modify, message, retries: int = 4) -> None:
    """users.json 을 안전하게 갱신. modify(data) 가 검증/변경 수행. 409(sha 충돌)는 재시도."""
    last = None
    for _ in range(max(1, retries)):
        data, sha = _load_users()
        modify(data)                      # 검증 예외(AuthError)는 그대로 전파
        try:
            _put_file(USERS_PATH, data, sha, message)
            return
        except BackendError as e:
            if "(409)" in str(e):         # 동시/연속 쓰기 sha 충돌 → 재시도
                last = e
                time.sleep(0.5)
                continue
            raise
    if last:
        raise last


# ---------------- 관리자: 사용자 관리 ----------------
def list_users() -> list:
    """등록 사용자 목록(가입 대기 포함). 관리자(고정)는 목록에 없음."""
    data, _ = _load_users()
    return list(data["users"])


def set_user_status(username: str, status: str) -> None:
    """status: approved / rejected / pending."""
    if status not in ("approved", "rejected", "pending"):
        raise AuthError("잘못된 상태값입니다.")

    def modify(data):
        u = _find(data["users"], username)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        u["status"] = status
        if status == "approved":
            u["approved_notified"] = False   # 승인 시 다음 로그인에서 안내
    _commit_users(modify, f"{status} {username}")


def rename_user(old: str, new: str) -> None:
    """관리자: 사용자 아이디(ID) 변경."""
    new = (new or "").strip()
    if len(new) < 3:
        raise AuthError("아이디는 3자 이상이어야 합니다.")
    if new.lower() == ADMIN_USERNAME.lower():
        raise AuthError("사용할 수 없는 아이디입니다.")

    def modify(data):
        u = _find(data["users"], old)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        if (old or "").strip().lower() != new.lower() and _find(data["users"], new):
            raise AuthError("이미 존재하는 아이디입니다.")
        u["username"] = new
    _commit_users(modify, f"rename {old} -> {new}")


def change_password(username: str, old_pw: str, new_pw: str) -> None:
    """본인 비밀번호 변경(현재 비밀번호 확인 후). 관리자도 변경 가능(admin.json 저장)."""
    if len(new_pw or "") < 4:
        raise AuthError("새 비밀번호는 4자 이상이어야 합니다.")
    salt, h = hash_password(new_pw)

    # 관리자: admin.json 에 새 해시 저장(임베드 해시 대체)
    if (username or "").lower() == ADMIN_USERNAME.lower():
        if not backend_enabled():
            raise AuthError("서버에 연결되어야 관리자 비밀번호를 변경할 수 있습니다.")
        last = None
        for _ in range(4):
            ov, sha = _load_admin()
            cur_salt = ov.get("salt") or ADMIN_SALT
            cur_hash = ov.get("hash") or ADMIN_HASH
            if not verify_password(old_pw, cur_salt, cur_hash):
                raise AuthError("현재 비밀번호가 올바르지 않습니다.")
            try:
                _put_file(ADMIN_PATH,
                          {"salt": salt, "hash": h, "pw_changed_at": _today_str()},
                          sha, "admin chgpw")
                return
            except BackendError as e:
                if "(409)" in str(e):
                    last = e
                    time.sleep(0.5)
                    continue
                raise
        if last:
            raise last
        return

    def modify(data):
        u = _find(data["users"], username)
        if not u or not verify_password(old_pw, u.get("salt", ""), u.get("hash", "")):
            raise AuthError("현재 비밀번호가 올바르지 않습니다.")
        u["salt"], u["hash"] = salt, h
        u["pw_changed_at"] = _today_str()
    _commit_users(modify, f"chgpw {username}")


def delete_user(username: str) -> None:
    def modify(data):
        before = len(data["users"])
        data["users"][:] = [u for u in data["users"]
                            if str(u.get("username", "")).strip().lower()
                            != (username or "").strip().lower()]
        if len(data["users"]) == before:
            raise AuthError("사용자를 찾을 수 없습니다.")
    _commit_users(modify, f"delete {username}")


# ---------------- 공유 일일현황 ----------------
def load_daily() -> list:
    """공유 저장소의 일일현황 이력(list). 백엔드 미설정/오류 시 빈 목록."""
    if not backend_enabled():
        return []
    try:
        data, _ = _get_file(DAILY_PATH)
    except BackendError:
        return []
    return data if isinstance(data, list) else []


def record_daily(record: dict, retries: int = 3) -> None:
    """오늘 날짜 기록을 공유 저장소에 저장(같은 날짜는 최신으로 갱신). 충돌 시 재시도."""
    if not backend_enabled():
        raise BackendError("백엔드가 설정되지 않았습니다.")
    last = None
    for _ in range(max(1, retries)):
        try:
            data, sha = _get_file(DAILY_PATH)
            hist = data if isinstance(data, list) else []
            hist = [h for h in hist if h.get("date") != record.get("date")]
            hist.append(record)
            hist.sort(key=lambda h: (h.get("date", ""), h.get("time", "")))
            _put_file(DAILY_PATH, hist, sha, f"daily {record.get('date')}")
            return
        except BackendError as e:
            last = e                  # 409 등 충돌 → sha 다시 받아 재시도
            time.sleep(0.6)
    if last:
        raise last
