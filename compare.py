"""
가격/재고 비교 모듈.

Wizfasta(스토어팜 판매상품) 데이터와 EcountERP 재고현황을 **품목코드 기준**으로 결합한다.

- Wizfasta : data/wizfasta_products.json (브라우저에서 추출, 키 = 품목코드 = Mpm_Pr_Cd)
- EcountERP: API 응답(InventoryBalance) 또는 저장된 output/inventory_raw.json

EcountERP 재고 응답의 필드명(품목코드/재고수량/단가)은 계정·엔드포인트마다 다를 수 있어
자동 감지하되, config.json 의 "ecount_fields" 로 정확히 지정할 수도 있다.

    "ecount_fields": {
        "품목코드": "PROD_CD",
        "재고수량": "BAL_QTY",
        "단가": "IN_PRICE"
    }
"""

from __future__ import annotations

import csv
import json
import os
import re
from typing import Any

# EcountERP 필드 자동 감지용 후보 (대소문자 무시, 부분일치)
_CODE_CANDIDATES = ["PROD_CD", "PROD_CODE", "ITEM_CD", "품목코드", "상품코드", "PRODCD"]
_QTY_CANDIDATES = ["BAL_QTY", "QTY", "STOCK", "재고", "수량", "INV_QTY", "현재고"]
_PRICE_CANDIDATES = ["IN_PRICE", "PRICE", "단가", "COST", "UNIT_PRICE", "AMT", "원가"]
# 재고현황(ByLocation 등) 응답에 함께 오는 추가 항목
_NAME_CANDIDATES = ["PROD_DES", "PROD_NAME", "품목명", "상품명", "ITEM_NAME"]
_SIZE_CANDIDATES = ["PROD_SIZE_DES", "SIZE_DES", "규격", "사이즈명"]
_WH_CANDIDATES = ["WH_CD", "창고코드"]
_WHNAME_CANDIDATES = ["WH_DES", "창고명"]
_BARCODE_CANDIDATES = ["BAR_CODE", "BARCODE", "바코드", "EAN", "UPC"]


def parse_product_name(name: str) -> dict[str, str]:
    """EcountERP 품목명을 '_' 기준으로 분해한다.

    형식: 브랜드_모델명_사이즈_입고일자(이후 전부)
      - 첫 번째 '_' 앞      -> 브랜드
      - 첫 번째 '_' 다음    -> 모델명
      - 두 번째 '_' 다음    -> 사이즈
      - 세 번째 '_' 다음    -> 입고일자 (나머지 전체)

    언더스코어가 부족하면 해당 항목은 빈 문자열로 둔다.
    """
    s = "" if name is None else str(name).strip()
    parts = s.split("_", 3)   # 최대 4조각 (입고일자는 나머지 전체)
    return {
        "브랜드": parts[0] if len(parts) > 0 else "",
        "모델명": parts[1] if len(parts) > 1 else "",
        "사이즈": parts[2] if len(parts) > 2 else "",
        "입고일자": parts[3] if len(parts) > 3 else "",
    }


def build_inventory_display(
    inventory_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]] | None = None,
    field_map: dict[str, str | None] | None = None,
    product_field_map: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    """재고현황 행을 표시용으로 변환한다.

    품목명(PROD_DES)을 브랜드/모델명/사이즈/입고일자로 분해(컬럼엔 분해값만, 품목명·규격은 미표시).
    품목 마스터(product_rows)에서 입고단가(IN_PRICE)를 가져와 결합하고,
    총단가 = 입고단가 × 재고수량 을 계산한다.

    출력 컬럼(있는 것만):
      브랜드 | 모델명 | 사이즈 | 입고일자 | 품목코드 | 창고코드 | 창고명 | 재고수량 | 입고단가 | 총단가
    재고수량·금액은 소수점을 제외한 정수로 표현한다.
    """
    if not inventory_rows:
        return []

    inv_fmap = field_map or detect_ecount_fields(inventory_rows)
    code_f, qty_f = inv_fmap["품목코드"], inv_fmap["재고수량"]

    sample = inventory_rows[0]
    name_f = _pick_field(sample, _NAME_CANDIDATES)       # PROD_DES (품목명, 인라인)
    wh_f = _pick_field(sample, _WH_CANDIDATES)           # 창고코드
    whn_f = _pick_field(sample, _WHNAME_CANDIDATES)      # 창고명

    # 품목 마스터: 품목코드 -> (품목명 보완, 입고단가)
    name_map: dict[str, str] = {}
    price_map: dict[str, float] = {}
    if product_rows:
        pfmap = product_field_map or detect_product_fields(product_rows)
        pcode, pname = pfmap["품목코드"], pfmap["품목명"]
        pprice = _pick_field(product_rows[0], _PRICE_CANDIDATES)   # IN_PRICE
        for pr in product_rows:
            c = str(pr.get(pcode, "")).strip() if pcode else ""
            if not c:
                continue
            if pname and c not in name_map:
                name_map[c] = str(pr.get(pname, "") or "")
            if pprice and c not in price_map:
                price_map[c] = _to_number(pr.get(pprice))

    out = []
    for r in inventory_rows:
        code = str(r.get(code_f, "")).strip() if code_f else ""
        name = str(r.get(name_f, "") or "") if name_f else name_map.get(code, "")
        parsed = parse_product_name(name)
        qty = int(_to_number(r.get(qty_f))) if qty_f else 0
        price = int(round(price_map.get(code, 0)))   # 입고단가(정수)
        total = price * qty                          # 총단가
        row: dict[str, Any] = {
            "브랜드": parsed["브랜드"],
            "모델명": parsed["모델명"],
            "사이즈": parsed["사이즈"],
            "입고일자": parsed["입고일자"],
            "품목코드": code,
        }
        if wh_f:
            row["창고코드"] = str(r.get(wh_f, "") or "")
        if whn_f:
            row["창고명"] = str(r.get(whn_f, "") or "")
        row["재고수량"] = qty
        row["입고단가"] = price
        row["총단가"] = total
        out.append(row)
    return out


