"""
EcountERP 재고현황 + Wizfasta 가격비교 - Windows 데스크톱 앱 (Tkinter GUI).

탭 구성:
  ① 재고현황 조회 : EcountERP Open API 로 재고현황 조회 → 표 표시 / CSV 내보내기
  ② 가격비교      : Wizfasta 판매상품(JSON)과 EcountERP 재고를 품목코드로 결합 비교

설정은 %APPDATA%\\EcountInventory\\config.json 에 저장됩니다.
"""

from __future__ import annotations

import json
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from ecount_api import EcountClient, EcountApiError
import compare as cmp
import updater
from version import APP_VERSION

APP_NAME = "EcountInventory"

# ===== 디자인 팔레트 =====
FONT = "Segoe UI"
BG = "#f3f4f6"          # 전체 배경(연한 회색)
CARD = "#ffffff"        # 카드/표 배경
TEXT = "#1f2937"        # 본문 텍스트
SUBTLE = "#374151"      # 라벨프레임 제목
MUTED = "#6b7280"       # 보조 텍스트
BORDER = "#d1d5db"      # 테두리
ACCENT = "#14b8a6"      # 강조(민트/틸)
ACCENT_ACTIVE = "#0d9488"
ACCENT_SOFT = "#cbf5ec"  # 민트 연한톤(헤더 보조 텍스트 등)
HEADING_BG = "#e0f7f1"  # 표 헤더 배경(연한 민트)
HEADING_FG = "#0f766e"  # 표 헤더 글자(진한 틸)
ROW_ALT = "#f5fbf9"     # 표 줄무늬(홀수행, 민트 기운)
SUBTOTAL_BG = "#d7f5ed"  # 소계 행 강조(민트)
SELECT_BG = "#a7e8da"   # 표 선택행(민트)

MONEY_COLS = {"입고단가", "총단가", "Wiz_원가", "Ecount_입고단가", "원가-입고단가차이"}  # 우측정렬 금액 컬럼

# Wizfasta 원가 가져오기 진행 체크리스트 단계
WIZ_STEPS = [
    ("start", "Chrome 시작"),
    ("login", "Wizfasta 로그인"),
    ("query", "상품DB 조회 (일반상품·재고≥1)"),
    ("download", "전체 엑셀 다운로드"),
    ("parse", "데이터 파싱"),
    ("match", "모델명 매칭"),
]


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_PATH = os.path.join(app_data_dir(), "config.json")
PRICE_CACHE_PATH = os.path.join(app_data_dir(), "price_cache.json")


