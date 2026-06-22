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
    # 관리자 — 변경분(admin.json)이 있으면 그것으로, 없으면 임베드 해시로 검증(잠금 없음)
    if username.lower() == ADMIN_USERNAME.lower():
        ov, _sha = _load_admin()
        a_salt = ov.get("salt") or ADMIN_SALT
        a_hash = ov.get("hash") or ADMIN_HASH
        if not verify_password(password, a_salt, a_hash):
            raise AuthError("비밀번호가 올바르지 않습니다.")
        days = _days_since(ov.get("pw_changed_at")) if ov.get("pw_changed_at") else None
        return {"username": ADMIN_USERNAME, "name": ov.get("name") or "임정수",
                "phone": ov.get("phone", ""), "role": "admin",
                "status": "approved", "notice": "",
                "pw_days": days, "pw_expired": bool(days is not None and days >= PW_MAX_DAYS)}
    data, sha = _load_users()
    u = _find(data["users"], username)
    if not u:
        raise AuthError("아이디 또는 비밀번호가 올바르지 않습니다.")
    if u.get("locked"):
        raise AuthError("LOCKED:비밀번호 5회 오류로 계정이 잠겼습니다.\n"
                        "관리자에게 잠금 해제를 요청하세요.")
    if not verify_password(password, u.get("salt", ""), u.get("hash", "")):
        # 실패 횟수 증가 → 5회면 잠금
        fc = int(u.get("failed", 0) or 0) + 1
        locked = fc >= 5

        def _fail(d):
            uu = _find(d["users"], username)
            if uu:
                uu["failed"] = fc
                if locked:
                    uu["locked"] = True
        try:
            _commit_users(_fail, f"fail {username}")
        except Exception:   # noqa: BLE001
            pass
        if locked:
            raise AuthError("LOCKED:비밀번호를 5회 틀려 계정이 잠겼습니다.\n"
                            "관리자에게 잠금 해제를 요청하세요.")
        raise AuthError(f"비밀번호가 올바르지 않습니다. (실패 {fc}/5)")
    st = u.get("status", "pending")
    if st == "pending":
        raise AuthError("관리자 승인 대기 중입니다. 승인 후 로그인할 수 있습니다.")
    if st == "rejected":
        raise AuthError("가입이 거절되었습니다. 관리자에게 문의하세요.")
    # 로그인 성공 → 마지막 접속 기록 + 실패 초기화 + (최초 승인 안내) 1회 쓰기
    need_notice = (st == "approved" and not u.get("approved_notified"))
    notice = ("관리자 승인이 완료되었습니다. 가입을 환영합니다!\n"
              "이제 모든 기능을 사용할 수 있습니다." if need_notice else "")
    now_str = time.strftime("%Y-%m-%d %H:%M")

    def _success(d):
        uu = _find(d["users"], username)
        if uu:
            uu["last_login"] = now_str
            uu["failed"] = 0
            if need_notice:
                uu["approved_notified"] = True
    try:
        _commit_users(_success, f"login {username}")
    except Exception:   # noqa: BLE001
        pass
    days = _days_since(u.get("pw_changed_at") or u.get("created_at"))
    return {"username": u["username"], "name": u.get("name", ""),
            "phone": u.get("phone", ""), "role": u.get("role", "user"),
            "status": st, "notice": notice,
            "pw_days": days, "pw_expired": bool(days is not None and days >= PW_MAX_DAYS),
            "pw_must_change": bool(u.get("must_change"))}


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
        u["must_change"] = False
    _commit_users(modify, f"chgpw {username}")


RESET_PW = "000000"


def reset_password(username: str) -> None:
    """관리자: 사용자 비밀번호를 000000 으로 초기화(+잠금/실패 해제, 다음 로그인 시 변경 유도)."""
    if (username or "").lower() == ADMIN_USERNAME.lower():
        raise AuthError("관리자 계정은 이 기능으로 초기화할 수 없습니다.")
    salt, h = hash_password(RESET_PW)

    def modify(d):
        u = _find(d["users"], username)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        u["salt"], u["hash"] = salt, h
        u["failed"] = 0
        u["locked"] = False
        u["unlock_requested"] = False
        u["must_change"] = True
        u["pw_changed_at"] = _today_str()
    _commit_users(modify, f"reset-pw {username}")


def lock_user(username: str) -> None:
    """관리자: 사용자 계정을 잠금(접속 차단). 관리자 계정은 잠글 수 없다."""
    def modify(d):
        u = _find(d["users"], username)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        if u.get("role") == "admin" or (username or "").lower() == ADMIN_USERNAME.lower():
            raise AuthError("관리자 계정은 잠글 수 없습니다.")
        u["locked"] = True
        u["unlock_requested"] = False
    _commit_users(modify, f"lock {username}")


def unlock_user(username: str) -> None:
    """관리자: 잠긴 사용자 계정 잠금 해제(실패 횟수·요청 초기화)."""
    def modify(d):
        u = _find(d["users"], username)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        u["locked"] = False
        u["failed"] = 0
        u["unlock_requested"] = False
    _commit_users(modify, f"unlock {username}")


def request_unlock(username: str) -> None:
    """잠긴 사용자가 관리자에게 잠금 해제를 요청(unlock_requested 플래그)."""
    def modify(d):
        u = _find(d["users"], username)
        if not u:
            raise AuthError("존재하지 않는 아이디입니다.")
        if not u.get("locked"):
            raise AuthError("잠긴 계정이 아닙니다.")
        u["unlock_requested"] = True
    _commit_users(modify, f"unlock-req {username}")


