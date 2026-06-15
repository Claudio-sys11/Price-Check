"""
Wizfasta 상품DB에서 모델명·원가를 가져오는 Selenium 스크레이퍼.

방식: 상품DB(일반상품·재고수량≥1·500개씩) 조회 → '전체 엑셀 다운로드'(btnExcelFullDown)
      로 .xlsx 를 받아 파싱한다. (엑셀이 안 받아지면 그리드 데이터로 폴백)

- 앱 전용 Chrome 프로필(%APPDATA%\\EcountInventory\\wizfasta_chrome)로 실행 →
  Wizfasta에 한 번만 로그인하면 이후 세션 유지.
- 진행 상황은 progress(msg) 콜백으로 실시간 보고.

Selenium 4.x 의 Selenium Manager가 chromedriver를 자동으로 받아 사용한다.
"""

from __future__ import annotations

import glob
import os
import time

PRDDB_URL = "https://www.wizfasta.com/ProductMng/PrdDbList.aspx"


def _appdir(*parts) -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "EcountInventory", *parts)
    os.makedirs(path, exist_ok=True)
    return path


# 필터(일반상품·재고≥1·500개씩) 설정 + 조회
_SETUP_JS = r"""
var setSel=function(name,val){var s=Array.prototype.find.call(document.querySelectorAll('select'),
  function(x){return (x.name||x.id)===name;}); if(s){s.value=val; s.dispatchEvent(new Event('change',{bubbles:true}));}};
var setInp=function(name,val){var i=Array.prototype.find.call(document.querySelectorAll('input'),
  function(x){return (x.name||x.id)===name;}); if(i){i.value=val; i.dispatchEvent(new Event('change',{bubbles:true}));}};
setSel('selPrDivCd','PrDivCd_Gen'); setInp('txtStockNum','1'); setSel('pageSize','500');
var btn=Array.prototype.find.call(document.querySelectorAll('a'),function(a){return (a.innerText||'').trim()==='조회';});
if(btn){btn.click(); return 'ok';} return 'no-button';
"""

_GRID_LEN_JS = "return (window.whus_data&&window.whus_data.grid)?window.whus_data.grid.length:0;"

# 전체 엑셀 다운로드 버튼 클릭(비동기 발사 → execute_script 블로킹 방지)
_EXCEL_CLICK_JS = r"""
var b=document.getElementById('btnExcelFullDown');
if(!b) return 'no-button';
setTimeout(function(){ b.click(); }, 0);
return 'clicked';
"""

# 폴백: 그리드에서 직접 추출
_GRID_EXTRACT_JS = r"""
var g=window.whus_data&&window.whus_data.grid;
if(!g||!g.length) return '[]';
return JSON.stringify(g.map(function(r){return {모델명:r.Ppm_Mdl_Nm, 브랜드:r.Ppm_Bnd_Nm,
  원가:r.Ppm_Cost, 재고:r.Lim_Cpter_Stock_Qty};}));
"""


