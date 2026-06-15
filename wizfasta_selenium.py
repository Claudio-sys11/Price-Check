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
import subprocess
import threading
import time

PRDDB_URL = "https://www.wizfasta.com/ProductMng/PrdDbList.aspx"
LOGIN_URL = "https://www.wizfasta.com/Login/Login.aspx"


class FetchStopped(Exception):
    """사용자가 중단(취소)한 경우."""


class FetchTimeout(Exception):
    """전체 제한시간(기본 60초) 초과 — 재시작 대상."""


def _kill_chromedriver() -> None:
    """멈춘 chromedriver만 정리(사용자의 일반 Chrome 창은 건드리지 않음)."""
    try:
        subprocess.run(["taskkill", "/IM", "chromedriver.exe", "/F"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


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


def _make_driver(opts):
    """Chrome 드라이버 생성. chromedriver는 webdriver-manager(설치 Chrome 버전에 맞춰
    자동 다운로드·캐시)로 확보하고, 실패 시 Selenium Manager 기본 경로로 폴백한다.
    프리징(.exe) 환경에서도 안정적으로 동작하도록 함."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    errors = []
    # 1) webdriver-manager (순수 파이썬, exe 에서 안정적)
    try:
        from webdriver_manager.chrome import ChromeDriverManager
        path = ChromeDriverManager().install()
        return webdriver.Chrome(service=Service(path), options=opts)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"webdriver-manager: {exc}")
    # 2) Selenium Manager 기본
    try:
        return webdriver.Chrome(options=opts)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"selenium-manager: {exc}")
    raise RuntimeError("Chrome/chromedriver 시작 실패 — " + " / ".join(errors)[:300])


def _make_driver_timeout(opts, timeout: int = 60):
    """드라이버 생성을 timeout 초 안에 끝내지 못하면 종료(재시도 가능)."""
    box = {}

    def work():
        try:
            box["d"] = _make_driver(opts)
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        _kill_chromedriver()   # 멈춘 드라이버 정리
        raise TimeoutError(
            f"Chrome 시작이 {timeout}초 내에 완료되지 않았습니다. "
            "[Wizfasta 원가 가져오기]를 다시 눌러 재시도하세요.")
    if "e" in box:
        raise box["e"]
    return box["d"]


# Wizfasta 셀러 로그인 폼 채우기 (필드: corpCode / txtUserId / txtUserPwd)
# 값은 DOM과 jQuery 양쪽으로 넣고 input/change 이벤트를 발생시킨다(페이지의 fn_Login 이
# $("#corpCode").val() 등으로 읽으므로 jQuery 값 동기화가 중요).
_LOGIN_FILL_JS = r"""
var c=document.getElementById('corpCode'), u=document.getElementById('txtUserId'),
    p=document.getElementById('txtUserPwd');
if(!c||!u||!p) return 'no-form';
function setv(el,v){ el.value=v;
  try{ if(window.jQuery){ window.jQuery(el).val(v); } }catch(e){}
  try{ el.dispatchEvent(new Event('input',{bubbles:true})); }catch(e){}
  try{ el.dispatchEvent(new Event('change',{bubbles:true})); }catch(e){}
}
setv(c,arguments[0]); setv(u,arguments[1]); setv(p,arguments[2]);
try{ var h=document.getElementById('hdLoginDiv'); if(h) h.value='Seller'; }catch(e){}
return 'filled';
"""

# 셀러 로그인 실행: 페이지의 실제 로그인 함수 fn_Login() 호출
# (fn_Login 이 /Login/Login_Ajax.aspx 로 AJAX → 성공 시 alert 후 location.href 리다이렉트)
_LOGIN_SUBMIT_JS = r"""
try{ if(typeof fn_Login==='function'){ fn_Login(); return 'fn_Login'; } }catch(e){ return 'err:'+e; }
var b=document.getElementById('btnLogin'); if(b){ b.click(); return 'click'; }
return 'no-button';
"""


def _login_wizfasta(driver, corp, uid, pw, step, guard):
    """Wizfasta 셀러 로그인.

    동작: 폼 채우기 → fn_Login() 호출 → (성공/실패) alert 수락 → 리다이렉트 확인.
    - 로그인 성공 시 성공 메시지 alert 가 뜨며, 이 alert 를 수락해야 location.href 리다이렉트가
      진행된다(수락하지 않으면 로그인 페이지에 멈춤 = 기존 증상).
    - 실패 시 오류 메시지 alert 후 로그인 페이지에 머문다 → RuntimeError 로 사유를 보고.
    """
    from selenium.common.exceptions import NoAlertPresentException

    def take_alert():
        """대기 중인 alert 가 있으면 텍스트를 읽고 수락, 없으면 None."""
        try:
            al = driver.switch_to.alert
            t = (al.text or "").strip()
            try:
                al.accept()
            except Exception:
                pass
            return t
        except NoAlertPresentException:
            return None
        except Exception:
            return None

    # 1) 로그인 폼(corpCode) 로드 대기
    for _ in range(20):
        guard(driver)
        try:
            if driver.execute_script("return !!document.getElementById('corpCode');"):
                break
        except Exception:
            pass
        time.sleep(0.5)

    # 2) 값 채우기
    step("login", "로그인 정보 입력")
    if driver.execute_script(_LOGIN_FILL_JS, corp, uid, pw) == "no-form":
        raise RuntimeError("Wizfasta 로그인 폼을 찾지 못했습니다. (페이지 구조가 바뀌었을 수 있습니다)")

    # 3) 로그인 실행
    step("login", "로그인 시도")
    driver.execute_script(_LOGIN_SUBMIT_JS)

    # 4) 결과 대기: alert(성공/실패) 수락 + 리다이렉트 확인
    last_msg = ""
    while True:
        guard(driver)
        t = take_alert()
        if t is not None:
            last_msg = t or last_msg
            if t:
                step("login", t[:24])
            # alert 수락 후 리다이렉트가 진행될 여유를 두고 URL 변화 확인
            for _ in range(12):
                guard(driver)
                t2 = take_alert()
                if t2 is not None:        # 연속 alert 처리
                    if t2:
                        last_msg = t2
                    continue
                try:
                    url = driver.current_url.lower()
                except Exception:
                    time.sleep(0.4)
                    continue
                if "login_otp" in url:
                    raise RuntimeError("이 계정은 OTP(2단계) 인증이 설정되어 자동 로그인할 수 없습니다. "
                                       "Wizfasta에서 OTP를 해제하거나 직접 로그인하세요.")
                if "passwordcampaign" in url:
                    raise RuntimeError("비밀번호 변경 안내 페이지로 이동했습니다. Wizfasta에 직접 로그인해 "
                                       "비밀번호 절차를 완료한 뒤 다시 시도하세요.")
                if "login" not in url:     # 로그인 페이지를 벗어남 = 성공
                    step("login", "로그인 성공")
                    return
                time.sleep(0.4)
            # 여유시간 내 리다이렉트 없음 → 로그인 실패(자격증명 오류 등)
            raise RuntimeError("Wizfasta 로그인 실패: " +
                               (last_msg or "업체코드 / 아이디 / 비밀번호를 확인하세요."))

        # alert 가 아직 없음 → 즉시 리다이렉트(세션 유지 등) 여부 확인
        try:
            url = driver.current_url.lower()
        except Exception:
            time.sleep(0.3)
            continue
        if url and "login" not in url:
            step("login", "로그인 성공")
            return
        time.sleep(0.4)


def fetch_wizfasta_costs(corp: str = "", uid: str = "", pw: str = "",
                         progress=None, headless: bool = True,
                         start_timeout: int = 60, overall_timeout: int = 60,
                         should_stop=None) -> list[dict]:
    """상품DB 전체 엑셀을 받아 모델명·원가를 추출(실패 시 그리드 폴백).

    corp/uid/pw 가 주어지면 Wizfasta에 자동 로그인한다. progress(step_key, detail)
    콜백으로 체크리스트 단계를 보고한다(step_key: start/login/query/download/parse).
    should_stop() 가 True면 즉시 중단(FetchStopped), 전체 overall_timeout 초과 시 FetchTimeout.
    """
    import json as _json
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    _t0 = time.time()

    def step(key, detail=""):
        if progress:
            progress(key, detail)

    def guard(driver=None):
        """중단/전체 타임아웃 점검 — 해당 시 드라이버 정리 후 예외."""
        stop = should_stop and should_stop()
        over = (time.time() - _t0) > overall_timeout
        if stop or over:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
            _kill_chromedriver()
            if stop:
                raise FetchStopped("사용자가 중단했습니다.")
            raise FetchTimeout(f"{overall_timeout}초 초과")

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

    step("start", "Chrome 창 여는 중 (chromedriver 확보)")
    driver = _make_driver_timeout(opts, start_timeout)   # 60초 내 미시작 시 종료(재시도 가능)
    try:
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior",
                                   {"behavior": "allow", "downloadPath": dl_dir})
        except Exception:
            pass

        try:
            driver.set_page_load_timeout(25)   # 페이지 로드 무한 대기 방지
        except Exception:
            pass

        # 로그인 화면으로 이동 후 자동 로그인
        step("login", "로그인 화면 이동")
        guard(driver)
        try:
            driver.get(LOGIN_URL)
        except Exception:
            pass   # 페이지 로드 타임아웃이어도 폼은 떠 있을 수 있음 → 계속 진행
        time.sleep(0.5)

        if "login" in driver.current_url.lower():
            if not (corp and uid and pw):
                raise RuntimeError("Wizfasta 로그인 정보가 없습니다. "
                                   "[설정]에서 업체코드 / 아이디 / 비밀번호를 입력하세요.")
            _login_wizfasta(driver, corp, uid, pw, step, guard)
        else:
            step("login", "세션 유지")

        if "PrdDbList" not in driver.current_url:
            guard(driver)
            try:
                driver.get(PRDDB_URL)
            except Exception:
                pass
            time.sleep(1.5)

        step("query", "상품DB 조회")
        guard(driver)
        driver.execute_script(_SETUP_JS)
        grid_n = 0
        for _ in range(40):          # 조회 결과(그리드) 로드 대기 — 전체 타임아웃은 guard 가 적용
            guard(driver)
            time.sleep(0.8)
            grid_n = driver.execute_script(_GRID_LEN_JS) or 0
            if grid_n:
                break
        step("query", f"{grid_n}건")

        # 조회 시점에 그리드(window.whus_data.grid)에 모델명·브랜드·원가·재고가 모두 로드된다.
        # headless 환경에서 '전체 엑셀 다운로드'는 브라우저가 차단할 수 있어, 그리드 직접 추출을
        # 기본 경로로 사용한다(빠르고 안정적). 그리드가 비어 있을 때만 엑셀 다운로드로 폴백.
        step("download", "상품 데이터 수집")
        guard(driver)
        data = driver.execute_script(_GRID_EXTRACT_JS)
        rows = _json.loads(data) if data else []
        if rows:
            step("download", f"{len(rows)}건 수집")
            step("parse", f"{len(rows)}건")
            return rows

        # 폴백: 그리드가 비어 있으면 '전체 엑셀 다운로드' 시도(최대 30초)
        step("download", "엑셀 다운로드 시도")
        driver.execute_script(_EXCEL_CLICK_JS)
        xlsx = None
        start = time.time()
        while time.time() - start < 30:
            guard(driver)
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
            step("parse", f"{len(rows)}건 (엑셀)")
            return rows
        step("parse", "0건")
        return []
    finally:
        try:
            driver.quit()
        except Exception:
            pass
