# app/ui/tkinter_app.py

import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk
from pdf2image import convert_from_path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rules.rule_generator import generate_rule_from_region


DATA_DIR = Path("/home/harielpadillasanchez/Documentos/Empleo/Inbursa/OCR_document_inbursa/data")
SUPPORTED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}  # DOC/DOCX fuera por ahora


@dataclass
class DocState:
    file_path: Optional[Path] = None
    doc_type: Optional[str] = None  # "pdf" | "image"
    pages: Optional[List[Image.Image]] = None  # PIL images (original)
    page_index: int = 0
    zoom: float = 1.0


def list_data_files() -> List[Path]:
    if not DATA_DIR.exists():
        return []
    return [
        p for p in sorted(DATA_DIR.iterdir())
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
    ]


def build_region_payload(file_path: str, page_index: int, bbox: dict, mode: str = "auto") -> dict:
    """
    Hook para tu lógica inteligente. MVP: region_only.
    """
    return {
        "file_path": file_path,
        "page_index": page_index,
        "bbox": bbox,
        "strategy": "region_only",
        "extra_pages": [],
        "mode": mode,
    }


class OCRRuleTkApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OCR + LLM Rules (Tkinter MVP)")
        self.geometry("1200x800")

        self.state_ = DocState()
        self.rules: List[Dict[str, Any]] = []

        # selección actual (coords en canvas)
        self._drag_start: Optional[Tuple[int, int]] = None
        self._rect_id: Optional[int] = None
        self._last_bbox_img: Optional[dict] = None  # coords en imagen original

        self._photo: Optional[ImageTk.PhotoImage] = None  # referencia para Tkinter

        self._build_ui()
        self._load_file_list()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ttk.Button(top, text="Abrir archivo...", command=self.open_file_dialog).pack(side=tk.LEFT)
        ttk.Label(top, text="   o elegir en /data:").pack(side=tk.LEFT)

        self.file_combo = ttk.Combobox(top, state="readonly", width=60)
        self.file_combo.pack(side=tk.LEFT, padx=6)
        self.file_combo.bind("<<ComboboxSelected>>", self.on_pick_data_file)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)

        # Controls
        controls = ttk.Frame(self)
        controls.pack(side=tk.TOP, fill=tk.X, padx=8)

        self.btn_prev = ttk.Button(controls, text="◀ Prev", command=self.prev_page, state=tk.DISABLED)
        self.btn_prev.pack(side=tk.LEFT)

        self.page_label = ttk.Label(controls, text="Página: -/-")
        self.page_label.pack(side=tk.LEFT, padx=10)

        self.btn_next = ttk.Button(controls, text="Next ▶", command=self.next_page, state=tk.DISABLED)
        self.btn_next.pack(side=tk.LEFT)

        ttk.Label(controls, text="   Zoom:").pack(side=tk.LEFT, padx=(20, 4))
        self.zoom_var = tk.DoubleVar(value=1.0)
        self.zoom_slider = ttk.Scale(controls, from_=0.5, to=2.0, value=1.0, command=self.on_zoom_change)
        self.zoom_slider.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(controls, text="Reset selección", command=self.reset_selection).pack(side=tk.LEFT, padx=10)

        # Field title + actions
        actions = ttk.Frame(self)
        actions.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        ttk.Label(actions, text="Título del campo:").pack(side=tk.LEFT)
        self.field_title_var = tk.StringVar(value="numero_cuenta")
        ttk.Entry(actions, textvariable=self.field_title_var, width=30).pack(side=tk.LEFT, padx=6)

        ttk.Button(actions, text="Generar regla (LLM)", command=self.generate_rule).pack(side=tk.LEFT, padx=10)

        self.status_var = tk.StringVar(value="Listo. Abre un archivo para comenzar.")
        ttk.Label(actions, textvariable=self.status_var).pack(side=tk.LEFT, padx=10)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)

        # Main canvas (with scroll)
        main = ttk.Frame(self)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(main, bg="#222222", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        yscroll = ttk.Scrollbar(main, orient=tk.VERTICAL, command=self.canvas.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.configure(yscrollcommand=yscroll.set)

        # Bind mouse for rectangle selection
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)

        # Bottom info
        bottom = ttk.Frame(self)
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=6)
        self.bbox_label = ttk.Label(bottom, text="BBox: (sin selección)")
        self.bbox_label.pack(side=tk.LEFT)

        self.rules_label = ttk.Label(bottom, text="Reglas en memoria: 0")
        self.rules_label.pack(side=tk.RIGHT)

    def _load_file_list(self):
        files = list_data_files()
        self._data_files = files
        labels = [f.name for f in files]
        self.file_combo["values"] = labels
        if labels:
            self.file_combo.set(labels[0])

    # ---------------- File handling ----------------
    def open_file_dialog(self):
        path = filedialog.askopenfilename(
            title="Selecciona un documento",
            filetypes=[
                ("PDF", "*.pdf"),
                ("Images", "*.png *.jpg *.jpeg"),
                ("All", "*.*"),
            ],
        )
        if not path:
            return
        self.load_document(Path(path))

    def on_pick_data_file(self, _evt=None):
        name = self.file_combo.get()
        if not name:
            return
        p = DATA_DIR / name
        self.load_document(p)

    def load_document(self, path: Path):
        if not path.exists():
            messagebox.showerror("Error", f"No existe: {path}")
            return

        ext = path.suffix.lower()
        self.reset_selection()

        try:
            if ext == ".pdf":
                self.status_var.set("Cargando PDF (renderizando páginas)...")
                self.update_idletasks()
                pages = convert_from_path(str(path), dpi=160)
                self.state_ = DocState(file_path=path, doc_type="pdf", pages=pages, page_index=0, zoom=1.0)
                self.zoom_var.set(1.0)
                self.zoom_slider.set(1.0)
            elif ext in {".png", ".jpg", ".jpeg"}:
                img = Image.open(path).convert("RGB")
                self.state_ = DocState(file_path=path, doc_type="image", pages=[img], page_index=0, zoom=1.0)
                self.zoom_var.set(1.0)
                self.zoom_slider.set(1.0)
            else:
                messagebox.showwarning("No soportado", f"Formato no soportado: {ext}")
                return

            self.status_var.set(f"Cargado: {path.name}")
            self._refresh_page_controls()
            self.render_current_page()

        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status_var.set("Error al cargar documento.")

    def _refresh_page_controls(self):
        pages = self.state_.pages or []
        total = len(pages)

        if total > 1:
            self.btn_prev.config(state=tk.NORMAL)
            self.btn_next.config(state=tk.NORMAL)
        else:
            self.btn_prev.config(state=tk.DISABLED)
            self.btn_next.config(state=tk.DISABLED)

        self.page_label.config(text=f"Página: {self.state_.page_index + 1}/{max(1, total)}")

    def prev_page(self):
        if not self.state_.pages:
            return
        if self.state_.page_index > 0:
            self.state_.page_index -= 1
            self.reset_selection()
            self._refresh_page_controls()
            self.render_current_page()

    def next_page(self):
        if not self.state_.pages:
            return
        if self.state_.page_index < len(self.state_.pages) - 1:
            self.state_.page_index += 1
            self.reset_selection()
            self._refresh_page_controls()
            self.render_current_page()

    def on_zoom_change(self, _val):
        self.state_.zoom = float(self.zoom_slider.get())
        self.render_current_page()

    # ---------------- Rendering ----------------
    def render_current_page(self):
        if not self.state_.pages:
            return

        img = self.state_.pages[self.state_.page_index]
        zoom = self.state_.zoom

        w, h = img.size
        zw, zh = int(w * zoom), int(h * zoom)

        rendered = img.resize((zw, zh))

        self._photo = ImageTk.PhotoImage(rendered)
        self.canvas.delete("all")

        # Put image at 0,0 anchored nw
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags=("page_image",))

        # update scroll region
        self.canvas.configure(scrollregion=(0, 0, zw, zh))

        self._refresh_page_controls()
        self._update_bbox_label()

    # ---------------- Selection (rectangle) ----------------
    def on_mouse_down(self, event):
        if not self.state_.pages:
            return
        self.reset_selection(keep_bbox=False)
        self._drag_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        x, y = self._drag_start
        self._rect_id = self.canvas.create_rectangle(x, y, x, y, outline="red", width=2)

    def on_mouse_drag(self, event):
        if self._drag_start is None or self._rect_id is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        self.canvas.coords(self._rect_id, x0, y0, x1, y1)

    def on_mouse_up(self, event):
        if self._drag_start is None or self._rect_id is None:
            return

        x0, y0 = self._drag_start
        x1, y1 = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

        # normalize
        rx1, ry1 = min(x0, x1), min(y0, y1)
        rx2, ry2 = max(x0, x1), max(y0, y1)

        # convert from rendered coords -> original img coords
        img = self.state_.pages[self.state_.page_index]
        img_w, img_h = img.size
        zoom = self.state_.zoom

        ox1 = int(rx1 / zoom)
        oy1 = int(ry1 / zoom)
        ox2 = int(rx2 / zoom)
        oy2 = int(ry2 / zoom)

        # clamp
        ox1 = max(0, min(img_w - 1, ox1))
        oy1 = max(0, min(img_h - 1, oy1))
        ox2 = max(0, min(img_w, ox2))
        oy2 = max(0, min(img_h, oy2))

        # validate bbox size
        if abs(ox2 - ox1) < 2 or abs(oy2 - oy1) < 2:
            self._last_bbox_img = None
            self.status_var.set("Selección muy pequeña. Intenta de nuevo.")
        else:
            self._last_bbox_img = {"x1": ox1, "y1": oy1, "x2": ox2, "y2": oy2}
            self.status_var.set("Selección lista. Ahora genera regla.")

        self._drag_start = None
        self._update_bbox_label()

    def reset_selection(self, keep_bbox: bool = False):
        if self._rect_id is not None:
            try:
                self.canvas.delete(self._rect_id)
            except Exception:
                pass
        self._rect_id = None
        self._drag_start = None
        if not keep_bbox:
            self._last_bbox_img = None
        self._update_bbox_label()

    def _update_bbox_label(self):
        if self._last_bbox_img is None:
            self.bbox_label.config(text="BBox: (sin selección)")
        else:
            self.bbox_label.config(text=f"BBox: {self._last_bbox_img}")

    # ---------------- Rule generation ----------------
    def generate_rule(self):
        if self.state_.file_path is None or not self.state_.pages:
            messagebox.showwarning("Falta documento", "Carga un documento primero.")
            return

        if self._last_bbox_img is None:
            messagebox.showwarning("Falta selección", "Selecciona un área con el mouse (rectángulo).")
            return

        field_title = self.field_title_var.get().strip()
        if not field_title:
            messagebox.showwarning("Falta título", "Escribe el título del campo.")
            return

        file_path = str(self.state_.file_path)
        page_index = int(self.state_.page_index)
        bbox = dict(self._last_bbox_img)

        # 1) payload inteligente
        region_payload = build_region_payload(
            file_path=file_path,
            page_index=page_index,
            bbox=bbox,
            mode="auto",
        )

        # 2) OCR placeholder (se conecta después)
        ocr_text = "[OCR_PENDING] Aún no está conectado el OCR. Solo bbox/payload."

        # 3) LLM
        try:
            rule = generate_rule_from_region(
                region=region_payload,
                ocr_text=ocr_text,
                field_title=field_title,
            )

            print("=== Regla generada ===")
            print(json.dumps(rule, ensure_ascii=False, indent=2))

            self.rules.append(
                {
                    "file": file_path,
                    "page_index": page_index,
                    "bbox": bbox,
                    "field_title": field_title,
                    "region_payload": region_payload,
                    "rule": rule,
                }
            )
            self.rules_label.config(text=f"Reglas en memoria: {len(self.rules)}")
            self.status_var.set(f"Regla #{len(self.rules)} generada (ver consola).")

        except Exception as e:
            messagebox.showerror("Error LLM", str(e))
            self.status_var.set("Error al generar regla.")

    # ---------------- Close / save ----------------
    def on_close(self):
        """
        Al cerrar:
        - guarda rules_output.json si hay reglas
        """
        if self.rules:
            out_path = ROOT / "rules_output.json"
            try:
                out_path.write_text(json.dumps(self.rules, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[INFO] Guardado: {out_path}")
            except Exception as e:
                messagebox.showerror("Error guardando JSON", str(e))

        self.destroy()


def main():
    app = OCRRuleTkApp()
    app.mainloop()


if __name__ == "__main__":
    main()