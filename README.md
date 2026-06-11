# EcountERP 재고현황 API 연동

EcountERP Open API로 **재고현황**을 조회해 `output/` 폴더에 저장하는 Python 프로그램입니다.
이후 Wizfasta(스토어팜 판매상품) 데이터와 결합해 가격/재고 비교에 사용할 수 있습니다.

## 호출 흐름

1. **Zone 조회** — `POST https://oapi.ecount.com/OAPI/V2/Zone` (회사코드 → ZONE)
2. **로그인** — `POST https://oapi{ZONE}.ecount.com/OAPI/V2/OAPILogin` → `SESSION_ID`
3. **재고현황** — `POST https://oapi{ZONE}.ecount.com/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatusByLocation?SESSION_ID=...`

> 운영(production) 도메인은 `oapi`, 테스트(상자테스트)는 `sboapi` 를 사용합니다. (config.json 의 `ENV`)

## 설치

```powershell
pip install -r requirements.txt
```

## 설정

1. `config.example.json` 을 `config.json` 으로 복사
2. `config.json` 에 실제 값 입력:
   - `COM_CODE` : 회사코드
   - `USER_ID` : API 사용자 ID
   - `API_CERT_KEY` : EcountERP에서 발급받은 API 인증키
     (EcountERP 내 **Self-Customizing > 정보관리 > API 인증키 발급**)

> ⚠️ `config.json` 에는 인증키가 들어가므로 외부에 공유하거나 버전관리에 올리지 마세요.

## 실행

```powershell
python main.py
```

결과:
- `output/inventory_raw.json` — API 응답 원문
- `output/inventory.csv` — 표로 변환한 재고현황 (행 데이터가 있을 경우)

## 재고현황 파라미터 조정

재고현황 엔드포인트의 요청 파라미터 이름(`BASE_DATE`, `PROD_CD`, `WH_CD` 등)은
계정/버전에 따라 다를 수 있습니다. EcountERP의 **API 매뉴얼**에서 정확한 필드명을 확인한 뒤
`config.json` 의 `inventory.payload` 를 수정하세요. 빈 값으로 두면 전체 조회를 시도합니다.

엔드포인트 자체를 바꾸려면 `inventory.endpoint` 를 수정합니다. 예:
- 위치(창고)별: `/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatusByLocation`
- 품목별 합계: `/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus`

## Windows 데스크톱 앱 / 설치파일

명령줄 대신 GUI로도 사용할 수 있습니다.

- **앱(실행 파일)**: `dist\EcountInventory.exe` — 설치 없이 바로 실행
- **설치파일**: `installer\EcountInventory_Setup.exe` — 더블클릭하면 설치 마법사 실행
  (시작 메뉴 등록, 바탕화면 아이콘 선택 가능, 제거 프로그램 포함)

GUI 사용법:
1. 앱 실행 → 상단 **[설정] 메뉴 → '인증 정보 설정...'** 에서 회사코드 / 사용자ID / API 인증키 / 환경 입력 후 저장
   (인증정보는 보안을 위해 메인 화면에 표시하지 않고 설정 메뉴에서만 다룹니다)
2. (선택) 기준일자·**브랜드**·**모델명**·품목코드·창고코드 입력 — 브랜드·모델명은 부분일치 필터
3. **재고현황 조회** 클릭 → 표로 결과 표시 (기본 정렬: **브랜드 → 모델명 → 사이즈(S·M·L 순, 숫자는 작은→큰) → 입고일자**)
4. 조회 후 **브랜드·모델명을 바꾸면 재조회 없이 즉시 재필터**됩니다(이미 받아온 데이터에서 필터링).
5. **열 머리글 클릭** 시 그 열 기준 정렬(클릭마다 오름차순 ▲ ↔ 내림차순 ▼ 토글, 금액·수량은 숫자 기준).
6. 열 너비는 조회된 내용에 맞춰 **자동 최적화**됩니다.
4. **CSV 내보내기** 로 저장