def load_price_cache() -> dict:
    try:
        with open(PRICE_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_price_cache(cache: dict) -> None:
    try:
        with open(PRICE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass


def _sort_key(value) -> tuple:
    """정렬 키: 숫자로 해석되면 숫자 기준, 아니면 문자 기준(콤마 제거)."""
    s = str(value).replace(",", "").strip()
    try:
        return (0, float(s))
    except ValueError:
        return (1, str(value))


# 의류 사이즈 순서(S < M < L ...). 숫자 사이즈는 작은 것부터.
_SIZE_ORDER = {
    "XXS": 0, "XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5,
    "XXL": 6, "2XL": 6, "XXXL": 7, "3XL": 7, "4XL": 8,
    "U": 50, "F": 51, "FREE": 51, "ONE": 51, "ONESIZE": 51,
}


def _size_key(value) -> tuple:
    """사이즈 정렬 키: 글자 사이즈는 S,M,L 순 / 숫자 사이즈는 작은→큰 / 기타는 텍스트."""
    s = str(value).strip().upper()
    if s in _SIZE_ORDER:
        return (0, _SIZE_ORDER[s], 0.0, "")
    if re.fullmatch(r"[0-9]+(\.[0-9]+)?", s):     # 순수 숫자 사이즈
        return (1, 0, float(s), "")
    return (2, 0, 0.0, s)                          # 그 외 텍스트


def fill_tree(tree: ttk.Treeview, rows: list[dict],
              sort_callback=None, sort_col: str | None = None,
              sort_desc: bool = False) -> None:
    import tkinter.font as tkfont

    tree.delete(*tree.get_children())
    if not rows:
        tree["columns"] = ()
        return
    headers: list[str] = []
    for r in rows:
        for k in r.keys():
            if not k.startswith("_") and k not in headers:
                headers.append(k)
    tree["columns"] = headers

    # 내용에 맞춘 열 너비 자동 최적화
    fnt = tkfont.Font(family=FONT, size=10)
    fnt_b = tkfont.Font(family=FONT, size=10, weight="bold")
    for h in headers:
        arrow = (" ▼" if sort_desc else " ▲") if h == sort_col else ""
        head_text = f"{h}{arrow}"
        if sort_callback is not None:
            tree.heading(h, text=head_text, anchor="center",
                         command=lambda c=h: sort_callback(c))
        else:
            tree.heading(h, text=head_text, anchor="center")
        # 헤더(화살표 여유 포함) + 모든 셀 중 최대 픽셀폭
        w = fnt_b.measure(head_text + "  ")
        for r in rows:
            w = max(w, fnt.measure(str(r.get(h, ""))))
        w = min(max(w + 26, 60), 420)   # 여백 + 최소/최대 제한
        # 금액 컬럼은 우측정렬, 그 외는 가운데
        anchor = "e" if h in MONEY_COLS else "center"
        tree.column(h, width=w, anchor=anchor, stretch=False)

    tree.tag_configure("subtotal", background=SUBTOTAL_BG, font=(FONT, 10, "bold"))
    tree.tag_configure("odd", background=ROW_ALT)
    tree.tag_configure("even", background="white")
    i = 0
    for r in rows:
        if r.get("_subtotal"):
            tags: tuple = ("subtotal",)
        else:
            tags = ("odd",) if (i % 2) else ("even",)
            i += 1
        tree.insert("", "end", values=[r.get(h, "") for h in headers], tags=tags)


def export_rows_csv(rows: list[dict], initial: str = "export.csv") -> None:
    import csv
    if not rows:
        messagebox.showwarning("데이터 없음", "내보낼 데이터가 없습니다.")
        return
    path = filedialog.asksaveasfilename(
        title="CSV 저장", defaultextension=".csv",
        filetypes=[("CSV 파일", "*.csv"), ("모든 파일", "*.*")], initialfile=initial,
    )
    if not path:
        return
    headers: list[str] = []
    for r in rows:
        for k in r.keys():
            if not k.startswith("_") and k not in headers:
                headers.append(k)
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except OSError as exc:
        messagebox.showerror("저장 실패", str(exc))
        return
    messagebox.showinfo("저장 완료", f"{len(rows)}건을 저장했습니다.\n{path}")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"EcountERP 재고현황 / Wizfasta 가격비교  v{APP_VERSION}")
        self.geometry("1120x720")
        self.minsize(940, 600)
        self.configure(bg=BG)

        self._inventory_rows: list[dict] = []   # 탭1 조회 결과(원본: 가격비교 탭에서 사용)
        self._inventory_display: list[dict] = []  # 파싱 컬럼 포함 표시용(필터 적용 후)
        self._inventory_display_all: list[dict] = []  # 필터 전 전체(재필터용)
        self._inv_status_suffix: str = ""
        self._sort_col: str | None = None    # 정렬 기준 열(헤더 클릭)
        self._sort_desc: bool = False        # 내림차순 여부
        self._query_seq: int = 0             # 조회 시퀀스(백그라운드 가격조회 취소용)
        self._inventory_raw: dict | None = None
        self._compare_rows: list[dict] = []
        self._update_url: str = ""

        # 인증/설정 변수 (메인 화면엔 표시하지 않고 '설정' 메뉴 다이얼로그에서 입력)
        self.var_com = tk.StringVar()
        self.var_user = tk.StringVar()
        self.var_key = tk.StringVar()
        self.var_env = tk.StringVar(value="production")
        self.var_show_key = tk.BooleanVar(value=False)
        self.var_github = tk.StringVar()
        self.var_model = tk.StringVar()      # 조회조건: 모델명 필터(클라이언트측)
        self.var_brand = tk.StringVar()      # 조회조건: 브랜드 필터(클라이언트측)
        self.var_wcorp = tk.StringVar()      # Wizfasta 업체코드
        self.var_wid = tk.StringVar()        # Wizfasta 아이디
        self.var_wpw = tk.StringVar()        # Wizfasta 비밀번호
        self.var_wshow = tk.BooleanVar(value=False)
        self.ent_key: ttk.Entry | None = None
        self.ent_wpw: ttk.Entry | None = None

        self._apply_theme()
        self._build_menu()
        self._build_header()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        self.tab_inv = ttk.Frame(nb, style="Tab.TFrame")
        self.tab_cmp = ttk.Frame(nb, style="Tab.TFrame")
        nb.add(self.tab_inv, text="  재고현황 조회  ")
        nb.add(self.tab_cmp, text="  가격비교  ")

        self._build_inventory_tab()
        self._build_compare_tab()
        self._load_config()

        # 실행 시 백그라운드로 업데이트 확인
        self.after(800, self._start_update_check)

    # ================= 디자인 테마 =================
    def _apply_theme(self) -> None:
        self.option_add("*Font", (FONT, 10))
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("Tab.TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure("Status.TLabel", background=BG, foreground=ACCENT, font=(FONT, 9, "bold"))
        # 입력
        style.configure("TEntry", fieldbackground="white", bordercolor=BORDER, relief="flat", padding=4)
        style.configure("TCombobox", fieldbackground="white", bordercolor=BORDER, padding=4)
        # 카드형 LabelFrame
        style.configure("TLabelframe", background=BG, bordercolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=BG, foreground=SUBTLE,
                        font=(FONT, 10, "bold"))
        # 버튼 (기본=보조, Accent=주요)
        style.configure("TButton", background="#e5e7eb", foreground=TEXT,
                        bordercolor=BORDER, relief="flat", padding=(12, 7), font=(FONT, 10))
        style.map("TButton", background=[("active", "#d1d5db"), ("disabled", "#f0f1f3")],
                  foreground=[("disabled", "#9ca3af")])
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        relief="flat", padding=(14, 7), font=(FONT, 10, "bold"))
        style.map("Accent.TButton",
                  background=[("active", ACCENT_ACTIVE), ("disabled", "#a7ddd4")],
                  foreground=[("disabled", "#e6fffa")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        # 노트북 탭
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 6, 6, 0))
        style.configure("TNotebook.Tab", background="#e5e7eb", foreground=MUTED,
                        padding=(18, 9), font=(FONT, 10, "bold"), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", CARD)],
                  foreground=[("selected", ACCENT)])
        # 표(Treeview)
        style.configure("Treeview", background="white", fieldbackground="white",
                        foreground=TEXT, rowheight=29, font=(FONT, 10), borderwidth=0)
        style.configure("Treeview.Heading", background=HEADING_BG, foreground=HEADING_FG,
                        font=(FONT, 10, "bold"), relief="flat", padding=6)
        style.map("Treeview.Heading", background=[("active", "#cdeee6")])
        style.map("Treeview", background=[("selected", SELECT_BG)],
                  foreground=[("selected", "#0f3d36")])

    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=ACCENT, height=60)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        inner = tk.Frame(bar, bg=ACCENT)
        inner.pack(fill="both", expand=True, padx=18)
        tk.Label(inner, text="EcountERP 재고현황 · Wizfasta 가격비교", bg=ACCENT, fg="white",
                 font=(FONT, 15, "bold")).pack(side="left", pady=12)
        tk.Label(inner, text=f"v{APP_VERSION}", bg=ACCENT, fg=ACCENT_SOFT,
                 font=(FONT, 10)).pack(side="left", padx=10, pady=16)
        tk.Label(inner, text="설정 ▸ 인증 정보에서 키를 입력하세요", bg=ACCENT, fg=ACCENT_SOFT,
                 font=(FONT, 9)).pack(side="right", pady=18)

    # ================= 상단 메뉴 / 설정 =================
    def _build_menu(self) -> None:
        menubar = tk.Menu(self, background=ACCENT, foreground="white",
                          activebackground=ACCENT_ACTIVE, activeforeground="white")
        settings_menu = tk.Menu(menubar, tearoff=0, background="white", foreground=TEXT,
                                activebackground=ACCENT, activeforeground="white")
        settings_menu.add_command(label="인증 정보 설정...", command=self._open_settings)
        settings_menu.add_command(label="입고단가 캐시 비우기(갱신)", command=self._clear_price_cache)
        settings_menu.add_separator()
        settings_menu.add_command(label="종료", command=self.destroy)
        menubar.add_cascade(label="설정", menu=settings_menu)
        self.config(menu=menubar)

    def _open_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title("설정")
        win.transient(self)
        win.resizable(False, False)
        win.configure(bg=BG)
        pad = {"padx": 8, "pady": 5}

        auth = ttk.LabelFrame(win, text="인증 정보")
        auth.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(auth, text="회사코드").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(auth, textvariable=self.var_com, width=30).grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(auth, text="사용자ID").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(auth, textvariable=self.var_user, width=30).grid(row=1, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(auth, text="API 인증키").grid(row=2, column=0, sticky="e", **pad)
        self.ent_key = ttk.Entry(auth, textvariable=self.var_key, width=30, show="*")
        self.ent_key.grid(row=2, column=1, sticky="w", **pad)
        ttk.Checkbutton(auth, text="표시", variable=self.var_show_key,
                        command=self._toggle_key).grid(row=2, column=2, sticky="w", **pad)
        ttk.Label(auth, text="환경").grid(row=3, column=0, sticky="e", **pad)
        ttk.Combobox(auth, textvariable=self.var_env, width=28, state="readonly",
                     values=["production", "test"]).grid(row=3, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(auth, text="(production=운영 / test=상자테스트)", foreground="#666").grid(
            row=4, column=1, columnspan=2, sticky="w", padx=8)

        wiz = ttk.LabelFrame(win, text="Wizfasta 로그인 (가격비교용)")
        wiz.pack(fill="x", padx=12, pady=6)
        ttk.Label(wiz, text="업체코드").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(wiz, textvariable=self.var_wcorp, width=30).grid(row=0, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(wiz, text="아이디").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(wiz, textvariable=self.var_wid, width=30).grid(row=1, column=1, columnspan=2, sticky="w", **pad)
        ttk.Label(wiz, text="비밀번호").grid(row=2, column=0, sticky="e", **pad)
        self.ent_wpw = ttk.Entry(wiz, textvariable=self.var_wpw, width=30, show="*")
        self.ent_wpw.grid(row=2, column=1, sticky="w", **pad)
        ttk.Checkbutton(wiz, text="표시", variable=self.var_wshow,
                        command=self._toggle_wpw).grid(row=2, column=2, sticky="w", **pad)

        upd = ttk.LabelFrame(win, text="자동 업데이트")
        upd.pack(fill="x", padx=12, pady=6)
        ttk.Label(upd, text="GitHub 저장소").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(upd, textvariable=self.var_github, width=30).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(upd, text="(예: Claudio-sys11/Price-Check)", foreground="#666").grid(
            row=1, column=1, sticky="w", padx=8)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(6, 12))
        ttk.Button(btns, text="저장", style="Accent.TButton",
                   command=lambda: self._save_settings(win)).pack(side="right", padx=4)
        ttk.Button(btns, text="닫기", command=win.destroy).pack(side="right", padx=4)

        win.grab_set()

    def _save_settings(self, win: tk.Toplevel) -> None:
        self._save_config()
        win.destroy()

    def _clear_price_cache(self) -> None:
        save_price_cache({})
        messagebox.showinfo("입고단가 캐시", "입고단가 캐시를 비웠습니다.\n다음 조회 때 품목등록에서 다시 받아옵니다.")

    # ================= 자동 업데이트 =================
    def _start_update_check(self) -> None:
        cfg = {}
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    cfg = json.load(f) or {}
        except (OSError, json.JSONDecodeError):
            cfg = {}
        # github_repo 또는 update_url 중 하나라도 있으면 검사
        if not (cfg.get("github_repo") or cfg.get("update_url")):
            return
        threading.Thread(target=self._do_update_check, args=(cfg,), daemon=True).start()

    def _do_update_check(self, cfg: dict) -> None:
        manifest = updater.check(cfg)
        if manifest:
            self.after(0, self._prompt_update, manifest)

    def _prompt_update(self, manifest: dict) -> None:
        ver = manifest.get("version", "")
        notes = (manifest.get("notes", "") or "")[:300]
        dl = manifest.get("url", "")
        msg = f"새 버전 {ver} 이(가) 있습니다. (현재 {APP_VERSION})\n"
        if notes:
            msg += f"\n변경사항: {notes}\n"
        msg += "\n지금 업데이트하시겠습니까? (앱이 종료되고 설치가 진행됩니다)"
        if not dl:
            messagebox.showinfo("업데이트", msg + "\n\n(다운로드 URL이 매니페스트에 없습니다.)")
            return
        if messagebox.askyesno("업데이트 확인", msg):
            self._run_update(dl)

    def _run_update(self, url: str) -> None:
        self.status.set("업데이트 다운로드 중...")
        def worker():
            try:
                path = updater.download_installer(url)
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror("업데이트 실패", f"다운로드 오류:\n{exc}"))
                return
            def finish():
                updater.launch_installer(path)
                self.destroy()  # 앱 종료 → 설치파일이 교체 진행
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    # ================= 탭1: 재고현황 =================
    def _build_inventory_tab(self) -> None:
        pad = {"padx": 6, "pady": 5}
        root = self.tab_inv

        ttk.Label(root, style="Muted.TLabel",
                  text="인증 정보는 상단 [설정] 메뉴 → '인증 정보 설정…' 에서 입력하세요."
                  ).pack(fill="x", padx=16, pady=(12, 2))

        cond = ttk.LabelFrame(root, text=" 조회 조건  (선택 — 비우면 전체) ")
        cond.pack(fill="x", padx=16, pady=6, ipady=4)
        self.var_base_date = tk.StringVar()
        self.var_prod = tk.StringVar()
        self.var_wh = tk.StringVar()
        ttk.Label(cond, text="기준일자(YYYYMMDD)").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_base_date, width=16).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(cond, text="브랜드").grid(row=0, column=2, sticky="e", **pad)
        ent_brand = ttk.Entry(cond, textvariable=self.var_brand, width=18)
        ent_brand.grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(cond, text="모델명").grid(row=0, column=4, sticky="e", **pad)
        ent_model = ttk.Entry(cond, textvariable=self.var_model, width=18)
        ent_model.grid(row=0, column=5, sticky="w", **pad)
        # 조회 후 브랜드/모델명을 바꾸면 재조회 없이 즉시 재필터
        ent_brand.bind("<KeyRelease>", self._on_filter_change)
        ent_model.bind("<KeyRelease>", self._on_filter_change)
        ttk.Label(cond, text="품목코드").grid(row=1, column=0, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_prod, width=16).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(cond, text="창고코드").grid(row=1, column=2, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_wh, width=18).grid(row=1, column=3, sticky="w", **pad)
        ttk.Label(cond, style="Muted.TLabel",
                  text="※ 브랜드·모델명은 조회 결과를 부분일치로 필터링합니다. 결과는 브랜드 순으로 정렬됩니다.").grid(
                      row=2, column=0, columnspan=6, sticky="w", padx=8, pady=(2, 0))

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=16, pady=(2, 6))
        self.btn_query = ttk.Button(btns, text="🔍  재고현황 조회", style="Accent.TButton",
                                    command=self._on_query)
        self.btn_query.pack(side="left")
        self.btn_inv_csv = ttk.Button(btns, text="CSV 내보내기",
                                      command=lambda: export_rows_csv(self._inventory_display, "inventory.csv"),
                                      state="disabled")
        self.btn_inv_csv.pack(side="left", padx=8)
        self.btn_sub_csv = ttk.Button(btns, text="소계/평균만 내보내기",
                                      command=self._export_subtotals, state="disabled")
        self.btn_sub_csv.pack(side="left")
        self.status = tk.StringVar(value="대기 중")
        ttk.Label(btns, textvariable=self.status, style="Status.TLabel").pack(side="right")

        tf = tk.Frame(root, bg=BORDER)   # 1px 테두리 느낌의 카드
        tf.pack(fill="both", expand=True, padx=16, pady=(2, 14))
        self.tree_inv = ttk.Treeview(tf, show="headings")
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree_inv.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree_inv.xview)
        self.tree_inv.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree_inv.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _toggle_key(self) -> None:
        if self.ent_key is not None:
            self.ent_key.configure(show="" if self.var_show_key.get() else "*")

    def _toggle_wpw(self) -> None:
        if self.ent_wpw is not None:
            self.ent_wpw.configure(show="" if self.var_wshow.get() else "*")

    # ================= 탭2: 가격비교 =================
    def _build_compare_tab(self) -> None:
        root = self.tab_cmp
        self._wiz_rows: list[dict] = []

        ttk.Label(root, style="Muted.TLabel",
                  text="① 재고현황 탭에서 조회 후 → ② [Wizfasta 원가 가져오기]로 Chrome에서 상품DB 원가를 받아 "
                       "모델명으로 비교합니다. (Wizfasta 최초 1회 로그인 필요)"
                  ).pack(fill="x", padx=16, pady=(12, 2))

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=16, pady=(4, 6))
        self.btn_wiz = ttk.Button(btns, text="🛒  Wizfasta 원가 가져오기 (Chrome)",
                                  style="Accent.TButton", command=self._on_fetch_wizfasta)
        self.btn_wiz.pack(side="left")
        self.btn_cmp_csv = ttk.Button(btns, text="비교결과 CSV 내보내기",
                                      command=lambda: export_rows_csv(self._compare_rows, "price_compare.csv"),
                                      state="disabled")
        self.btn_cmp_csv.pack(side="left", padx=8)
        self.cmp_status = tk.StringVar(
            value="먼저 재고현황 탭에서 조회한 뒤, Wizfasta 원가 가져오기를 누르세요."
        )
        ttk.Label(btns, textvariable=self.cmp_status, style="Status.TLabel").pack(side="right")

        # 진행 체크리스트
        chk = ttk.LabelFrame(root, text=" 진행 상황 ")
        chk.pack(fill="x", padx=16, pady=(0, 6))
        self._step_labels = {}
        for key, label in WIZ_STEPS:
            lb = ttk.Label(chk, text=f"⬜  {label}", style="Muted.TLabel")
            lb.pack(anchor="w", padx=10, pady=1)
            self._step_labels[key] = (lb, label)

        tf = tk.Frame(root, bg=BORDER)
        tf.pack(fill="both", expand=True, padx=16, pady=(2, 14))
        self.tree_cmp = ttk.Treeview(tf, show="headings")
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree_cmp.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree_cmp.xview)
        self.tree_cmp.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree_cmp.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _reset_steps(self) -> None:
        for key, (lb, label) in self._step_labels.items():
            lb.configure(text=f"⬜  {label}", foreground=MUTED)

    def _set_step(self, key: str, detail: str = "") -> None:
        """체크리스트: key 이전 단계는 완료(✅), 현재 단계는 진행(⏳)으로 표시."""
        order = [k for k, _ in WIZ_STEPS]
        if key not in order:
            return
        idx = order.index(key)
        for i, (k, label) in enumerate(WIZ_STEPS):
            lb = self._step_labels[k][0]
            if i < idx:
                lb.configure(text=f"✅  {label}", foreground="#0d9488")
            elif i == idx:
                txt = f"⏳  {label}" + (f"  — {detail}" if detail else "")
                lb.configure(text=txt, foreground=ACCENT)
            else:
                lb.configure(text=f"⬜  {label}", foreground=MUTED)

    def _mark_all_done(self) -> None:
        for key, (lb, label) in self._step_labels.items():
            lb.configure(text=f"✅  {label}", foreground="#0d9488")

    def _on_fetch_wizfasta(self) -> None:
        if not getattr(self, "_inventory_display_all", []):
            messagebox.showwarning(
                "재고 먼저 조회",
                "먼저 ① 재고현황 탭에서 조회해 주세요.\n(EcountERP 모델명·입고단가가 있어야 비교됩니다.)")
            return
        corp, uid, pw = self.var_wcorp.get().strip(), self.var_wid.get().strip(), self.var_wpw.get()
        if not (corp and uid and pw):
            messagebox.showwarning(
                "Wizfasta 로그인 정보 필요",
                "[설정] → 'Wizfasta 로그인'에 업체코드·아이디·비밀번호를 입력해 주세요.")
            return
        self._reset_steps()
        self.btn_wiz.configure(state="disabled")
        self.btn_cmp_csv.configure(state="disabled")
        self.cmp_status.set("진행 중…")
        threading.Thread(target=self._do_fetch_wizfasta, args=(corp, uid, pw), daemon=True).start()

    def _do_fetch_wizfasta(self, corp: str, uid: str, pw: str) -> None:
        try:
            import wizfasta_selenium
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._wiz_failed(f"Selenium 모듈 로드 실패: {exc}"))
            return
        try:
            rows = wizfasta_selenium.fetch_wizfasta_costs(
                corp, uid, pw,
                progress=lambda key, detail="": self.after(0, lambda k=key, d=detail: self._set_step(k, d)),
                headless=True)
        except Exception as exc:  # noqa: BLE001
            self.after(0, lambda: self._wiz_failed(f"Wizfasta 가져오기 실패: {exc}"))
            return
        self.after(0, lambda: self._wiz_done(rows))

    def _wiz_failed(self, msg: str) -> None:
        self.btn_wiz.configure(state="normal")
        self.cmp_status.set("실패")
        messagebox.showerror("Wizfasta 가져오기 실패", msg)

    def _wiz_done(self, wiz_rows: list[dict]) -> None:
        self.btn_wiz.configure(state="normal")
        self._wiz_rows = wiz_rows or []
        if not self._wiz_rows:
            self.cmp_status.set("Wizfasta 원가 0건 — 로그인/조회 상태를 확인하세요.")
            messagebox.showwarning("데이터 없음", "Wizfasta에서 원가를 받지 못했습니다(0건).")
            return
        self._set_step("match")
        ecount_data = [d for d in self._inventory_display_all]  # 모델명·입고단가 포함
        self._compare_rows = cmp.build_cost_comparison(self._wiz_rows, ecount_data)
        fill_tree(self.tree_cmp, self._compare_rows)
        self.btn_cmp_csv.configure(state="normal")
        matched = sum(1 for r in self._compare_rows if r.get("매칭") == "O")
        self._mark_all_done()
        self.cmp_status.set(
            f"완료 — Wiz {len(self._wiz_rows)}건 / 모델명 매칭 {matched} / 미매칭 {len(self._compare_rows) - matched}")

    # ================= 설정 =================
    def _load_config(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        self.var_com.set(cfg.get("COM_CODE", ""))
        self.var_user.set(cfg.get("USER_ID", ""))
        self.var_key.set(cfg.get("API_CERT_KEY", ""))
        self.var_env.set(cfg.get("ENV", "production"))
        self._update_url = cfg.get("update_url", "")
        self.var_github.set(cfg.get("github_repo", ""))
        wz = cfg.get("wizfasta") or {}
        self.var_wcorp.set(wz.get("corp", ""))
        self.var_wid.set(wz.get("id", ""))
        self.var_wpw.set(wz.get("pw", ""))
        payload = (cfg.get("inventory") or {}).get("payload", {})
        self.var_base_date.set(payload.get("BASE_DATE", ""))
        self.var_prod.set(payload.get("PROD_CD", ""))
        self.var_wh.set(payload.get("WH_CD", ""))

    def _current_config(self) -> dict:
        return {
            "COM_CODE": self.var_com.get().strip(),
            "USER_ID": self.var_user.get().strip(),
            "API_CERT_KEY": self.var_key.get().strip(),
            "LAN_TYPE": "ko-KR",
            "ENV": self.var_env.get(),
            "update_url": getattr(self, "_update_url", ""),
            "github_repo": self.var_github.get().strip(),
            "wizfasta": {
                "corp": self.var_wcorp.get().strip(),
                "id": self.var_wid.get().strip(),
                "pw": self.var_wpw.get(),
            },
            "inventory": {
                "endpoint": "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus",
                "payload": {
                    "BASE_DATE": self.var_base_date.get().strip(),
                    "PROD_CD": self.var_prod.get().strip(),
                    "WH_CD": self.var_wh.get().strip(),
                },
            },
        }

    def _save_config(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._current_config(), f, ensure_ascii=False, indent=2)
        except OSError as exc:
            messagebox.showerror("저장 실패", f"설정 저장 중 오류:\n{exc}")
            return
        messagebox.showinfo("저장 완료", f"설정을 저장했습니다.\n{CONFIG_PATH}")

    # ================= 조회 실행 =================
    def _on_query(self) -> None:
        cfg = self._current_config()
        if not cfg["COM_CODE"] or not cfg["USER_ID"] or not cfg["API_CERT_KEY"]:
            messagebox.showwarning("입력 필요", "회사코드 / 사용자ID / API 인증키를 모두 입력하세요.")
            return
        self._query_seq = getattr(self, "_query_seq", 0) + 1  # 새 조회 → 이전 백그라운드 가격조회 중단
        self.btn_query.configure(state="disabled")
        self.btn_inv_csv.configure(state="disabled")
        self.status.set("조회 중...")
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        threading.Thread(target=self._do_query, args=(cfg, self._query_seq), daemon=True).start()

    # 재고현황 엔드포인트: 풍부한 ByLocation(창고/품목명/규격 포함) 우선, 권한 없으면 기본으로 폴백
    RICH_EP = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatusByLocation"
    BASIC_EP = "/OAPI/V2/InventoryBalance/GetListInventoryBalanceStatus"

    def _do_query(self, cfg: dict, my_seq: int = 0) -> None:
        from datetime import date
        try:
            client = EcountClient(
                com_code=cfg["COM_CODE"], user_id=cfg["USER_ID"],
                api_cert_key=cfg["API_CERT_KEY"], lan_type=cfg.get("LAN_TYPE", "ko-KR"),
                env=cfg.get("ENV", "production"),
            )
            payload = {k: v for k, v in cfg["inventory"]["payload"].items() if v}
            if not payload.get("BASE_DATE"):
                payload["BASE_DATE"] = date.today().strftime("%Y%m%d")
            client.get_zone()
            client.login()  # 한 번만 로그인 (이후 엔드포인트 재시도에 세션 재사용)
        except EcountApiError as exc:
            self.after(0, self._query_failed, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.after(0, self._query_failed, f"예기치 못한 오류: {exc}")
            return

        note = ""
        try:
            # 1순위: 창고별 재고현황 (창고코드/창고명/품목명/재고 포함)
            data = client.get_inventory(endpoint=self.RICH_EP, payload=payload)
            rows = cmp.extract_ecount_rows(data)
        except EcountApiError as exc:
            # 권한 없으면 기본 재고현황으로 대체
            note = f"창고별 재고(ByLocation) 사용 불가 → 기본 재고로 대체. 사유: {exc}"
            try:
                data = client.get_inventory(endpoint=self.BASIC_EP, payload=payload)
                rows = cmp.extract_ecount_rows(data)
            except EcountApiError as exc2:
                self.after(0, self._query_failed, str(exc2))
                return

        # 입고단가: 품목코드별로 품목등록(IN_PRICE)을 개별 조회해 매칭(캐시 활용)
        code_f = cmp.detect_ecount_fields(rows).get("품목코드") or "PROD_CD"
        inv_codes = list(dict.fromkeys(
            str(r.get(code_f, "")).strip() for r in rows if str(r.get(code_f, "")).strip()))
        cache = load_price_cache()

        # 1) 우선 캐시에 있는 입고단가로 즉시 표시
        price_map = {c: cache[c] for c in inv_codes if c in cache}
        all_display = cmp.build_inventory_display(rows, price_map=price_map)
        self.after(0, self._query_done, data, rows, all_display, note)

        # 2) 누락된 품목코드 입고단가를 백그라운드로 받아 채우고 자동 갱신
        missing = [c for c in inv_codes if c not in cache]
        if missing:
            def prog(d, t):
                self.after(0, lambda d=d, t=t: self.status.set(
                    f"입고단가 매칭 중… {d}/{t} (완료 후 자동 갱신)"))
            try:
                fetched = client.get_prices(
                    missing, progress=prog,
                    should_stop=lambda: getattr(self, "_query_seq", 0) != my_seq)
            except EcountApiError:
                fetched = {}
            if fetched and getattr(self, "_query_seq", 0) == my_seq:
                cache.update(fetched)
                save_price_cache(cache)
                full_map = {c: cache[c] for c in inv_codes if c in cache}
                new_all = cmp.build_inventory_display(rows, price_map=full_map)
                self.after(0, self._prices_updated, rows, new_all, len(fetched))

    def _query_failed(self, msg: str) -> None:
        self.btn_query.configure(state="normal")
        self.status.set("실패")
        messagebox.showerror("조회 실패", msg)

    def _query_done(self, data: dict, rows: list[dict],
                    all_display: list[dict], product_note: str = "") -> None:
        self._inventory_raw = data
        self._inventory_rows = rows                  # 원본(가격비교 탭에서 사용)
        self._inventory_display_all = all_display    # 필터 전 전체(재필터용)
        self.btn_query.configure(state="normal")
        if not rows:
            self._inventory_display = []
            fill_tree(self.tree_inv, [])
            self.status.set("완료 — 표시할 행 없음")
            self.btn_inv_csv.configure(state="normal")
            messagebox.showinfo(
                "조회 완료",
                "응답을 받았지만 재고 행을 자동으로 찾지 못했습니다.\n"
                "'CSV 내보내기'로 원문 구조를 확인하세요.",
            )
            return

        # 연동 항목 안내(상태바 접두)
        has_name = any(d.get("브랜드") for d in all_display)
        has_price = any(
            str(d.get("입고단가", "")).replace(",", "").lstrip("-") not in ("", "0")
            for d in all_display
        )
        has_wh = any("창고코드" in d for d in all_display)
        if has_name or has_price:
            parts = []
            if has_name:
                parts.append("품목명 분해" + (" + 창고별" if has_wh else ""))
            if has_price:
                parts.append("입고단가/총단가")
            self._inv_status_suffix = f"({', '.join(parts)} 적용, 소계 포함)"
        else:
            self._inv_status_suffix = "(품목명/입고단가 미적용: 창고별/품목 조회 API 권한 필요)"
            if product_note:
                messagebox.showwarning(
                    "재고 항목 일부 미연동",
                    "재고수량은 조회됐지만 브랜드/모델명/사이즈/입고일자·창고 정보를 채울 "
                    "품목명을 가져오지 못했습니다.\n\n"
                    f"사유: {product_note}\n\n"
                    "EcountERP에서 '창고별 재고현황(GetListInventoryBalanceStatusByLocation)' "
                    "또는 '품목 조회(GetBasicProductsList)' API 권한을 켜면 다음 조회부터 "
                    "자동으로 모든 항목이 채워집니다.",
                )

        self.btn_inv_csv.configure(state="normal")
        self.btn_sub_csv.configure(state="normal")
        self._render_inventory(initial=True)

    def _render_inventory(self, initial: bool = False) -> None:
        """이미 받아온 전체 데이터에 현재 브랜드/모델명 필터 + 브랜드 정렬 + 소계를 적용해 표시."""
        allrows = getattr(self, "_inventory_display_all", [])
        bf = self.var_brand.get().strip().lower()
        mf = self.var_model.get().strip().lower()
        filtered = [
            d for d in allrows
            if (not bf or bf in str(d.get("브랜드", "")).lower())
            and (not mf or mf in str(d.get("모델명", "")).lower())
        ]
        if self._sort_col:   # 헤더 클릭 정렬(오름/내림)
            col = self._sort_col
            if col == "사이즈":
                keyf = lambda d: _size_key(d.get("사이즈", ""))
            else:
                keyf = lambda d: _sort_key(d.get(col, ""))
            filtered.sort(key=keyf, reverse=self._sort_desc)
        else:                # 기본 정렬: 브랜드 → 모델명 → 사이즈(S,M,L/숫자) → 입고일자
            filtered.sort(key=lambda d: (
                str(d.get("브랜드", "")),
                str(d.get("모델명", "")),
                _size_key(d.get("사이즈", "")),
                str(d.get("입고일자", "")),
                str(d.get("품목코드", "")),
                str(d.get("창고코드", "")),
            ))
        self._inventory_display = cmp.add_subtotals(filtered)
        fill_tree(self.tree_inv, self._inventory_display,
                  sort_callback=self._on_sort_column,
                  sort_col=self._sort_col, sort_desc=self._sort_desc)

        total = len(allrows)
        shown = len(filtered)
        suffix = getattr(self, "_inv_status_suffix", "")
        if shown == total:
            self.status.set(f"완료 — {total}건 {suffix}".rstrip())
        else:
            self.status.set(f"필터 {shown}건 / 전체 {total}건 {suffix}".rstrip())

    def _on_filter_change(self, event=None) -> None:
        """조회조건(브랜드/모델명) 변경 시: 이미 받아온 데이터에서 즉시 재필터(재조회 없음)."""
        if getattr(self, "_inventory_display_all", []):
            self._render_inventory()

    def _on_sort_column(self, col: str) -> None:
        """열 머리글 클릭: 같은 열이면 오름↔내림 토글, 다른 열이면 그 열 오름차순."""
        if not getattr(self, "_inventory_display_all", []):
            return
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        self._render_inventory()

    def _export_subtotals(self) -> None:
        """현재 표의 소계/평균 행만 (브랜드·모델명 포함) CSV 로 내보낸다."""
        subs = [d for d in self._inventory_display if d.get("_subtotal")]
        if not subs:
            messagebox.showwarning("데이터 없음", "내보낼 소계가 없습니다. 먼저 재고현황을 조회하세요.")
            return
        rows = [{
            "브랜드": d.get("브랜드", ""),
            "모델명": d.get("모델명", ""),
            "재고수량": d.get("재고수량", ""),
            "평균단가": d.get("입고단가", ""),
            "총단가": d.get("총단가", ""),
        } for d in subs]
        export_rows_csv(rows, "subtotals.csv")

    def _prices_updated(self, rows: list[dict], new_all: list[dict], n_fetched: int) -> None:
        """백그라운드 입고단가 매칭 완료 → 전체 데이터 교체 후 현재 필터/정렬로 재표시."""
        if rows is not self._inventory_rows:   # 그 사이 새 조회가 있었으면 무시
            return
        self._inventory_display_all = new_all
        self._render_inventory()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