def update_info(username: str, name: str, phone: str) -> None:
    """이름/휴대폰 정보 변경(관리자는 admin.json, 일반 사용자는 users.json)."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name:
        raise AuthError("이름을 입력하세요.")
    if (username or "").lower() == ADMIN_USERNAME.lower():
        if not backend_enabled():
            raise AuthError("서버에 연결되어야 정보를 변경할 수 있습니다.")
        for _ in range(4):
            ov, sha = _load_admin()
            ov = dict(ov)
            ov["name"] = name
            ov["phone"] = phone
            try:
                _put_file(ADMIN_PATH, ov, sha, "admin info")
                return
            except BackendError as e:
                if "(409)" in str(e):
                    time.sleep(0.5)
                    continue
                raise
        return

    def modify(d):
        u = _find(d["users"], username)
        if not u:
            raise AuthError("사용자를 찾을 수 없습니다.")
        u["name"] = name
        u["phone"] = phone
    _commit_users(modify, f"info {username}")


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


def record_daily(record: dict, retries: int = 4) -> None:
    """일일현황 기록 추가(같은 날의 여러 조회를 모두 보관 — date+time 단위). 충돌 시 재시도."""
    if not backend_enabled():
        raise BackendError("백엔드가 설정되지 않았습니다.")
    last = None
    for _ in range(max(1, retries)):
        try:
            data, sha = _get_file(DAILY_PATH)
            hist = data if isinstance(data, list) else []
            # 같은 (날짜, 시각)만 갱신 — 다른 시각은 누적(낮 동안 모든 조회 표시)
            hist = [h for h in hist
                    if not (h.get("date") == record.get("date")
                            and h.get("time") == record.get("time"))]
            hist.append(record)
            hist.sort(key=lambda h: (h.get("date", ""), h.get("time", "")))
            _put_file(DAILY_PATH, hist, sha, f"daily {record.get('date')}")
            return
        except BackendError as e:
            last = e                  # 409 등 충돌 → sha 다시 받아 재시도
            time.sleep(0.6)
    if last:
        raise last


def delete_daily(keys, retries: int = 4) -> int:
    """일일현황에서 지정한 (날짜, 시각) 레코드를 삭제한다(관리자 전용).

    keys: [(date, time), ...]. 매칭되는 모든 레코드를 한 번에 삭제하고
    삭제된 건수를 반환한다. 충돌(409) 시 재시도.
    """
    if not backend_enabled():
        raise BackendError("백엔드가 설정되지 않았습니다.")
    keyset = {(str(d), str(t)) for d, t in keys}
    if not keyset:
        return 0
    last = None
    for _ in range(max(1, retries)):
        try:
            data, sha = _get_file(DAILY_PATH)
            hist = data if isinstance(data, list) else []
            new_hist = [h for h in hist
                        if (str(h.get("date", "")), str(h.get("time", ""))) not in keyset]
            removed = len(hist) - len(new_hist)
            if removed == 0:
                return 0
            _put_file(DAILY_PATH, new_hist, sha, f"delete daily {removed}")
            return removed
        except BackendError as e:
            last = e                  # 409 등 충돌 → sha 다시 받아 재시도
            time.sleep(0.6)
    if last:
        raise last
    return 0


def finalize_daily(date_str: str, retries: int = 4):
    """해당 날짜를 '00:01 최종' 1건으로 확정(그날 마지막 조회값 채택, 나머지 삭제)."""
    if not backend_enabled():
        return None
    last = None
    for _ in range(max(1, retries)):
        try:
            data, sha = _get_file(DAILY_PATH)
            hist = data if isinstance(data, list) else []
            same = [h for h in hist if h.get("date") == date_str]
            if not same:
                return None
            latest = dict(max(same, key=lambda h: h.get("time", "")))
            latest["time"] = "00:01"
            latest["final"] = True
            latest["by"] = "system"        # 00:01 자동 확정 → 조회자: 시스템
            latest["by_name"] = "시스템"
            hist = [h for h in hist if h.get("date") != date_str]
            hist.append(latest)
            hist.sort(key=lambda h: (h.get("date", ""), h.get("time", "")))
            _put_file(DAILY_PATH, hist, sha, f"finalize {date_str}")
            return latest
        except BackendError as e:
            last = e
            time.sleep(0.6)
    if last:
        raise last


def finalize_old_days(today_str: str, retries: int = 4) -> bool:
    """today 이전 날짜 중 여러 기록이 남은 날을 각각 '00:01 최종' 1건으로 정리.

    (오늘 기록은 24:00까지 모두 보존하고, 자정이 지난 전일까지만 시스템이 1건으로
    확정한다.) 변경이 있을 때만 저장.
    """
    if not backend_enabled():
        return False
    last = None
    for _ in range(max(1, retries)):
        try:
            data, sha = _get_file(DAILY_PATH)
            hist = data if isinstance(data, list) else []
            bydate: dict = {}
            for h in hist:
                bydate.setdefault(h.get("date", ""), []).append(h)
            new_hist = []
            changed = False
            for d, recs in bydate.items():
                if d and d < today_str and len(recs) > 1:
                    latest = dict(max(recs, key=lambda h: h.get("time", "")))
                    latest["time"] = "00:01"
                    latest["final"] = True
                    latest["by"] = "system"        # 자동 확정 → 조회자: 시스템
                    latest["by_name"] = "시스템"
                    new_hist.append(latest)
                    changed = True
                else:
                    new_hist.extend(recs)
            if not changed:
                return False
            new_hist.sort(key=lambda h: (h.get("date", ""), h.get("time", "")))
            _put_file(DAILY_PATH, new_hist, sha, f"finalize-old {today_str}")
            return True
        except BackendError as e:
            last = e
            time.sleep(0.6)
    if last:
        raise last
    return False
