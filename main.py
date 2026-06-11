"""
EcountERP 재고현황 조회 실행 스크립트.

사용법:
    1) config.example.json 을 config.json 으로 복사
    2) config.json 에 COM_CODE / USER_ID / API_CERT_KEY 입력
    3) python main.py

결과:
    - output/inventory_raw.json : API 응답 원문
    - output/inventory.csv      : 표 형태로 변환한 재고현황 (행이 있을 경우)
"""

from __future__ import annotations

import csv
import json
import os
import sys
from typing import Any

# Windows 콘솔에서 한글이 깨지지 않도록 UTF-8 출력으로 전환
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from ecount_api import EcountClient, EcountApiError

CONFIG_PATH = "config.json"
OUTPUT_DIR = "output"


def load_config(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        sys.exit(
            f"[오류] 설정 파일이 없습니다: {path}\n"
            f"      config.example.json 을 {path} 로 복사한 뒤 값을 채우세요."
        )
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)

    required = ["COM_CODE", "USER_ID", "API_CERT_KEY"]
    missing = [
        k for k in required
        if not cfg.get(k) or str(cfg[k]).strip().startswith(("회사", "API", "API 사용자"))
    ]
    if missing:
        sys.exit(
            f"[오류] config.json 에 실제 값을 입력하세요. 미입력 항목: {', '.join(missing)}"
        )
    return cfg


def extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """EcountERP 응답에서 재고 리스트(행 배열)를 최대한 찾아낸다.

    응답 구조가 계정/엔드포인트에 따라 다를 수 있어 일반적인 위치를 순서대로 탐색한다.
    """
    candidates = [
        lambda d: d.get("Data", {}).get("Result"),
        lambda d: d.get("Data", {}).get("Datas"),
        lambda d: d.get("Data", {}).get("Rows"),
        lambda d: d.get("Data"),
    ]
    for getter in candidates:
        val = getter(data)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    return []


def save_csv(rows: list[dict[str, Any]], path: str) -> None:
    # 모든 행의 키를 합집합으로 모아 헤더 구성
    headers: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in headers:
                headers.append(k)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main() -> None:
    cfg = load_config(CONFIG_PATH)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    client = EcountClient(
        com_code=cfg["COM_CODE"],
        user_id=cfg["USER_ID"],
        api_cert_key=cfg["API_CERT_KEY"],
        lan_type=cfg.get("LAN_TYPE", "ko-KR"),
        env=cfg.get("ENV", "production"),
    )

    inv_cfg = cfg.get("inventory", {})
    endpoint = inv_cfg.get(
        "endpoint",
        "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus",
    )
    payload = {k: v for k, v in inv_cfg.get("payload", {}).items() if v}
    # GetListInventoryBalanceStatus 는 BASE_DATE 가 필수 → 비어 있으면 오늘 날짜 사용
    if not payload.get("BASE_DATE"):
        from datetime import date
        payload["BASE_DATE"] = date.today().strftime("%Y%m%d")

    print(f"[1/3] Zone 조회 중... (회사코드: {cfg['COM_CODE']})")
    try:
        zone = client.get_zone()
        print(f"      ZONE = {zone}")

        print("[2/3] 로그인 중...")
        client.login()
        print("      로그인 성공, SESSION_ID 발급됨")

        print(f"[3/3] 재고현황 조회 중... (endpoint: {endpoint})")
        data = client.get_inventory(endpoint=endpoint, payload=payload)
    except EcountApiError as exc:
        sys.exit(f"[실패] {exc}")

    # 원문 저장
    raw_path = os.path.join(OUTPUT_DIR, "inventory_raw.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"      응답 원문 저장: {raw_path}")

    # 표 변환 시도
    rows = extract_rows(data)
    if rows:
        csv_path = os.path.join(OUTPUT_DIR, "inventory.csv")
        save_csv(rows, csv_path)
        print(f"      재고현황 {len(rows)}건 CSV 저장: {csv_path}")
    else:
        print(
            "      [안내] 응답에서 재고 행 배열을 자동으로 찾지 못했습니다.\n"
            "             inventory_raw.json 을 열어 실제 데이터 위치를 확인한 뒤\n"
            "             main.py 의 extract_rows() 또는 config.json payload 를 조정하세요."
        )

    print("\n완료.")


if __name__ == "__main__":
    main()
