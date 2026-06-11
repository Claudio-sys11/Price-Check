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
from typing import Any

import requests


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

        # 로그인 시 발급되는 인증/라우팅 쿠키(ECOUNT_SessionId, SVID)를
        # 후속 데이터 호출에 자동 유지해야 한다. 이를 누락하면 로드밸런서가
        # 세션 미보유 서버로 라우팅하여 HTTP 412(Precondition Failed)가 발생한다.
        self._session = requests.Session()

    # ---- 내부 유틸 -------------------------------------------------------
    def _base_url(self, with_zone: bool) -> str:
        host = f"{self._prefix}{self.zone}" if with_zone else self._prefix
        return f"https://{host}.ecount.com"

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
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