def _parse_xlsx(path: str) -> list[dict]:
    """상품DB 엑셀에서 모델명·원가·브랜드·재고 컬럼을 헤더로 자동 감지해 추출."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # 헤더 행 찾기: '모델' 과 '원가' 가 모두 들어있는 행
    hdr_idx, headers = None, None
    for i, r in enumerate(rows[:10]):
        cells = [str(c).strip() if c is not None else "" for c in r]
        joined = " ".join(cells)
        if "모델" in joined and "원가" in joined:
            hdr_idx, headers = i, cells
            break
    if hdr_idx is None:
        return []

    def col(pred):
        for j, h in enumerate(headers):
            if pred(h):
                return j
        return None

    c_model = col(lambda h: "모델" in h)
    c_cost = col(lambda h: ("원가" in h) and ("판매" not in h))
    c_brand = col(lambda h: "브랜드" in h)
    c_stock = col(lambda h: ("재고" in h))

    out = []
    for r in rows[hdr_idx + 1:]:
        if not r:
            continue
        model = r[c_model] if c_model is not None and c_model < len(r) else None
        if model is None or str(model).strip() == "":
            continue
        out.append({
            "모델명": str(model).strip(),
            "브랜드": (str(r[c_brand]).strip() if c_brand is not None and c_brand < len(r) and r[c_brand] is not None else ""),
            "원가": (r[c_cost] if c_cost is not None and c_cost < len(r) else 0),
            "재고": (r[c_stock] if c_stock is not None and c_stock < len(r) else ""),
        })
    return out


def fetch_wizfasta_costs(progress=None, login_wait: int = 300) -> list[dict]:
    """상품DB 전체 엑셀을 받아 모델명·원가를 추출(실패 시 그리드 폴백)."""
    import json as _json
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    def say(m):
        if progress:
            progress(m)

    dl_dir = _appdir("wiz_download")
    # 이전 엑셀 정리
    for f in glob.glob(os.path.join(dl_dir, "*")):
        try:
            os.remove(f)
        except OSError:
            pass

    opts = Options()
    opts.add_argument(f"--user-data-dir={_appdir('wizfasta_chrome')}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("prefs", {
        "download.default_directory": dl_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "safebrowsing.enabled": True,
    })

    say("Chrome 시작 중…")
    driver = webdriver.Chrome(options=opts)
    try:
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior",
                                   {"behavior": "allow", "downloadPath": dl_dir})
        except Exception:
            pass

        driver.get(PRDDB_URL)
        # 로그인 대기
        deadline = time.time() + login_wait
        warned = False
        while "login" in driver.current_url.lower():
            if not warned:
                say("열린 Chrome 창에서 Wizfasta에 로그인해 주세요. (로그인하면 자동 진행)")
                warned = True
            if time.time() > deadline:
                raise TimeoutError("로그인 대기 시간 초과")
            time.sleep(2)
        if "PrdDbList" not in driver.current_url:
            driver.get(PRDDB_URL)
            time.sleep(2)

        say("상품DB 조회 중… (일반상품 / 재고수량≥1)")
        driver.execute_script(_SETUP_JS)

        # 그리드 로딩 대기
        gdeadline = time.time() + 60
        grid_n = 0
        while time.time() < gdeadline:
            time.sleep(1.5)
            grid_n = driver.execute_script(_GRID_LEN_JS) or 0
            if grid_n:
                break
        say(f"조회 완료: {grid_n}건. 전체 엑셀 다운로드 중…")

        # 전체 엑셀 다운로드 클릭
        driver.execute_script(_EXCEL_CLICK_JS)

        # 다운로드 대기(진행 표시)
        xlsx = None
        wdeadline = time.time() + 180
        while time.time() < wdeadline:
            time.sleep(1.5)
            files = [f for f in glob.glob(os.path.join(dl_dir, "*"))
                     if f.lower().endswith((".xlsx", ".xls"))]
            crs = glob.glob(os.path.join(dl_dir, "*.crdownload"))
            if files and not crs:
                # 다운로드 완료 추정(크기 안정)
                xlsx = max(files, key=os.path.getmtime)
                s1 = os.path.getsize(xlsx)
                time.sleep(1.0)
                if os.path.getsize(xlsx) == s1:
                    break
            secs = int(time.time() - (wdeadline - 180))
            say(f"엑셀 다운로드 대기 중… {secs}s")

        if xlsx:
            say("엑셀 파싱 중…")
            rows = _parse_xlsx(xlsx)
            if rows:
                say(f"완료: {len(rows)}건 (엑셀)")
                return rows

        # 폴백: 그리드 직접 추출
        say("엑셀 다운로드 실패 → 그리드에서 추출 중…")
        data = driver.execute_script(_GRID_EXTRACT_JS)
        rows = _json.loads(data) if data else []
        say(f"완료: {len(rows)}건 (그리드)")
        return rows
    finally:
        try:
            driver.quit()
        except Exception:
            pass
