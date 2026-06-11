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
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from ecount_api import EcountClient, EcountApiError
import compare as cmp
import updater
from version import APP_VERSION

APP_NAME = "EcountInventory"


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


CONFIG_PATH = os.path.join(app_data_dir(), "config.json")


def fill_tree(tree: ttk.Treeview, rows: list[dict]) -> None:
    tree.delete(*tree.get_children())
    if not rows:
        tree["columns"] = ()
        return
    headers: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in headers:
                headers.append(k)
    tree["columns"] = headers
    for h in headers:
        tree.heading(h, text=h, anchor="center")
        tree.column(h, width=120, anchor="center", stretch=False)
    for r in rows:
        tree.insert("", "end", values=[r.get(h, "") for h in headers])


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
            if k not in headers:
                headers.append(k)
    try:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=headers)
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
        self.geometry("1000x680")
        self.minsize(860, 560)

        self._inventory_rows: list[dict] = []   # 탭1 조회 결과(원본: 가격비교 탭에서 사용)
        self._inventory_display: list[dict] = []  # 파싱 컬럼 포함 표시용
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
        self.ent_key: ttk.Entry | None = None

        self._build_menu()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self.tab_inv = ttk.Frame(nb)
        self.tab_cmp = ttk.Frame(nb)
        nb.add(self.tab_inv, text="① 재고현황 조회")
        nb.add(self.tab_cmp, text="② 가격비교")

        self._build_inventory_tab()
        self._build_compare_tab()
        self._load_config()

        # 실행 시 백그라운드로 업데이트 확인
        self.after(800, self._start_update_check)

    # ================= 상단 메뉴 / 설정 =================
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="인증 정보 설정...", command=self._open_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="종료", command=self.destroy)
        menubar.add_cascade(label="설정", menu=settings_menu)
        self.config(menu=menubar)

    def _open_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title("설정")
        win.transient(self)
        win.resizable(False, False)
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

        upd = ttk.LabelFrame(win, text="자동 업데이트")
        upd.pack(fill="x", padx=12, pady=6)
        ttk.Label(upd, text="GitHub 저장소").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(upd, textvariable=self.var_github, width=30).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(upd, text="(예: Claudio-sys11/Price-Check)", foreground="#666").grid(
            row=1, column=1, sticky="w", padx=8)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(6, 12))
        ttk.Button(btns, text="저장", command=lambda: self._save_settings(win)).pack(side="right", padx=4)
        ttk.Button(btns, text="닫기", command=win.destroy).pack(side="right", padx=4)

        win.grab_set()

    def _save_settings(self, win: tk.Toplevel) -> None:
        self._save_config()
        win.destroy()

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
        pad = {"padx": 6, "pady": 4}
        root = self.tab_inv

        # 안내: 인증정보는 상단 [설정] 메뉴에서 입력 (메인 화면에는 표시하지 않음)
        info = ttk.Frame(root)
        info.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Label(info, text="인증 정보는 상단 [설정] 메뉴 → '인증 정보 설정...' 에서 입력하세요.",
                  foreground="#666").pack(side="left")

        cond = ttk.LabelFrame(root, text="조회 조건 (선택 — 비우면 전체)")
        cond.pack(fill="x", padx=10, pady=4)
        self.var_base_date = tk.StringVar()
        self.var_prod = tk.StringVar()
        self.var_wh = tk.StringVar()
        ttk.Label(cond, text="기준일자(YYYYMMDD)").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_base_date, width=16).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(cond, text="품목코드").grid(row=0, column=2, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_prod, width=16).grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(cond, text="창고코드").grid(row=0, column=4, sticky="e", **pad)
        ttk.Entry(cond, textvariable=self.var_wh, width=16).grid(row=0, column=5, sticky="w", **pad)

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=10, pady=4)
        self.btn_query = ttk.Button(btns, text="재고현황 조회", command=self._on_query)
        self.btn_query.pack(side="left", padx=4)
        self.btn_inv_csv = ttk.Button(btns, text="CSV 내보내기",
                                      command=lambda: export_rows_csv(self._inventory_display, "inventory.csv"),
                                      state="disabled")
        self.btn_inv_csv.pack(side="left", padx=4)
        self.status = tk.StringVar(value="대기 중")
        ttk.Label(btns, textvariable=self.status, foreground="#0a58ca").pack(side="right", padx=8)

        tf = ttk.Frame(root)
        tf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.tree_inv = ttk.Treeview(tf, show="headings")
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree_inv.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree_inv.xview)
        self.tree_inv.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree_inv.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _toggle_key(self) -> None:
        if self.ent_key is not None:
            self.ent_key.configure(show="" if self.var_show_key.get() else "*")

    # ================= 탭2: 가격비교 =================
    def _build_compare_tab(self) -> None:
        pad = {"padx": 6, "pady": 4}
        root = self.tab_cmp

        top = ttk.LabelFrame(root, text="Wizfasta 판매상품 데이터")
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.var_wiz_path = tk.StringVar(value=self._default_wiz_path())
        ttk.Label(top, text="JSON 파일").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(top, textvariable=self.var_wiz_path, width=70).grid(row=0, column=1, sticky="w", **pad)
        ttk.Button(top, text="찾아보기", command=self._browse_wiz).grid(row=0, column=2, **pad)
        ttk.Label(
            top,
            text="※ Wizfasta [상품관리>판매상품등록]에서 wizfasta_extract.js 로 추출한 파일을 지정하세요.",
            foreground="#666",
        ).grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        btns = ttk.Frame(root)
        btns.pack(fill="x", padx=10, pady=4)
        ttk.Button(btns, text="가격비교 실행", command=self._on_compare).pack(side="left", padx=4)
        self.btn_cmp_csv = ttk.Button(btns, text="비교결과 CSV 내보내기",
                                      command=lambda: export_rows_csv(self._compare_rows, "price_compare.csv"),
                                      state="disabled")
        self.btn_cmp_csv.pack(side="left", padx=4)
        self.cmp_status = tk.StringVar(
            value="먼저 ①탭에서 재고현황을 조회한 뒤, Wizfasta JSON 을 지정하고 실행하세요."
        )
        ttk.Label(btns, textvariable=self.cmp_status, foreground="#0a58ca").pack(side="right", padx=8)

        tf = ttk.Frame(root)
        tf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.tree_cmp = ttk.Treeview(tf, show="headings")
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree_cmp.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree_cmp.xview)
        self.tree_cmp.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree_cmp.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

    def _default_wiz_path(self) -> str:
        for p in ("data/wizfasta_products.json",
                  os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wizfasta_products.json")):
            if os.path.exists(p):
                return os.path.abspath(p)
        return ""

    def _browse_wiz(self) -> None:
        path = filedialog.askopenfilename(
            title="Wizfasta JSON 선택",
            filetypes=[("JSON 파일", "*.json"), ("모든 파일", "*.*")],
        )
        if path:
            self.var_wiz_path.set(path)

    def _on_compare(self) -> None:
        if not self._inventory_rows:
            if not messagebox.askyesno(
                "재고 데이터 없음",
                "①탭에서 재고현황을 먼저 조회하지 않았습니다.\n"
                "EcountERP 재고 없이 (미매칭으로) 비교를 진행할까요?",
            ):
                return
        wiz_path = self.var_wiz_path.get().strip()
        if not wiz_path or not os.path.exists(wiz_path):
            messagebox.showwarning("파일 필요", "유효한 Wizfasta JSON 파일을 지정하세요.")
            return
        try:
            summary = cmp.compare(
                wizfasta_path=wiz_path,
                ecount_rows=self._inventory_rows,
                ecount_raw_path=None,
                out_csv=os.path.join(app_data_dir(), "price_compare.csv"),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("비교 실패", str(exc))
            return

        self._compare_rows = summary["rows"]
        fill_tree(self.tree_cmp, self._compare_rows)
        self.btn_cmp_csv.configure(state="normal")
        self.cmp_status.set(
            f"완료 — Wiz {summary['wizfasta_count']}건 / 매칭 {summary['matched']} / "
            f"미매칭 {summary['unmatched']} (필드: {summary['ecount_fields']})"
        )

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
        self.btn_query.configure(state="disabled")
        self.btn_inv_csv.configure(state="disabled")
        self.status.set("조회 중...")
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        threading.Thread(target=self._do_query, args=(cfg,), daemon=True).start()

    def _do_query(self, cfg: dict) -> None:
        try:
            client = EcountClient(
                com_code=cfg["COM_CODE"], user_id=cfg["USER_ID"],
                api_cert_key=cfg["API_CERT_KEY"], lan_type=cfg.get("LAN_TYPE", "ko-KR"),
                env=cfg.get("ENV", "production"),
            )
            inv = cfg["inventory"]
            payload = {k: v for k, v in inv["payload"].items() if v}
            # GetListInventoryBalanceStatus 는 BASE_DATE 가 필수 → 비어 있으면 오늘 날짜 사용
            if not payload.get("BASE_DATE"):
                from datetime import date
                payload["BASE_DATE"] = date.today().strftime("%Y%m%d")
            data = client.fetch_inventory(endpoint=inv["endpoint"], payload=payload)
        except EcountApiError as exc:
            self.after(0, self._query_failed, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.after(0, self._query_failed, f"예기치 못한 오류: {exc}")
            return

        rows = cmp.extract_ecount_rows(data)

        # 품목명(브랜드_상품코드_사이즈_입고일자) 분해를 위해 품목 마스터도 조회 시도
        product_rows: list[dict] = []
        product_note = ""
        try:
            pdata = client.get_products()
            product_rows = cmp.extract_ecount_rows(pdata)
        except EcountApiError as exc:
            product_note = str(exc)
        except Exception as exc:  # noqa: BLE001
            product_note = f"품목 조회 오류: {exc}"

        display = cmp.build_inventory_display(rows, product_rows)
        self.after(0, self._query_done, data, rows, display, product_note)

    def _query_failed(self, msg: str) -> None:
        self.btn_query.configure(state="normal")
        self.status.set("실패")
        messagebox.showerror("조회 실패", msg)

    def _query_done(self, data: dict, rows: list[dict],
                    display: list[dict], product_note: str = "") -> None:
        self._inventory_raw = data
        self._inventory_rows = rows            # 원본(가격비교 탭에서 사용)
        self._inventory_display = display      # 파싱 컬럼 포함(표/CSV 표시용)
        self.btn_query.configure(state="normal")
        fill_tree(self.tree_inv, display)
        if not rows:
            self.status.set("완료 — 표시할 행 없음")
            self.btn_inv_csv.configure(state="normal")
            messagebox.showinfo(
                "조회 완료",
                "응답을 받았지만 재고 행을 자동으로 찾지 못했습니다.\n"
                "'CSV 내보내기'로 원문 구조를 확인하세요.",
            )
            return

        # 품목명을 가져왔는지(=브랜드/상품코드/사이즈/입고일자 채워졌는지) 확인
        has_name = any(d.get("품목명") for d in display)
        self.btn_inv_csv.configure(state="normal")
        if has_name:
            self.status.set(f"완료 — {len(rows)}건 (품목명 분해 적용)")
        else:
            self.status.set(f"완료 — {len(rows)}건 (품목명 미적용: 품목 조회 API 권한 필요)")
            if product_note:
                messagebox.showwarning(
                    "품목명 분해 불가",
                    "재고수량은 조회됐지만 브랜드/상품코드/사이즈/입고일자 컬럼을 채울 "
                    "품목명을 가져오지 못했습니다.\n\n"
                    f"사유: {product_note}\n\n"
                    "EcountERP에서 '품목 조회(GetBasicProductsList)' API 권한을 켜면 "
                    "다음 조회부터 자동으로 분해됩니다.",
                )


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