> UI: ttk `clam` 테마 기반의 정돈된 디자인(**민트** 헤더바·버튼·탭·표 헤더, 카드형 입력영역,
> 줄무늬 표, 소계 강조행). 금액(입고단가·총단가·평균)은 **천단위 콤마**로 표시.

> 설정은 `%APPDATA%\EcountInventory\config.json` 에 저장됩니다.
> (설치형 exe 는 Program Files 에서 실행되므로 쓰기 가능한 사용자 폴더에 보관)

### 재빌드 방법

```powershell
pip install pyinstaller
# 1) 앱 exe 빌드
python -m PyInstaller --noconfirm --onefile --windowed --name EcountInventory --hidden-import requests gui.py
# 2) 설치파일 빌드 (Inno Setup 6 필요)
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

## 가격비교 (Wizfasta ↔ EcountERP)

Wizfasta 스토어팜 판매상품과 EcountERP 재고현황을 **품목코드 기준**으로 결합해 비교합니다.

### 데이터 키

| 구분 | 키 필드 |
|---|---|
| Wizfasta | 품목코드 (`Mpm_Pr_Cd`) — 한 품목코드에 여러 판매상품이 매핑될 수 있음(1:N) |
| EcountERP | 품목코드 (`PROD_CD` 등 — 자동 감지/매핑) |

> EcountERP 재고 응답의 필드명(품목코드/재고수량/단가)은 자동 감지되며,
> 필요 시 `config.json` 의 `ecount_fields` 로 정확히 지정할 수 있습니다.

### ① Wizfasta 데이터 추출 (브라우저)

1. Wizfasta 로그인 → [상품관리 > 판매상품등록]
2. 쇼핑몰 선택=**스토어팜**, 등록유형=**일반상품** → [조회]
3. F12 개발자도구 > Console 에 `wizfasta_extract.js` 내용을 붙여넣고 실행
4. `wizfasta_products.json` 다운로드 → 프로그램의 `data/` 폴더에 배치

### ② 비교 실행

- **GUI**: 앱 실행 → ①탭에서 재고현황 조회 → ②탭에서 Wizfasta JSON 지정 후 [가격비교 실행]
- **CLI**: `python compare.py` (사전에 `python main.py` 로 `output/inventory_raw.json` 생성)

결과 컬럼: 품목코드 / 브랜드 / 모델명 / 판매상품명 / Wiz_원가 / Wiz_기준판매가 / Wiz_판매가 /
Wiz_수수료율 / Ecount_재고수량 / Ecount_단가 / 원가-단가차이 / 매칭여부

## 자동 업데이트 (실행 시 버전 체크)

앱 실행 시 백그라운드로 최신 버전을 확인하고, 새 버전이 있으면 안내 후 자동으로 내려받아 설치합니다.
업데이트 배포는 **GitHub Releases** 를 사용합니다.

- 현재 버전: `version.py` 의 `APP_VERSION` (설치 스크립트 `MyAppVersion` 과 동일하게 유지)
- 동작: `config.json` 의 `github_repo` 의 **최신 릴리스(tag_name)** 를 조회 → 현재 버전보다 높으면
  → "새 버전 있습니다" 안내 → 동의 시 릴리스의 `.exe` 에셋 다운로드 → 실행(설치파일이 실행 중 앱을 닫고 교체)

**설정 (GitHub Releases)**
1. GitHub 저장소 생성 (예: `your-id/EcountInventory`)
2. `config.json` 에 `"github_repo": "your-id/EcountInventory"` 지정
3. 새 버전 배포 시:
   - `version.py` 의 `APP_VERSION` 과 `installer.iss` 의 `MyAppVersion` 을 함께 올림 (예: 1.0.3)
   - exe·설치파일 재빌드
   - GitHub에서 **새 Release 생성**: 태그를 `v1.0.3` 처럼 버전으로, 빌드한 `EcountInventory_Setup.exe` 를 **에셋으로 첨부**(이름에 "Setup" 포함 권장)
   - 게시(Publish) → 사용자가 앱을 켜면 자동으로 감지·업데이트

> 비공개(private) 저장소면 에셋 다운로드에 토큰이 필요합니다 — **공개(public) 저장소** 권장.
> GitHub 대신 일반 웹서버를 쓰려면 `update_url`(매니페스트 JSON 주소)도 지원합니다(`update.example.json` 형식).

## 재고현황 — 전체 항목 연동

재고현황 조회 시 **창고별 재고현황 API(`GetListInventoryBalanceStatusByLocation`)** 를 우선 사용해
창고·품목명·규격·재고를 한 번에 연동합니다. 그리고 품목명(`브랜드_모델명_사이즈_입고일자`)을
`_` 기준으로 분해합니다.

| 품목명 구간 | 컬럼 |
|---|---|
| 첫 번째 `_` 앞 | 브랜드 |
| 첫 번째 `_` 다음 | 모델명 |
| 두 번째 `_` 다음 | 사이즈 |
| 세 번째 `_` 다음 | 입고일자 |

표시 컬럼:
**브랜드 / 모델명 / 사이즈 / 입고일자 / 품목코드 / 창고코드 / 창고명 / 재고수량 / 입고단가 / 총단가**

- **입고단가**: 품목등록(`GetBasicProductsList`)의 `IN_PRICE`를 **품목코드별로 개별 조회**해 매칭. **총단가 = 입고단가 × 재고수량**.
  - EcountERP 제약(빈 조회는 첫 1만건만·페이지네이션 없음, 개별 조회는 세션당 2건 제한) 때문에, **재로그인을 반복**하며 코드별로 받아 `%APPDATA%\EcountInventory\price_cache.json` 에 **캐시**합니다.
  - 첫 조회 시 누락 입고단가를 백그라운드로 채우며(약 코드 수×0.26초), 완료되면 표가 자동 갱신됩니다. 이후 조회는 캐시로 즉시. 가격 변경 시 [설정 → 입고단가 캐시 비우기].
- **소계 행**: **같은 브랜드+모델명**(여러 사이즈·창고 합산) 하단에 중간합계(재고수량 합·총단가 합·**평균 단가**=총단가합/수량합)를 강조 표시.
- 품목명·규격 컬럼은 표시하지 않음(품목명은 브랜드/모델명/사이즈/입고일자 분해에만 사용).
- 재고수량·금액은 **소수점을 제외한 정수**(천단위 콤마). 기본 가운데 정렬이며 **금액(입고단가·총단가)은 우측정렬**.
- **[소계/평균만 내보내기]** 버튼: 현재 표의 **소계 행만**(브랜드·모델명·재고수량 합·평균단가·총단가 합) CSV 로 내보냅니다.
- 동작 순서: ① 창고별 재고현황(ByLocation) → 권한 없으면 ② 기본 재고현황 → 입고단가·품목명은 품목 조회로 보완.

> ⚠️ 창고별/품목 조회 API 권한이 없는 인증키(예: 평가용 키의 운영 제한)에서는 항목 일부가 비어 있을 수 있습니다.
> EcountERP에서 해당 API 권한을 켜거나 운영용 키를 사용하면 다음 조회부터 모든 항목이 자동으로 채워집니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `ecount_api.py` | EcountERP API 클라이언트 (재고/품목 조회) |
| `compare.py` | 가격/재고 비교 + 품목명 분해 (필드 자동감지) |
| `version.py` | 앱 버전 (단일 출처) |
| `updater.py` | 자동 업데이트 엔진 |
| `gui.py` | 데스크톱 앱 (① 재고현황 / ② 가격비교 탭) |
| `main.py` | CLI 재고현황 조회 |
| `wizfasta_extract.js` | 브라우저 콘솔용 Wizfasta 추출 스니펫 |
| `data/wizfasta_products.json` | Wizfasta 추출 데이터(샘플 포함) |
| `installer.iss` | Inno Setup 설치 스크립트 |
