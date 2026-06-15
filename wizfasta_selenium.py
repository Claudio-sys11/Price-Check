"""
Wizfasta 상품DB에서 모델명·원가를 가져오는 Selenium 스크레이퍼.

동작:
  - 앱 전용 Chrome 프로필(%APPDATA%\\EcountInventory\\wizfasta_chrome)로 Chrome 실행.
    → Wizfasta에 한 번만 로그인하면 이후엔 세션이 유지되어 자동 진행.
  - 상품DB(PrdDbList.aspx)에서 등록유형=일반상품, 재고수량≥1, 500개씩 조회.
  - 그리드(window.whus_data.grid)에서 모델명(Ppm_Mdl_Nm)·원가(Ppm_Cost)·
    브랜드(Ppm_Bnd_Nm)·재고(Lim_Cpter_Stock_Qty)를 추출.

Selenium 4.x 의 Selenium Manager가 chromedriver를 자동으로 받아 사용한다.
"""

from __future__ import annotations

import os
import time

PRDDB_URL = "https://www.wizfasta.com/ProductMng/PrdDbList.aspx"


def _profile_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "EcountInventory", "wizfasta_chrome")
    os.makedirs(path, exist_ok=True)
    return path


# 필터 설정 + 조회 실행 JS
_SETUP_JS = r"""
var setSel=function(name,val){var s=document.querySelector('select[name="'+name+'"]')||
  Array.prototype.find.call(document.querySelectorAll('select'),function(x){return (x.name||x.id)===name;});
  if(s){s.value=val; s.dispatchEvent(new Event('change',{bubbles:true}));}};
var setInp=function(name,val){var i=document.querySelector('input[name="'+name+'"]')||
  Array.prototype.find.call(document.querySelectorAll('input'),function(x){return (x.name||x.id)===name;});
  if(i){i.value=val; i.dispatchEvent(new Event('change',{bubbles:true}));}};
setSel('selPrDivCd','PrDivCd_Gen');   // 등록유형=일반상품
setInp('txtStockNum','1');            // 재고수량 시작=1
setSel('pageSize','500');             // 500개씩
// 조회 버튼 클릭
var btn=Array.prototype.find.call(document.querySelectorAll('a'),function(a){return (a.innerText||'').trim()==='조회';});
if(btn){btn.click(); return 'clicked';} return 'no-button';
"""

_EXTRACT_JS = r"""
var g=window.whus_data&&window.whus_data.grid;
if(!g||!g.length) return JSON.stringify([]);
return JSON.stringify(g.map(function(r){return {
  모델명:r.Ppm_Mdl_Nm, 브랜드:r.Ppm_Bnd_Nm,
  원가:r.Ppm_Cost, 재고:r.Lim_Cpter_Stock_Qty};}));
"""


def fetch_wizfasta_costs(progress=None, login_wait: int = 240,
                         headless: bool = False) -> list[dict]:
    """상품DB에서 모델명·원가를 추출해 리스트로 반환한다.

    progress(msg) 콜백으로 상태 안내. Wizfasta 미로그인 시 창에서 직접 로그인하면
    감지 후 자동 진행한다(login_wait 초 대기).
    """
    import json as _json
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    def say(m):
        if progress:
            progress(m)

    opts = Options()
    opts.add_argument(f"--user-data-dir={_profile_dir()}")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    if headless:
        opts.add_argument("--headless=new")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])

    say("Chrome 시작 중…")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(PRDDB_URL)
        # 로그인 대기(미로그인 시 Login 페이지로 리다이렉트)
        deadline = time.time() + login_wait
        warned = False
        while "login" in driver.current_url.lower():
            if not warned:
                say("Wizfasta에 로그인해 주세요. (로그인하면 자동으로 계속됩니다)")
                warned = True
            if time.time() > deadline:
                raise TimeoutError("로그인 대기 시간 초과")
            time.sleep(2)

        # 상품DB 화면 보장
        if "PrdDbList" not in driver.current_url:
            driver.get(PRDDB_URL)
            time.sleep(2)

        say("상품DB 조회 중… (일반상품 / 재고수량≥1)")
        driver.execute_script(_SETUP_JS)

        # 그리드 로딩 대기
        deadline = time.time() + 60
        rows = []
        while time.time() < deadline:
            time.sleep(1.5)
            data = driver.execute_script(_EXTRACT_JS)
            rows = _json.loads(data) if data else []
            if rows:
                break
        say(f"추출 완료: {len(rows)}건")
        return rows
    finally:
        try:
            driver.quit()
        except Exception:
            pass
