"""시스템 자동 조회(헤드리스) — GitHub Actions(클라우드)에서 매일 00:01(KST) 실행.

관리자 PC를 켜두지 않아도, 클라우드가 EcountERP 재고현황을 조회·매칭해
공유본(inventory_shared.json)을 만든다. 앱(일반 사용자)은 이 파일을 읽어 표시한다.

의존: ecount_api.py, compare.py (같은 폴더). requests 만 있으면 동작(GUI/셀레늄 불필요).
환경변수: ECOUNT_API_KEY(선택, 없으면 기본값), PRICE_LIMIT(선택, 테스트용 상한).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import compare as cmp
from ecount_api import EcountClient

COM_CODE = os.environ.get("ECOUNT_COM_CODE", "188894")
USER_ID = os.environ.get("ECOUNT_USER_ID", "THEFEELKOREA")
API_KEY = os.environ.get("ECOUNT_API_KEY", "2b4a6569451a84b93aa0548bd0ad0ef428")

RICH_EP = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatusByLocation"
BASIC_EP = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus"
OUT_PATH = "inventory_shared.json"


def main() -> int:
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)
    base_date = now.strftime("%Y%m%d")

    client = EcountClient(com_code=COM_CODE, user_id=USER_ID, api_cert_key=API_KEY)
    client.get_zone()
    client.login()   # 접속 1회

    payload = {"BASE_DATE": base_date}
    try:
        data = client.get_inventory(endpoint=RICH_EP, payload=payload)
        rows = cmp.extract_ecount_rows(data)
    except Exception:   # noqa: BLE001  권한 없으면 기본 재고로 폴백
        data = client.get_inventory(endpoint=BASIC_EP, payload=payload)
        rows = cmp.extract_ecount_rows(data)

    fields = cmp.detect_ecount_fields(rows)
    code_f = fields.get("품목코드") or "PROD_CD"
    qty_f = fields.get("재고수량")
    inv_codes = list(dict.fromkeys(
        str(r.get(code_f, "")).strip() for r in rows if str(r.get(code_f, "")).strip()))

    # 재고 1개 이상 품목만 단가 조회 대상
    qty_by_code: dict[str, float] = {}
    if qty_f:
        for r in rows:
            c = str(r.get(code_f, "")).strip()
            if c:
                qty_by_code[c] = qty_by_code.get(c, 0.0) + cmp._to_number(r.get(qty_f))
    stock_codes = ([c for c in inv_codes if qty_by_code.get(c, 0.0) > 0]
                   if qty_f else inv_codes)

    limit = int(os.environ.get("PRICE_LIMIT", "0") or 0)   # 테스트용
    if limit > 0:
        stock_codes = stock_codes[:limit]

    prices = {}
    if stock_codes:
        prices = client.get_prices(stock_codes)   # 품목등록 IN_PRICE 매칭

    display = cmp.build_inventory_display(rows, price_map=prices)
    out = {
        "rows": display,
        "ts": now.strftime("%Y-%m-%d %H:%M"),
        "by": "system",
        "by_name": "시스템",
        "suffix": "(시스템 자동 조회)",
        "count": len(display),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"[system_query] shared rows={len(display)} matched_prices={len(prices)} "
          f"base_date={base_date} ts={out['ts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