def add_subtotals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """동일 품목코드 그룹마다 하단에 중간합계(평균 단가) 행을 삽입한다.

    중간합계 행: 재고수량 합계, 총단가 합계, 평균 단가(= 총단가합 / 수량합, 수량합이 0이면 단가 평균).
    내부 표식 키 '_subtotal'=True 로 표시(표시 컬럼에서는 제외).
    """
    if not rows:
        return rows
    cols = [k for k in rows[0].keys() if not k.startswith("_")]

    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for r in rows:
        c = str(r.get("품목코드", ""))
        if c not in groups:
            groups[c] = []
            order.append(c)
        groups[c].append(r)

    out: list[dict[str, Any]] = []
    for c in order:
        g = groups[c]
        out.extend(g)
        sum_qty = sum(_to_number(r.get("재고수량")) for r in g)
        sum_total = sum(_to_number(r.get("총단가")) for r in g)
        prices = [_to_number(r.get("입고단가")) for r in g]
        if sum_qty != 0:
            avg = sum_total / sum_qty
        elif prices:
            avg = sum(prices) / len(prices)
        else:
            avg = 0
        sub = {k: "" for k in cols}
        label_col = "창고명" if "창고명" in sub else "품목코드"
        sub[label_col] = "▸ 소계/평균"
        sub["재고수량"] = int(sum_qty)
        sub["입고단가"] = int(round(avg))    # 평균 단가
        sub["총단가"] = int(round(sum_total))
        sub["_subtotal"] = True
        out.append(sub)
    return out


def _pick_field(sample: dict, candidates: list[str]) -> str | None:
    keys = list(sample.keys())
    low = {k.lower(): k for k in keys}
    # 1) 정확 일치 (대소문자 무시)
    for c in candidates:
        if c.lower() in low:
            return low[c.lower()]
    # 2) 부분 일치
    for c in candidates:
        for k in keys:
            if c.lower() in k.lower():
                return k
    return None


def detect_ecount_fields(rows: list[dict]) -> dict[str, str | None]:
    if not rows:
        return {"품목코드": None, "재고수량": None, "단가": None}
    s = rows[0]
    return {
        "품목코드": _pick_field(s, _CODE_CANDIDATES),
        "재고수량": _pick_field(s, _QTY_CANDIDATES),
        "단가": _pick_field(s, _PRICE_CANDIDATES),
    }


