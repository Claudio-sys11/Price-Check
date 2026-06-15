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


def find_chrome() -> str | None:
    """설치된 컴퓨터의 Chrome 실행파일 경로를 찾는다(표준 경로 → 레지스트리)."""
    cands = [
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
                val, _ = winreg.QueryValueEx(k, None)
                if val and os.path.exists(val):
                    return val
            except OSError:
                pass
    except Exception:
        pass
    import shutil
    return shutil.which("chrome")


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


# Wizfasta 셀러로그인 자동 입력 (필드: corpCode / txtUserId / txtUserPwd, 버튼 btnLogin)
_LOGIN_JS = r"""
var c=document.getElementById('corpCode'), u=document.getElementById('txtUserId'),
    p=document.getElementById('txtUserPwd'), b=document.getElementById('btnLogin');
if(!c||!u||!p||!b) return 'no-form';
c.value=arguments[0]; u.value=arguments[1]; p.value=arguments[2];
b.click(); return 'submitted';
"""


def fetch_wizfasta_costs(corp: str = "", uid: str = "", pw: str = "",
                         progress=None, headless: bool = True) -> list[dict]:
    """상품DB 전체 엑셀을 받아 모델명·원가를 추출(실패 시 그리드 폴백).

    corp/uid/pw 가 주어지면 Wizfasta에 자동 로그인한다. progress(step_key, detail)
    콜백으로 체크리스트 단계를 보고한다(step_key: start/login/query/download/parse).
    """
    import json as _json
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    def step(key, detail=""):
        if progress:
            progress(key, detail)

    dl_dir = _appdir("wiz_download")
    for f in glob.glob(os.path.join(dl_dir, "*")):
        try:
            os.remove(f)
        except OSError:
            pass

    chrome_bin = find_chrome()
    if not chrome_bin:
        raise RuntimeError("이 컴퓨터에서 Chrome을 찾지 못했습니다. Chrome을 설치해 주세요.")

    opts = Options()
    opts.binary_location = chrome_bin   # 설치된 컴퓨터의 Chrome 사용
    # 고정 프로필(user-data-dir)은 잠금으로 멈출 수 있어 사용하지 않음 → 매번 임시 프로필 + 자동 로그인
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    if headless:
        opts.add_argument("--headless=new")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("prefs", {
        "download.default_directory": dl_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "safebrowsing.enabled": True,
    })

    step("start", "Chrome 연결 중")
    try:
        driver = webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Chrome 시작 실패: {exc}") from exc
    try:
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior",
                                   {"behavior": "allow", "downloadPath": dl_dir})
        except Exception:
            pass

        driver.get(PRDDB_URL)

        # 로그인 처리: 로그인 페이지면 자동 로그인
        if "login" in driver.current_url.lower():
            step("login")
            if not (corp and uid and pw):
                raise RuntimeError("Wizfasta 로그인 정보가 없습니다. [설정]에서 입력하세요.")
            res = driver.execute_script(_LOGIN_JS, corp, uid, pw)
            if res == "no-form":
                raise RuntimeError("Wizfasta 로그인 폼을 찾지 못했습니다.")
            # 로그인 완료(리다이렉트) 대기
            ldl = time.time() + 30
            while "login" in driver.current_url.lower() and time.time() < ldl:
                time.sleep(1)
            if "login" in driver.current_url.lower():
                raise RuntimeError("Wizfasta 로그인 실패 — 업체코드/아이디/비밀번호를 확인하세요.")
            driver.get(PRDDB_URL)
            time.sleep(1.5)
        else:
            step("login", "세션 유지")

        if "PrdDbList" not in driver.current_url:
            driver.get(PRDDB_URL)
            time.sleep(1.5)

        step("query")
        driver.execute_script(_SETUP_JS)
        gdeadline = time.time() + 60
        grid_n = 0
        while time.time() < gdeadline:
            time.sleep(1.5)
            grid_n = driver.execute_script(_GRID_LEN_JS) or 0
            if grid_n:
                break
        step("query", f"{grid_n}건")

        step("download", "시작")
        driver.execute_script(_EXCEL_CLICK_JS)
        xlsx = None
        start = time.time()
        wdeadline = start + 180
        while time.time() < wdeadline:
            time.sleep(1.5)
            files = [f for f in glob.glob(os.path.join(dl_dir, "*"))
                     if f.lower().endswith((".xlsx", ".xls"))]
            crs = glob.glob(os.path.join(dl_dir, "*.crdownload"))
            if files and not crs:
                xlsx = max(files, key=os.path.getmtime)
                s1 = os.path.getsize(xlsx)
                time.sleep(1.0)
                if os.path.getsize(xlsx) == s1:
                    break
            step("download", f"{int(time.time() - start)}s")

        step("parse")
        if xlsx:
            rows = _parse_xlsx(xlsx)
            if rows:
                step("parse", f"{len(rows)}건 (엑셀)")
                return rows

        # 폴백: 그리드 추출
        data = driver.execute_script(_GRID_EXTRACT_JS)
        rows = _json.loads(data) if data else []
        step("parse", f"{len(rows)}건 (그리드)")
        return rows
    finally:
        try:
            driver.quit()
        except Exception:
            pass
