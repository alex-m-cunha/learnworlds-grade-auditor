#!/usr/bin/env python3
"""GUI para o pipeline de auditoria LearnWorlds.

Duplo-clique para abrir. Sem terminal necessário.

Aba 1 — Nova Run : pipeline completo end-to-end
Aba 2 — Re-correr: reconciliar / interpretar sobre run existente
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

# ── Project root (same dir as this file) ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent

# ── tkinter ───────────────────────────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("ERRO: tkinter não disponível neste Python. Instala o Python da python.org.")
    sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[\s/\\]+", "-", text.strip())
    text = re.sub(r"[^\w\-]+", "", text)
    text = re.sub(r"-+", "-", text).strip("-").lower()
    return text or "untitled"


def _load_cfg() -> dict[str, str]:
    cfg: dict[str, str] = {}
    path = PROJECT_ROOT / "assessment.cfg"
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cfg[k.strip()] = v.strip()
    return cfg


def _save_cfg(program: str, label: str, label_display: str, assessment_id: str, epoca: str) -> None:
    path = PROJECT_ROOT / "assessment.cfg"
    text = path.read_text(encoding="utf-8") if path.exists() else ""

    def _set(text: str, key: str, value: str) -> str:
        pattern = rf"^({re.escape(key)}\s*=).*$"
        new_text, n = re.subn(pattern, rf"\g<1>{value}", text, flags=re.MULTILINE)
        if n == 0:
            new_text = text.rstrip("\n") + f"\n{key}={value}\n"
        return new_text

    text = _set(text, "PROGRAM", program)
    text = _set(text, "LABEL", label)
    text = _set(text, "LABEL_DISPLAY", label_display)
    text = _set(text, "ASSESSMENT_ID", assessment_id)
    text = _set(text, "EPOCA", epoca)
    path.write_text(text, encoding="utf-8")


def _set_run_meta_key(run_dir: Path, key: str, value: str) -> None:
    meta_path = run_dir / "run_meta.cfg"
    text = meta_path.read_text(encoding="utf-8") if meta_path.exists() else ""
    pattern = rf"^({re.escape(key)}\s*=).*$"
    new_text, n = re.subn(pattern, rf"\g<1>{value}", text, flags=re.MULTILINE)
    if n == 0:
        new_text = text.rstrip("\n") + f"\n{key}={value}\n"
    meta_path.write_text(new_text, encoding="utf-8")


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


# ── Subprocess streaming ───────────────────────────────────────────────────────

def _run_step(
    cmd: list[str],
    log: "tk.Text",
    on_done: "callable[[int], None]",
) -> None:
    def _append(line: str) -> None:
        log.configure(state="normal")
        log.insert("end", line)
        log.see("end")
        log.configure(state="disabled")

    def _worker() -> None:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(PROJECT_ROOT),
            )
            for line in proc.stdout:
                log.after(0, lambda l=line: _append(l))
            proc.wait()
            log.after(0, lambda: on_done(proc.returncode))
        except Exception as exc:
            log.after(0, lambda: _append(f"\nERRO ao arrancar processo: {exc}\n"))
            log.after(0, lambda: on_done(1))

    threading.Thread(target=_worker, daemon=True).start()


def _log_write(log: "tk.Text", text: str) -> None:
    log.configure(state="normal")
    log.insert("end", text)
    log.see("end")
    log.configure(state="disabled")


def _log_clear(log: "tk.Text") -> None:
    log.configure(state="normal")
    log.delete("1.0", "end")
    log.configure(state="disabled")


# ── Nova Run tab ──────────────────────────────────────────────────────────────

class NovaRunTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, log: "tk.Text") -> None:
        super().__init__(parent, padding=12)
        self._log = log
        self._run_dir: Path | None = None

        cfg = _load_cfg()

        # ── Form ──────────────────────────────────────────────────────────────
        form = ttk.LabelFrame(self, text="Configuração da run", padding=10)
        form.pack(fill="x", pady=(0, 10))
        form.columnconfigure(1, weight=1)

        labels = [
            ("Sigla do Programa:", "programa", cfg.get("PROGRAM", "").upper()),
            ("Unidade Curricular:", "uc", cfg.get("LABEL_DISPLAY", "")),
            ("Assessment ID:", "aid", cfg.get("ASSESSMENT_ID", "")),
        ]
        hints = [
            "ex: PGGF2",
            "ex: UC1 Gestão de Projetos e Estratégia de Dados",
            "Últimos 24 caracteres da URL da atividade no LearnWorlds",
        ]
        self._vars: dict[str, tk.StringVar] = {}
        for row_idx, (lbl, key, default) in enumerate(labels):
            ttk.Label(form, text=lbl).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value=default)
            self._vars[key] = var
            entry = ttk.Entry(form, textvariable=var, width=55)
            entry.grid(row=row_idx, column=1, sticky="ew", pady=3)
            ttk.Label(
                form,
                text=hints[row_idx],
                foreground="grey",
                font=("TkDefaultFont", 9),
            ).grid(row=row_idx, column=2, sticky="w", padx=(6, 0))

        # Época dropdown
        ttk.Label(form, text="Época:").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=3)
        self._vars["epoca"] = tk.StringVar(value=cfg.get("EPOCA", "Normal"))
        epoca_menu = ttk.OptionMenu(form, self._vars["epoca"], self._vars["epoca"].get(), "Normal", "Extraordinária")
        epoca_menu.grid(row=3, column=1, sticky="w", pady=3)

        # ── Steps ─────────────────────────────────────────────────────────────
        steps = ttk.LabelFrame(self, text="Passos do pipeline", padding=10)
        steps.pack(fill="x", pady=(0, 10))

        self._btn_xlsx = ttk.Button(
            steps,
            text="1 — Selecionar Gabarito LW (XLSX) + Extrair submissões",
            command=self._step1_xlsx,
        )
        self._btn_xlsx.pack(fill="x", pady=3)

        word_frame = ttk.Frame(steps)
        word_frame.pack(fill="x", pady=3)
        self._btn_word = ttk.Button(
            word_frame,
            text="2 — Selecionar ficheiros Word (gabarito ID / docente)",
            command=self._step2_word,
            state="disabled",
        )
        self._btn_word.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._btn_skip_word = ttk.Button(
            word_frame,
            text="Ignorar",
            command=self._step2_skip,
            state="disabled",
            width=10,
        )
        self._btn_skip_word.pack(side="left")

        self._btn_reconcile = ttk.Button(
            steps,
            text="3 — Reconciliar",
            command=self._step3_reconcile,
            state="disabled",
        )
        self._btn_reconcile.pack(fill="x", pady=3)

        self._btn_interpret = ttk.Button(
            steps,
            text="4 — Interpretar (IA)",
            command=self._step4_interpret,
            state="disabled",
        )
        self._btn_interpret.pack(fill="x", pady=3)

        self._status_var = tk.StringVar(value="Pronto.")
        ttk.Label(self, textvariable=self._status_var, foreground="grey").pack(anchor="w")

    # ── Step helpers ──────────────────────────────────────────────────────────

    def _fields(self) -> tuple[str, str, str, str, str]:
        programa = _slugify(self._vars["programa"].get().strip())
        label_display = self._vars["uc"].get().strip()
        label = _slugify(label_display)
        aid = self._vars["aid"].get().strip()
        epoca = self._vars["epoca"].get().strip()
        return programa, label, label_display, aid, epoca

    def _set_status(self, msg: str, colour: str = "grey") -> None:
        self._status_var.set(msg)

    def _disable_all(self) -> None:
        for btn in (self._btn_xlsx, self._btn_word, self._btn_skip_word,
                    self._btn_reconcile, self._btn_interpret):
            btn.configure(state="disabled")

    # ── Step 1 ────────────────────────────────────────────────────────────────

    def _step1_xlsx(self) -> None:
        programa, label, label_display, aid, epoca = self._fields()
        if not programa or not label_display or not aid:
            messagebox.showerror("Campos obrigatórios", "Preenche Programa, Unidade Curricular e Assessment ID.")
            return

        xlsx = filedialog.askopenfilename(
            title="Selecionar Gabarito LW (XLSX exportado do LearnWorlds)",
            filetypes=[("Excel files", "*.xlsx *.xls"), ("All files", "*.*")],
            initialdir=str(PROJECT_ROOT / "input" / programa / "exam_configs"),
        )
        if not xlsx:
            return

        # Save config
        _save_cfg(programa, label, label_display, aid, epoca)

        # Create run dir
        run_ts = _timestamp()
        run_dir = PROJECT_ROOT / "output" / programa / label / run_ts
        run_dir.mkdir(parents=True, exist_ok=True)
        self._run_dir = run_dir

        # Copy XLSX to input
        dest_dir = PROJECT_ROOT / "input" / programa / "exam_configs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_xlsx = dest_dir / f"{label}_exam_config.xlsx"
        shutil.copy2(xlsx, dest_xlsx)

        _log_clear(self._log)
        _log_write(self._log, f"Run folder: {run_dir}\n\n>> [1a] A extrair submissões (API)...\n")
        self._disable_all()
        self._set_status("A extrair submissões...")

        cmd_extract = [
            sys.executable, "-m", "extractor.run_extract",
            "--assessment-id", aid,
            "--label", label,
            "--run-dir", str(run_dir),
        ]

        def _after_extract(rc: int) -> None:
            if rc != 0:
                _log_write(self._log, "\nERRO na extracção de submissões.\n")
                self._btn_xlsx.configure(state="normal")
                self._set_status("Erro na extracção.")
                return
            _log_write(self._log, "\n>> [1b] A importar gabarito LearnWorlds...\n")
            cmd_exam = [
                sys.executable, "-m", "extractor.run_exam_config",
                "--xlsx", str(dest_xlsx),
                "--assessment-id", aid,
                "--label", label,
                "--run-dir", str(run_dir),
            ]
            _run_step(cmd_exam, self._log, _after_exam_config)

        def _after_exam_config(rc: int) -> None:
            if rc != 0:
                _log_write(self._log, "\nERRO no import do gabarito LW.\n")
                self._btn_xlsx.configure(state="normal")
                self._set_status("Erro no gabarito LW.")
                return
            _log_write(self._log, "\nPasso 1 concluído. Seleciona os ficheiros Word ou ignora.\n")
            self._btn_word.configure(state="normal")
            self._btn_skip_word.configure(state="normal")
            self._set_status("Passo 1 OK — seleciona Word docs ou ignora.")

        _run_step(cmd_extract, self._log, _after_extract)

    # ── Step 2 ────────────────────────────────────────────────────────────────

    def _step2_word(self) -> None:
        programa, label, _, _, _ = self._fields()
        docs = filedialog.askopenfilenames(
            title="Selecionar ficheiros Word (gabarito docente — .docx)",
            filetypes=[("Word documents", "*.docx"), ("All files", "*.*")],
            initialdir=str(PROJECT_ROOT / "input" / programa / "word_docs"),
        )
        if not docs:
            return

        dest_dir = PROJECT_ROOT / "input" / programa / "word_docs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_docs: list[str] = []
        for doc in docs:
            stem = Path(doc).stem
            dest = dest_dir / f"{label}_{stem}.docx"
            shutil.copy2(doc, dest)
            dest_docs.append(str(dest))

        _log_write(self._log, f"\n>> [2] A extrair respostas de {len(dest_docs)} ficheiro(s) Word...\n")
        self._btn_word.configure(state="disabled")
        self._btn_skip_word.configure(state="disabled")
        self._set_status("A extrair gabarito Word (IA)...")

        cmd = [
            sys.executable, "tools/extract_answer_key.py",
            "--run-dir", str(self._run_dir),
            "--docs", *dest_docs,
        ]

        def _after(rc: int) -> None:
            if rc != 0:
                _log_write(self._log, "\nERRO na extracção do gabarito Word.\n")
                self._btn_word.configure(state="normal")
                self._btn_skip_word.configure(state="normal")
                self._set_status("Erro no gabarito Word.")
                return
            _log_write(self._log, "\nPasso 2 concluído.\n")
            self._btn_reconcile.configure(state="normal")
            self._set_status("Passo 2 OK — pronto para reconciliar.")

        _run_step(cmd, self._log, _after)

    def _step2_skip(self) -> None:
        _log_write(self._log, "\n[2] Gabarito Word ignorado.\n")
        self._btn_word.configure(state="disabled")
        self._btn_skip_word.configure(state="disabled")
        self._btn_reconcile.configure(state="normal")
        self._set_status("Passo 2 ignorado — pronto para reconciliar.")

    # ── Step 3 ────────────────────────────────────────────────────────────────

    def _step3_reconcile(self) -> None:
        _log_write(self._log, "\n>> [3] A reconciliar...\n")
        self._btn_reconcile.configure(state="disabled")
        self._set_status("A reconciliar...")

        cmd = [
            sys.executable, "-m", "reconcile.run_reconcile",
            "--run-dir", str(self._run_dir),
        ]

        def _after(rc: int) -> None:
            if rc != 0:
                _log_write(self._log, "\nERRO na reconciliação.\n")
                self._btn_reconcile.configure(state="normal")
                self._set_status("Erro na reconciliação.")
                return
            _log_write(self._log, "\nPasso 3 concluído.\n")
            self._btn_interpret.configure(state="normal")
            self._set_status("Passo 3 OK — pronto para interpretar.")

        _run_step(cmd, self._log, _after)

    # ── Step 4 ────────────────────────────────────────────────────────────────

    def _step4_interpret(self) -> None:
        _log_write(self._log, "\n>> [4] A gerar interpretação (IA)...\n")
        self._btn_interpret.configure(state="disabled")
        self._set_status("A interpretar (IA)...")

        cmd = [
            sys.executable, "tools/interpret_run.py",
            "--run-dir", str(self._run_dir),
        ]

        def _after(rc: int) -> None:
            if rc != 0:
                _log_write(self._log, "\nERRO na interpretação (OPENAI_API_KEY ausente?).\n")
                self._btn_interpret.configure(state="normal")
                self._set_status("Erro na interpretação.")
                return
            out = self._run_dir / "audit_interpretation.md"
            _log_write(self._log, f"\nConcluído. Relatório: {out}\n")
            self._set_status(f"Pipeline concluído. Output: {self._run_dir.name}")

        _run_step(cmd, self._log, _after)


# ── Re-correr tab ─────────────────────────────────────────────────────────────

class RecorrerTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, log: "tk.Text") -> None:
        super().__init__(parent, padding=12)
        self._log = log
        self._run_dir: Path | None = None

        # ── Folder picker ─────────────────────────────────────────────────────
        picker_frame = ttk.LabelFrame(self, text="Run existente", padding=10)
        picker_frame.pack(fill="x", pady=(0, 10))
        picker_frame.columnconfigure(1, weight=1)

        ttk.Label(picker_frame, text="Pasta da run:").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._dir_var = tk.StringVar()
        ttk.Entry(picker_frame, textvariable=self._dir_var, state="readonly", width=60).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Button(picker_frame, text="Escolher...", command=self._pick_dir).grid(
            row=0, column=2, padx=(6, 0)
        )

        # ── Checkbox inferências ──────────────────────────────────────────────
        self._inferred_reviewed = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self,
            text="Respostas inferidas já revistas manualmente",
            variable=self._inferred_reviewed,
        ).pack(anchor="w", pady=(0, 8))

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=(0, 8))

        self._btn_reconcile = ttk.Button(
            btn_frame, text="Reconciliar", command=self._reconcile, state="disabled"
        )
        self._btn_reconcile.pack(side="left", padx=(0, 6))

        self._btn_interpret = ttk.Button(
            btn_frame, text="Interpretar (IA)", command=self._interpret, state="disabled"
        )
        self._btn_interpret.pack(side="left", padx=(0, 6))

        _finder_label = "Abrir no Finder" if sys.platform == "darwin" else "Abrir no Explorer"
        self._btn_finder = ttk.Button(
            btn_frame, text=_finder_label, command=self._open_finder, state="disabled"
        )
        self._btn_finder.pack(side="left")

        self._status_var = tk.StringVar(value="Seleciona uma pasta de run.")
        ttk.Label(self, textvariable=self._status_var, foreground="grey").pack(anchor="w")

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(
            title="Selecionar pasta de run existente",
            initialdir=str(PROJECT_ROOT / "output"),
        )
        if not d:
            return
        self._run_dir = Path(d)
        self._dir_var.set(d)

        # Pre-fill checkbox from run_meta.cfg
        meta = self._run_dir / "run_meta.cfg"
        if meta.exists():
            for line in meta.read_text(encoding="utf-8").splitlines():
                if line.startswith("INFERRED_REVIEWED="):
                    self._inferred_reviewed.set(line.partition("=")[2].strip().lower() == "true")

        self._btn_reconcile.configure(state="normal")
        self._btn_interpret.configure(state="normal")
        self._btn_finder.configure(state="normal")
        self._status_var.set(f"Run seleccionada: {self._run_dir.name}")

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)

    def _reconcile(self) -> None:
        if not self._run_dir:
            return
        _log_clear(self._log)
        _log_write(self._log, f"Run: {self._run_dir}\n\n>> A reconciliar...\n")
        self._btn_reconcile.configure(state="disabled")
        self._set_status("A reconciliar...")

        cmd = [
            sys.executable, "-m", "reconcile.run_reconcile",
            "--run-dir", str(self._run_dir),
        ]

        def _after(rc: int) -> None:
            self._btn_reconcile.configure(state="normal")
            if rc != 0:
                _log_write(self._log, "\nERRO na reconciliação.\n")
                self._set_status("Erro na reconciliação.")
            else:
                _log_write(self._log, "\nReconciliação concluída.\n")
                self._set_status("Reconciliação concluída.")

        _run_step(cmd, self._log, _after)

    def _interpret(self) -> None:
        if not self._run_dir:
            return

        # Write INFERRED_REVIEWED to run_meta.cfg before interpreting
        _set_run_meta_key(
            self._run_dir,
            "INFERRED_REVIEWED",
            "true" if self._inferred_reviewed.get() else "false",
        )

        _log_write(self._log, "\n>> A interpretar (IA)...\n")
        self._btn_interpret.configure(state="disabled")
        self._set_status("A interpretar (IA)...")

        cmd = [
            sys.executable, "tools/interpret_run.py",
            "--run-dir", str(self._run_dir),
        ]

        def _after(rc: int) -> None:
            self._btn_interpret.configure(state="normal")
            if rc != 0:
                _log_write(self._log, "\nERRO na interpretação.\n")
                self._set_status("Erro na interpretação.")
            else:
                out = self._run_dir / "audit_interpretation.md"
                _log_write(self._log, f"\nConcluído. Relatório: {out}\n")
                self._set_status("Interpretação concluída.")

        _run_step(cmd, self._log, _after)

    def _open_finder(self) -> None:
        if not (self._run_dir and self._run_dir.exists()):
            return
        if sys.platform == "win32":
            import os
            os.startfile(str(self._run_dir))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self._run_dir)])
        else:
            subprocess.Popen(["xdg-open", str(self._run_dir)])


# ── Main window ───────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("LearnWorlds — Auditoria de Avaliações")
        self.resizable(True, True)
        self.minsize(700, 560)

        # ── Shared log ────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(4, 10), side="bottom")

        scroll = ttk.Scrollbar(log_frame)
        scroll.pack(side="right", fill="y")

        self._log = tk.Text(
            log_frame,
            height=12,
            state="disabled",
            wrap="word",
            yscrollcommand=scroll.set,
            font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="white",
        )
        self._log.pack(fill="both", expand=True)
        scroll.configure(command=self._log.yview)

        # ── Notebook tabs ─────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=False, padx=10, pady=(10, 0))

        self._nova_run = NovaRunTab(nb, self._log)
        nb.add(self._nova_run, text="  Nova Run  ")

        self._recorrer = RecorrerTab(nb, self._log)
        nb.add(self._recorrer, text="  Re-correr  ")

        # ── Startup: install deps if needed ───────────────────────────────────
        self.after(200, self._check_deps)

    def _check_deps(self) -> None:
        try:
            import openai  # noqa: F401
        except ImportError:
            _log_write(self._log, "A instalar dependências (primeira vez)...\n")

            def _install() -> None:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(PROJECT_ROOT),
                )
                for line in proc.stdout:
                    self._log.after(0, lambda l=line: _log_write(self._log, l))
                proc.wait()
                self._log.after(
                    0,
                    lambda: _log_write(
                        self._log,
                        "Dependências instaladas.\n\n" if proc.returncode == 0
                        else "AVISO: falha ao instalar dependências.\n\n",
                    ),
                )

            threading.Thread(target=_install, daemon=True).start()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