def load_wizfasta(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("rows", data if isinstance(data, list) else [])


def _to_number(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def index_ecount(
    rows: list[dict[str, Any]],
    field_map: dict[str, str | None] | None = None,
) -> tuple[dict[str, dict[str, float]], dict[str, str | None]]:
    """EcountERP 재고 행을 품목코드 기준으로 합산 인덱싱한다.

    같은 품목코드가 여러 창고에 있으면 재고수량은 합산, 단가는 마지막 값을 사용한다.
    """
    fmap = field_map or detect_ecount_fields(rows)
    code_f, qty_f, price_f = fmap["품목코드"], fmap["재고수량"], fmap["단가"]

    index: dict[str, dict[str, float]] = {}
    if not code_f:
        return index, fmap

    for r in rows:
        code = str(r.get(code_f, "")).strip()
        if not code:
            continue
        slot = index.setdefault(code, {"재고수량": 0.0, "단가": 0.0})
        if qty_f:
            slot["재고수량"] += _to_number(r.get(qty_f))
        if price_f:
            slot["단가"] = _to_number(r.get(price_f))
    return index, fmap


def build_comparison(
    wiz_rows: list[dict[str, Any]],
    ecount_index: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Wizfasta 행마다 EcountERP 재고/단가를 붙이고 가격 차이를 계산한다 (1:N 관계)."""
    result = []
    for w in wiz_rows:
        code = str(w.get("품목코드", "")).strip()
        ec = ecount_index.get(code)

        wiz_cost = _to_number(w.get("원가"))
        ec_price = _to_number(ec["단가"]) if ec else None
        ec_qty = _to_number(ec["재고수량"]) if ec else None

        diff = (wiz_cost - ec_price) if ec_price is not None else None

        result.append({
            "품목코드": code,
            "브랜드": w.get("브랜드", ""),
            "모델명": w.get("모델명", ""),
            "판매상품명": w.get("판매상품명", ""),
            "Wiz_원가": wiz_cost,
            "Wiz_기준판매가": _to_number(w.get("기준판매가")),
            "Wiz_판매가": _to_number(w.get("판매가")),
            "Wiz_수수료율": _to_number(w.get("수수료율")),
            "Ecount_재고수량": ec_qty if ec_qty is not None else "",
            "Ecount_단가": ec_price if ec_price is not None else "",
            "원가-단가차이": diff if diff is not None else "",
            "매칭": "O" if ec else "X(EcountERP에 품목코드 없음)",
        })
    return result


# ===== 모델번호 매칭 (EcountERP 품목명/바코드 ↔ Wizfasta 모델번호) =====
# (_NAME_CANDIDATES, _BARCODE_CANDIDATES 는 상단에 정의됨)


def extract_model_tokens(model_name: str) -> list[str]:
    """Wizfasta 모델명에서 매칭에 쓸 모델번호 토큰을 추출한다.

    예) '81222791-HSV'          -> ['81222791']
        'BB50R9B1F1 001-OFF'     -> ['BB50R9B1F1', '001', 'BB50R9B1F1001']
        '블랙 수트 S3HT404C117'  -> ['S3HT404C117']
    꼬리표(-HSV, -OFF 등)와 한글/공백을 제거하고, 길이>=4 인 영숫자 토큰만 사용.
    """
    if not model_name:
        return []
    # 꼬리표 제거
    s = re.sub(r"-(HSV|OFF|ON)\b", " ", str(model_name), flags=re.IGNORECASE)
    raw = re.split(r"[\s\-_/]+", s)
    toks = []
    for t in raw:
        t = re.sub(r"[^A-Za-z0-9]", "", t)
        if len(t) >= 4 and re.search(r"\d", t):  # 숫자 포함, 4자 이상
            toks.append(t.upper())
    # 연속 토큰 결합본도 후보(예: BB50R9B1F1 + 001)
    if len(toks) >= 2:
        toks.append("".join(toks[:2]))
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def detect_product_fields(rows: list[dict]) -> dict[str, str | None]:
    if not rows:
        return {"품목코드": None, "품목명": None, "바코드": None}
    s = rows[0]
    return {
        "품목코드": _pick_field(s, _CODE_CANDIDATES),
        "품목명": _pick_field(s, _NAME_CANDIDATES),
        "바코드": _pick_field(s, _BARCODE_CANDIDATES),
    }


def match_products_by_model(
    wiz_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
    product_field_map: dict[str, str | None] | None = None,
) -> tuple[dict[int, str], dict[str, str | None]]:
    """Wizfasta 각 행을 EcountERP 품목코드(PROD_CD)에 매칭한다.

    모델번호 토큰이 EcountERP 품목명에 포함되거나 바코드와 일치하면 매칭으로 본다.
    반환: ({wiz_row_index: 매칭된 PROD_CD}, 감지된 품목필드맵)
    """
    fmap = product_field_map or detect_product_fields(product_rows)
    code_f, name_f, bar_f = fmap["품목코드"], fmap["품목명"], fmap["바코드"]

    # 품목명/바코드 → 코드 인덱스 구성 (대문자 정규화)
    name_index = []   # (정규화품목명, 코드)
    bar_index = {}    # 바코드 -> 코드
    for pr in product_rows:
        code = str(pr.get(code_f, "")).strip() if code_f else ""
        if not code:
            continue
        if name_f:
            nm = re.sub(r"[^A-Za-z0-9]", "", str(pr.get(name_f, ""))).upper()
            if nm:
                name_index.append((nm, code))
        if bar_f:
            bc = re.sub(r"[^A-Za-z0-9]", "", str(pr.get(bar_f, ""))).upper()
            if bc:
                bar_index[bc] = code

    matched: dict[int, str] = {}
    for i, w in enumerate(wiz_rows):
        tokens = extract_model_tokens(w.get("모델명", ""))
        found = None
        for tk in tokens:
            if tk in bar_index:           # 바코드 정확일치 우선
                found = bar_index[tk]; break
        if not found:
            for tk in tokens:
                for nm, code in name_index:   # 품목명 부분일치
                    if tk in nm:
                        found = code; break
                if found:
                    break
        if found:
            matched[i] = found
    return matched, fmap


def build_comparison_by_model(
    wiz_rows: list[dict[str, Any]],
    matched: dict[int, str],
    ecount_index: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """모델매칭 결과(wiz_idx→PROD_CD)와 재고 인덱스를 결합한다."""
    result = []
    for i, w in enumerate(wiz_rows):
        prod_cd = matched.get(i)
        ec = ecount_index.get(prod_cd) if prod_cd else None
        wiz_cost = _to_number(w.get("원가"))
        ec_price = _to_number(ec["단가"]) if ec else None
        ec_qty = _to_number(ec["재고수량"]) if ec else None
        result.append({
            "품목코드(Wiz)": str(w.get("품목코드", "")),
            "브랜드": w.get("브랜드", ""),
            "모델명": w.get("모델명", ""),
            "판매상품명": w.get("판매상품명", ""),
            "Ecount_품목코드": prod_cd or "",
            "Wiz_원가": wiz_cost,
            "Wiz_기준판매가": _to_number(w.get("기준판매가")),
            "Wiz_판매가": _to_number(w.get("판매가")),
            "Ecount_재고수량": ec_qty if ec_qty is not None else "",
            "Ecount_단가": ec_price if ec_price is not None else "",
            "매칭": "O" if prod_cd else "X(모델번호 매칭 실패)",
        })
    return result


def save_csv(rows: list[dict[str, Any]], path: str) -> None:
    if not rows:
        return
    headers = list(rows[0].keys())
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def extract_ecount_rows(data: dict) -> list[dict]:
    """EcountERP API 응답에서 재고 행 배열을 찾아낸다 (main.py 와 동일 로직)."""
    candidates = [
        lambda d: d.get("Data", {}).get("Result"),
        lambda d: d.get("Data", {}).get("Datas"),
        lambda d: d.get("Data", {}).get("Rows"),
        lambda d: d.get("Data"),
    ]
    for getter in candidates:
        try:
            val = getter(data)
        except AttributeError:
            val = None
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return val
    return []


def compare(
    wizfasta_path: str = "data/wizfasta_products.json",
    ecount_rows: list[dict] | None = None,
    ecount_raw_path: str | None = "output/inventory_raw.json",
    field_map: dict[str, str | None] | None = None,
    out_csv: str = "output/price_compare.csv",
) -> dict[str, Any]:
    """전체 비교 실행. ecount_rows 가 주어지면 그것을, 아니면 저장된 raw json 을 사용."""
    wiz = load_wizfasta(wizfasta_path)

    if ecount_rows is None:
        if ecount_raw_path and os.path.exists(ecount_raw_path):
            with open(ecount_raw_path, encoding="utf-8") as f:
                ecount_rows = extract_ecount_rows(json.load(f))
        else:
            ecount_rows = []

    index, fmap = index_ecount(ecount_rows, field_map)
    rows = build_comparison(wiz, index)
    save_csv(rows, out_csv)

    matched = sum(1 for r in rows if r["매칭"] == "O")
    return {
        "wizfasta_count": len(wiz),
        "ecount_count": len(ecount_rows),
        "ecount_fields": fmap,
        "matched": matched,
        "unmatched": len(rows) - matched,
        "out_csv": out_csv,
        "rows": rows,
    }


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    summary = compare()
    print(f"Wizfasta 상품: {summary['wizfasta_count']}건")
    print(f"EcountERP 재고: {summary['ecount_count']}건")
    print(f"감지된 EcountERP 필드: {summary['ecount_fields']}")
    print(f"매칭 성공: {summary['matched']}건 / 미매칭: {summary['unmatched']}건")
    print(f"결과 저장: {summary['out_csv']}")
    if summary["ecount_count"] == 0:
        print("\n[안내] EcountERP 재고 데이터가 없습니다.")
        print("      먼저 main.py 또는 GUI 앱으로 재고현황을 조회해 output/inventory_raw.json 을 생성하세요.")
