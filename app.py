"""Editor de PDF simples: abra um PDF, clique num texto para editá-lo
(mantendo a fonte original) ou remova imagens, e salve.

Uso:  python app.py [arquivo.pdf]
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import pymupdf

from pdf_core import PdfDocument, clean_font_name

ZOOM_STEPS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]
TEXT_COLOR = "#1a73e8"
IMAGE_COLOR = "#d93025"


class EditDialog(tk.Toplevel):
    """Janela para editar o texto de um trecho."""

    def __init__(self, parent, span):
        super().__init__(parent)
        self.title("Editar texto")
        self.transient(parent)
        self.resizable(True, False)
        self.result = None

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)

        style = []
        if span.is_bold:
            style.append("negrito")
        if span.is_italic:
            style.append("itálico")
        info = f"Fonte: {clean_font_name(span.font)}   •   Tamanho: {span.size:.1f} pt"
        if style:
            info += "   •   " + ", ".join(style)
        ttk.Label(frame, text=info, foreground="#555").pack(anchor="w")

        self.var = tk.StringVar(value=span.text.replace("\xa0", " "))
        entry = ttk.Entry(frame, textvariable=self.var, width=max(40, len(span.text) + 10),
                          font=("Segoe UI", 11))
        entry.pack(fill="x", pady=(8, 4))
        entry.select_range(0, "end")
        entry.focus_set()

        ttk.Label(frame, text="Deixe vazio para apagar o texto.", foreground="#888").pack(anchor="w")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Aplicar", command=self._ok).pack(side="right", padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.wait_window()

    def _ok(self):
        self.result = self.var.get()
        self.destroy()


class PdfEditorApp(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title("Editor de PDF")
        self.geometry("1100x800")
        self.minsize(700, 500)

        self.pdf = None
        self.page_no = 0
        self.zoom = 1.25
        self.mode = tk.StringVar(value="text")
        self.items = []          # [(retângulo na tela, objeto)]
        self.hover = None
        self._photo = None

        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self._refresh_controls()
        if path:
            self.after(50, lambda: self.open_file(path))

    # ----------------------------------------------------------------- layout
    def _build_ui(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Toolbar.TFrame", background="#f3f3f3")

        bar = ttk.Frame(self, padding=(8, 6), style="Toolbar.TFrame")
        bar.pack(side="top", fill="x")

        def btn(text, cmd, **kw):
            b = ttk.Button(bar, text=text, command=cmd, **kw)
            b.pack(side="left", padx=2)
            return b

        def sep():
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        btn("📂 Abrir", self.ask_open)
        self.btn_save = btn("💾 Salvar", self.save)
        self.btn_save_as = btn("Salvar como…", self.save_as)
        sep()
        self.btn_undo = btn("↶ Desfazer", self.undo)
        sep()
        self.btn_prev = btn("◀", self.prev_page, width=3)
        self.page_label = ttk.Label(bar, text="—", width=14, anchor="center", background="#f3f3f3")
        self.page_label.pack(side="left")
        self.btn_next = btn("▶", self.next_page, width=3)
        sep()
        self.btn_zoom_out = btn("−", self.zoom_out, width=3)
        self.zoom_label = ttk.Label(bar, text="", width=6, anchor="center", background="#f3f3f3")
        self.zoom_label.pack(side="left")
        self.btn_zoom_in = btn("+", self.zoom_in, width=3)
        sep()
        ttk.Label(bar, text="Modo:", background="#f3f3f3").pack(side="left", padx=(0, 4))
        for value, label in (("text", "Editar texto"), ("image", "Remover imagem")):
            ttk.Radiobutton(bar, text=label, value=value, variable=self.mode,
                            command=self._on_mode_change).pack(side="left", padx=4)

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, background="#7f8c8d", highlightthickness=0)
        vbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.status = tk.StringVar(value="Abra um PDF para começar (Ctrl+O).")
        ttk.Label(self, textvariable=self.status, padding=(10, 4), relief="sunken",
                  anchor="w").pack(side="bottom", fill="x")

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda e: self._center_page())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _bind_keys(self):
        self.bind("<Control-o>", lambda e: self.ask_open())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-S>", lambda e: self.save_as())
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Prior>", lambda e: self.prev_page())
        self.bind("<Next>", lambda e: self.next_page())
        self.bind("<Control-plus>", lambda e: self.zoom_in())
        self.bind("<Control-equal>", lambda e: self.zoom_in())
        self.bind("<Control-minus>", lambda e: self.zoom_out())

    def _refresh_controls(self):
        has = self.pdf is not None
        state = lambda ok: "normal" if ok else "disabled"
        for b in (self.btn_save, self.btn_save_as, self.btn_zoom_in, self.btn_zoom_out):
            b.configure(state=state(has))
        self.btn_save.configure(state=state(has and self.pdf.modified))
        self.btn_undo.configure(state=state(has and self.pdf.can_undo))
        self.btn_prev.configure(state=state(has and self.page_no > 0))
        self.btn_next.configure(state=state(has and self.page_no < self.pdf.page_count - 1))
        self.page_label.configure(text=f"Página {self.page_no + 1} de {self.pdf.page_count}" if has else "—")
        self.zoom_label.configure(text=f"{int(self.zoom * 100)}%")
        name = os.path.basename(self.pdf.path) if has else ""
        mark = " •" if has and self.pdf.modified else ""
        self.title(f"{name}{mark} — Editor de PDF" if has else "Editor de PDF")

    # -------------------------------------------------------------- arquivos
    def ask_open(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="Abrir PDF", filetypes=[("Arquivos PDF", "*.pdf"),
                                                                        ("Todos os arquivos", "*.*")])
        if path:
            self.open_file(path)

    def open_file(self, path):
        try:
            password = None
            if PdfDocument.needs_password(path):
                password = simpledialog.askstring("Senha", "Este PDF é protegido. Digite a senha:",
                                                  show="•", parent=self)
                if password is None:
                    return
            pdf = PdfDocument(path, password)
        except Exception as exc:
            messagebox.showerror("Erro ao abrir", f"Não foi possível abrir o arquivo:\n{exc}")
            return
        if self.pdf:
            self.pdf.close()
        self.pdf = pdf
        self.page_no = 0
        self.canvas.yview_moveto(0)
        self.render()
        self.status.set(f"Aberto: {path}  —  clique sobre um texto para editá-lo.")

    def save(self):
        if self.pdf:
            self._write(self.pdf.path)

    def save_as(self):
        if not self.pdf:
            return
        base, _ = os.path.splitext(os.path.basename(self.pdf.path))
        path = filedialog.asksaveasfilename(title="Salvar como", defaultextension=".pdf",
                                            initialdir=os.path.dirname(self.pdf.path),
                                            initialfile=f"{base}_editado.pdf",
                                            filetypes=[("Arquivos PDF", "*.pdf")])
        if path:
            self._write(path)

    def _write(self, path):
        try:
            self.config(cursor="watch")
            self.update_idletasks()
            self.pdf.save(path)
        except Exception as exc:
            messagebox.showerror("Erro ao salvar", f"Não foi possível salvar:\n{exc}")
            return
        finally:
            self.config(cursor="")
        self.status.set(f"Salvo em {path}")
        self._refresh_controls()

    def _confirm_discard(self):
        if not self.pdf or not self.pdf.modified:
            return True
        answer = messagebox.askyesnocancel("Alterações não salvas",
                                           "Deseja salvar as alterações antes de continuar?")
        if answer is None:
            return False
        if answer:
            self.save()
            return not self.pdf.modified
        return True

    def _quit(self):
        if self._confirm_discard():
            self.destroy()

    # ------------------------------------------------------------ navegação
    def prev_page(self):
        if self.pdf and self.page_no > 0:
            self.page_no -= 1
            self.canvas.yview_moveto(0)
            self.render()

    def next_page(self):
        if self.pdf and self.page_no < self.pdf.page_count - 1:
            self.page_no += 1
            self.canvas.yview_moveto(0)
            self.render()

    def zoom_in(self):
        bigger = [z for z in ZOOM_STEPS if z > self.zoom + 1e-6]
        if self.pdf and bigger:
            self.zoom = bigger[0]
            self.render()

    def zoom_out(self):
        smaller = [z for z in ZOOM_STEPS if z < self.zoom - 1e-6]
        if self.pdf and smaller:
            self.zoom = smaller[-1]
            self.render()

    def _on_wheel(self, event):
        if event.state & 0x4:  # Ctrl + roda = zoom
            self.zoom_in() if event.delta > 0 else self.zoom_out()
        else:
            self.canvas.yview_scroll(-1 * (event.delta // 120 or (1 if event.delta > 0 else -1)) * 3, "units")

    # ---------------------------------------------------------- renderização
    def render(self):
        self.canvas.delete("all")
        self.hover = None
        if not self.pdf:
            return
        data, w, h = self.pdf.render(self.page_no, self.zoom)
        self._photo = tk.PhotoImage(data=data, format="PPM")
        self._page_size = (w, h)
        self.canvas.create_rectangle(4, 4, w + 4, h + 4, fill="#555", outline="", tags="page")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="page")
        self._center_page()
        self._refresh_controls()

    def _center_page(self):
        if not self.pdf or not hasattr(self, "_page_size"):
            return
        w, h = self._page_size
        margin = 20
        cw = self.canvas.winfo_width()
        self._offset = (max(margin, (cw - w) // 2), margin)
        x0, y0 = self.canvas.coords("page")[:2]
        self.canvas.move("page", self._offset[0] - x0 + 4, self._offset[1] - y0 + 4)
        self.canvas.configure(scrollregion=(0, 0, max(cw, w + 2 * margin), h + 2 * margin))
        self._load_items()

    def _load_items(self):
        """Calcula as áreas clicáveis (texto ou imagens) em coordenadas da tela."""
        if not self.pdf:
            return
        matrix = self.pdf.page_transform(self.page_no) * pymupdf.Matrix(self.zoom, self.zoom)
        ox, oy = getattr(self, "_offset", (0, 0))
        source = self.pdf.spans(self.page_no) if self.mode.get() == "text" else self.pdf.images(self.page_no)
        self.items = []
        for obj in source:
            r = obj.bbox * matrix
            self.items.append(((r.x0 + ox, r.y0 + oy, r.x1 + ox, r.y1 + oy), obj))
        self._set_hover(None)

    def _on_mode_change(self):
        self._load_items()
        if self.pdf:
            msg = ("Modo texto: clique sobre um texto para editá-lo." if self.mode.get() == "text"
                   else f"Modo imagem: clique sobre uma imagem para removê-la "
                        f"({len(self.items)} nesta página).")
            self.status.set(msg)

    # ------------------------------------------------------------- interação
    def _item_at(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        hits = [(rect, obj) for rect, obj in self.items
                if rect[0] - 1 <= x <= rect[2] + 1 and rect[1] - 1 <= y <= rect[3] + 1]
        # o menor retângulo é o mais específico (ex.: imagem sobre imagem)
        return min(hits, key=lambda h: (h[0][2] - h[0][0]) * (h[0][3] - h[0][1]), default=None)

    def _set_hover(self, hit):
        self.canvas.delete("hover")
        self.hover = hit
        if not hit:
            self.canvas.config(cursor="")
            return
        rect, obj = hit
        color = TEXT_COLOR if self.mode.get() == "text" else IMAGE_COLOR
        self.canvas.create_rectangle(rect[0] - 2, rect[1] - 2, rect[2] + 2, rect[3] + 2,
                                     outline=color, width=2, tags="hover")
        self.canvas.config(cursor="hand2")
        if self.mode.get() == "text":
            self.status.set(f"Fonte: {clean_font_name(obj.font)}  •  {obj.size:.1f} pt  —  "
                            f"clique para editar")
        else:
            self.status.set("Clique para remover esta imagem")

    def _on_motion(self, event):
        hit = self._item_at(event)
        if (hit and hit[1]) is not (self.hover and self.hover[1]):
            self._set_hover(hit)

    def _on_click(self, event):
        hit = self._item_at(event)
        if not hit:
            return
        obj = hit[1]
        if self.mode.get() == "text":
            dialog = EditDialog(self, obj)
            if dialog.result is None or dialog.result == obj.text.replace("\xa0", " "):
                return
            try:
                used = self.pdf.replace_text(self.page_no, obj, dialog.result)
            except Exception as exc:
                messagebox.showerror("Erro", f"Não foi possível editar o texto:\n{exc}")
                return
            self.render()
            self.status.set(f"Texto alterado. Fonte usada: {used}")
        else:
            if not messagebox.askyesno("Remover imagem", "Remover esta imagem da página?"):
                return
            try:
                self.pdf.remove_image(self.page_no, obj)
            except Exception as exc:
                messagebox.showerror("Erro", f"Não foi possível remover a imagem:\n{exc}")
                return
            self.render()
            self.status.set("Imagem removida.")

    def undo(self):
        if self.pdf and self.pdf.undo():
            self.page_no = min(self.page_no, self.pdf.page_count - 1)
            self.render()
            self.status.set("Última alteração desfeita.")


if __name__ == "__main__":
    PdfEditorApp(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
