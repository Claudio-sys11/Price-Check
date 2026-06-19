"""
EcountERP Open API 클라이언트.

호출 흐름:
    1) Zone 조회   : 회사코드(COM_CODE)로 접속 ZONE 확인
    2) 로그인       : ZONE 기반 도메인에 로그인하여 SESSION_ID 발급
    3) 재고현황 조회 : SESSION_ID 로 재고현황 API 호출

운영(production) 도메인은 oapi / oapi{ZONE}, 테스트(상자테스트)는 sboapi / sboapi{ZONE} 를 사용합니다.

주의: 재고현황 엔드포인트의 요청 파라미터(BASE_DATE, PROD_CD, WH_CD 등) 이름은
EcountERP 계정/버전에 따라 다를 수 있습니다. EcountERP 내 [Self-Customizing >
API 인증키 관리 > API 매뉴얼] 에서 정확한 필드명을 확인하고 config.json 의
inventory.payload 를 조정하세요. 이 클라이언트는 응답 원문(raw JSON)을 함께
반환하므로 필드명을 쉽게 맞춰볼 수 있습니다.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from typing import Any

import requests


def _num(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


class EcountApiError(Exception):
    """EcountERP API 호출 실패 시 발생."""


class EcountClient:
    def __init__(
        self,
        com_code: str,
        user_id: str,
        api_cert_key: str,
        lan_type: str = "ko-KR",
        env: str = "production",
        timeout: int = 30,
    ) -> None:
        self.com_code = com_code
        self.user_id = user_id
        self.api_cert_key = api_cert_key
        self.lan_type = lan_type
        self.env = env
        self.timeout = timeout

        # production -> oapi, 그 외(test/sandbox) -> sboapi
        self._prefix = "oapi" if env == "production" else "sboapi"

        self.zone: str | None = None
        self.session_id: str | None = None
        self.call_count = 0          # 이번 인스턴스가 보낸 EcountERP API 호출 수(일일 한도 집계용)

        # 로그인 시 발급되는 인증/라우팅 쿠키(ECOUNT_SessionId, SVID)를
        # 후속 데이터 호출에 자동 유지해야 한다. 이를 누락하면 로드밸런서가
        # 세션 미보유 서버로 라우팅하여 HTTP 412(Precondition Failed)가 발생한다.
        self._session = requests.Session()

    # ---- 내부 유틸 -------------------------------------------------------
    def _base_url(self, with_zone: bool) -> str:
        host = f"{self._prefix}{self.zone}" if with_zone else self._prefix
        return f"https://{host}.ecount.com"

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.call_count += 1
        try:
            resp = self._session.post(url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EcountApiError(f"네트워크 오류: {exc}") from exc

        if resp.status_code != 200:
            raise EcountApiError(
                f"HTTP {resp.status_code} 응답: {resp.text[:500]}"
            )

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise EcountApiError(
                f"JSON 파싱 실패: {resp.text[:500]}"
            ) from exc

    # ---- 1) Zone 조회 ---------------------------------------------------
    def get_zone(self) -> str:
        url = f"{self._base_url(with_zone=False)}/OAPI/V2/Zone"
        data = self._post(url, {"COM_CODE": self.com_code})

        # 응답 구조: {"Data": {"ZONE": "..."}, "Status": "200", ...}
        zone = (data.get("Data") or {}).get("ZONE")
        if not zone:
            raise EcountApiError(f"ZONE 조회 실패. 응답: {data}")

        self.zone = zone
        return zone

    # ---- 2) 로그인 ------------------------------------------------------
    def login(self) -> str:
        if not self.zone:
            self.get_zone()

        url = f"{self._base_url(with_zone=True)}/OAPI/V2/OAPILogin"
        payload = {
            "COM_CODE": self.com_code,
            "USER_ID": self.user_id,
            "API_CERT_KEY": self.api_cert_key,
            "LAN_TYPE": self.lan_type,
            "ZONE": self.zone,
        }
        data = self._post(url, payload)

        # 응답 구조: {"Data": {"Datas": {"SESSION_ID": "..."}, "Code": "00", ...}}
        block = data.get("Data") or {}
        datas = block.get("Datas") or {}
        session_id = datas.get("SESSION_ID")

        if not session_id:
            raise EcountApiError(f"로그인 실패(SESSION_ID 없음). 응답: {data}")

        self.session_id = session_id
        return session_id

    # ---- 3) 재고현황 조회 ----------------------------------------------
    def get_inventory(
        self,
        endpoint: str = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.session_id:
            self.login()

        url = (
            f"{self._base_url(with_zone=True)}{endpoint}"
            f"?SESSION_ID={self.session_id}"
        )
        data = self._post(url, payload or {})

        # 정상 여부 확인 (EcountERP 는 보통 Status / Errors 필드를 함께 반환)
        status = str(data.get("Status", ""))
        if status and status not in ("200", "OK"):
            raise EcountApiError(
                f"재고현황 조회 실패(Status={status}): {self._error_message(data)}"
            )

        return data

    # ---- 4) 품목 마스터 조회 (모델번호 매칭용) ---------------------------
    def get_products(
        self,
        endpoint: str = "/OAPI/V2/InventoryBasic/GetBasicProductsList",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """품목 마스터(품목코드·품목명·규격·바코드)를 조회한다.

        주의: 이 API(GetBasicProductsList)는 인증키에 별도 '사용 API' 권한이
        활성화돼 있어야 한다. 미활성 시 "인증되지 않은 API입니다(EXP00001)" 발생.
        """
        if not self.session_id:
            self.login()

        url = (
            f"{self._base_url(with_zone=True)}{endpoint}"
            f"?SESSION_ID={self.session_id}"
        )
        data = self._post(url, payload or {})

        status = str(data.get("Status", ""))
        if status and status not in ("200", "OK"):
            raise EcountApiError(
                f"품목 조회 실패(Status={status}): {self._error_message(data)}"
            )
        return data

    def _login_fresh(self) -> "tuple[requests.Session, str]":
        """워커용: 독립 세션으로 로그인해 (session, session_id) 반환(공유 상태 미변경)."""
        sess = requests.Session()
        url = f"{self._base_url(with_zone=True)}/OAPI/V2/OAPILogin"
        payload = {
            "COM_CODE": self.com_code, "USER_ID": self.user_id,
            "API_CERT_KEY": self.api_cert_key, "LAN_TYPE": self.lan_type, "ZONE": self.zone,
        }
        self.call_count += 1
        resp = sess.post(url, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise EcountApiError(f"HTTP {resp.status_code}")
        sid = ((resp.json().get("Data") or {}).get("Datas") or {}).get("SESSION_ID")
        if not sid:
            raise EcountApiError("로그인 실패(SESSION_ID 없음)")
        return sess, sid

    # ---- 5) 입고단가 일괄 조회 (단일 세션 1회, 추가 로그인 없음) ----------
    def get_all_prices(self) -> dict[str, float]:
        """현재 세션으로 품목등록(GetBasicProductsList)을 '한 번' 조회해
        {품목코드: 입고단가(IN_PRICE)} 전체를 반환한다(재고현황 조회의 로그인 세션 재사용).

        주의: 빈 조건 조회는 첫 10000건까지만 반환(페이지네이션 없음).
        """
        if not self.session_id:
            self.login()
        url = (
            f"{self._base_url(with_zone=True)}"
            f"/OAPI/V2/InventoryBasic/GetBasicProductsList?SESSION_ID={self.session_id}"
        )
        self.call_count += 1
        try:
            resp = self._session.post(url, json={}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise EcountApiError(f"네트워크 오류: {exc}") from exc
        if resp.status_code != 200:
            raise EcountApiError(f"입고단가 조회 실패: HTTP {resp.status_code}")
        try:
            rows = ((resp.json().get("Data") or {}).get("Result")) or []
        except (json.JSONDecodeError, ValueError) as exc:
            raise EcountApiError(f"입고단가 응답 파싱 실패: {exc}") from exc
        out: dict[str, float] = {}
        for r in rows:
            code = str(r.get("PROD_CD", "")).strip()
            if not code or code in out:
                continue
            # 일괄(목록) 응답에 입고단가 필드가 없으면 확정하지 않고 건너뛴다
            # → 호출측에서 해당 품목만 품목별 조회로 보충(매칭 누락 방지).
            if "IN_PRICE" not in r or r.get("IN_PRICE") in (None, ""):
                continue
            out[code] = _num(r.get("IN_PRICE"))
        return out

    # ---- 6) 품목코드별 입고단가 조회 (품목등록 IN_PRICE, 개별 매칭) -------
    def get_prices(self, codes, progress=None, per_session: int = 2,
                   should_stop=None, workers: int = 4,
                   max_attempts: int = 8) -> dict[str, float]:
        """품목코드 목록 각각에 대해 품목등록(GetBasicProductsList, PROD_CD 지정)의
        입고단가(IN_PRICE)를 조회해 {품목코드: 입고단가} 로 반환한다.

        EcountERP 제약:
          - PROD_CD 지정 조회는 한 세션당 약 2건만 허용되고 이후 HTTP 412(rate limit).
          - 재로그인(새 세션) 하면 제한이 리셋됨.
        속도+정확도 전략:
          - per_session(기본 2)건씩 한 세션(새 로그인)으로 조회. 여러 워커가 병렬 처리.
          - HTTP 200 응답만 '확정'으로 보고, 412/네트워크/로그인 실패한 코드는
            재시도 큐로 되돌려(backoff) max_attempts 회까지 다시 시도 → 누락 최소화.

        progress(done, total) 콜백, should_stop() 가 True면 중단.
        """
        if not self.zone:
            self.get_zone()
        uniq = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
        total = len(uniq)
        result: dict[str, float] = {}
        if total == 0:
            return result

        per_session = max(1, per_session)
        lock = threading.Lock()
        pending = deque((c, 0) for c in uniq)   # (code, attempts)
        resolved: set[str] = set()              # 확정(가격 확인 또는 포기) 코드
        stop = [False]
        login_gate = threading.Lock()
        last_login = [0.0]                       # 로그인 과부하 방지용 최소 간격

        def report() -> None:
            if progress:
                with lock:
                    d = len(resolved)
                progress(d, total)

        def take_batch():
            with lock:
                batch = []
                while pending and len(batch) < per_session:
                    batch.append(pending.popleft())
                return batch

        def requeue(items, give_up=False) -> None:
            with lock:
                for code, att in items:
                    if give_up or att + 1 >= max_attempts:
                        resolved.add(code)        # 더는 재시도 안 함(포기)
                    else:
                        pending.append((code, att + 1))

        def throttled_login():
            # 로그인 폭주를 막아 412/실패를 줄인다(워커 간 최소 0.06초 간격)
            with login_gate:
                gap = 0.06 - (time.monotonic() - last_login[0])
                if gap > 0:
                    time.sleep(gap)
                last_login[0] = time.monotonic()
            return self._login_fresh()

        def worker() -> None:
            while not stop[0]:
                with lock:
                    if len(resolved) >= total:
                        return
                if should_stop and should_stop():
                    stop[0] = True
                    return
                batch = take_batch()
                if not batch:
                    time.sleep(0.05)              # 다른 워커가 재큐할 수 있어 잠시 대기
                    continue
                try:
                    sess, sid = throttled_login()
                except Exception:                 # noqa: BLE001
                    requeue(batch)                # 로그인 실패 → 재시도
                    time.sleep(0.4)
                    report()
                    continue
                url = (
                    f"{self._base_url(with_zone=True)}"
                    f"/OAPI/V2/InventoryBasic/GetBasicProductsList?SESSION_ID={sid}"
                )
                failed = []
                for code, att in batch:
                    if should_stop and should_stop():
                        stop[0] = True
                        break
                    ok = False
                    self.call_count += 1
                    try:
                        resp = sess.post(url, json={"PROD_CD": code}, timeout=30)
                        if resp.status_code == 200:
                            rows = ((resp.json().get("Data") or {}).get("Result")) or []
                            if rows:
                                with lock:
                                    result[code] = _num(rows[0].get("IN_PRICE"))
                            ok = True             # 200 = 확정(가격 또는 가격 없음)
                    except (requests.RequestException, json.JSONDecodeError, ValueError):
                        pass
                    if ok:
                        with lock:
                            resolved.add(code)
                    else:
                        failed.append((code, att))
                if failed:
                    requeue(failed)               # 412/오류 코드만 재시도
                    time.sleep(0.15)              # 레이트리밋 완화
                report()

        n_workers = max(1, min(workers, total))
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(n_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return result

    def diagnose_price(self, code: str) -> dict[str, Any]:
        """단일 품목코드로 품목등록(GetBasicProductsList)을 1회 조회해 진단 정보를 반환한다.

        입고단가 매칭이 0건일 때 '왜 안 되는지'(권한 없음/412/구조 변경 등)를
        실제 응답에서 확인하기 위한 용도.
        """
        info: dict[str, Any] = {
            "code": code, "ok": False, "status": None,
            "message": "", "raw": "", "in_price": None, "row_keys": [],
        }
        try:
            sess, sid = self._login_fresh()
        except Exception as exc:   # noqa: BLE001
            info["message"] = f"로그인 실패: {exc}"
            return info
        url = (
            f"{self._base_url(with_zone=True)}"
            f"/OAPI/V2/InventoryBasic/GetBasicProductsList?SESSION_ID={sid}"
        )
        self.call_count += 1
        try:
            resp = sess.post(url, json={"PROD_CD": code}, timeout=30)
            info["status"] = resp.status_code
            info["raw"] = resp.text[:800]
            if resp.status_code == 200:
                data = resp.json()
                rows = ((data.get("Data") or {}).get("Result")) or []
                msg = self._error_message(data) if not rows else ""
                if rows:
                    info["ok"] = True
                    info["in_price"] = rows[0].get("IN_PRICE")
                    info["row_keys"] = list(rows[0].keys())
                else:
                    info["message"] = msg or "응답에 품목 데이터(Result)가 없습니다."
            else:
                info["message"] = f"HTTP {resp.status_code}"
        except Exception as exc:   # noqa: BLE001
            info["message"] = f"요청 오류: {exc}"
        return info

    @staticmethod
    def _error_message(data: dict[str, Any]) -> str:
        """EcountERP 응답에서 사람이 읽을 에러 메시지를 추출한다."""
        errs = data.get("Errors")
        if isinstance(errs, list) and errs:
            msgs = []
            for e in errs:
                code = e.get("Code", "")
                msg = e.get("Message", "")
                msgs.append(f"[{code}] {msg}" if code else msg)
            return "; ".join(m for m in msgs if m)
        err = data.get("Error")
        if isinstance(err, dict) and err.get("Message"):
            return str(err["Message"])
        return str(data)[:300]

    # ---- 편의 메서드: 전체 흐름 한 번에 ---------------------------------
    def fetch_inventory(
        self,
        endpoint: str = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.get_zone()
        self.login()
        return self.get_inventory(endpoint=endpoint, payload=payload)
