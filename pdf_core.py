"""Lógica de manipulação de PDF (sem interface gráfica).

Responsável por abrir, renderizar, extrair trechos de texto com suas
fontes, substituir texto mantendo a fonte original, remover imagens,
desfazer alterações e salvar.
"""

import math
import os
import re
import struct
from dataclasses import dataclass

import pymupdf

SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
MAX_UNDO = 30


def clean_font_name(name):
    """Remove o prefixo de subconjunto (ex.: 'ABCDEF+Arial' -> 'Arial')."""
    return SUBSET_PREFIX.sub("", name or "")


def _norm(name):
    """Normaliza nomes de fonte para comparação ('Arial-BoldMT' -> 'arialbold')."""
    n = re.sub(r"[^a-z0-9]", "", clean_font_name(name).lower()).replace("regular", "")
    return n[:-2] if n.endswith("mt") else n


@dataclass
class TextSpan:
    """Um trecho contínuo de texto com a mesma fonte, tamanho e cor."""

    bbox: pymupdf.Rect  # coordenadas da página não rotacionada
    origin: pymupdf.Point
    text: str
    font: str
    size: float
    color: int
    flags: int
    direction: tuple

    @property
    def is_bold(self):
        return bool(self.flags & pymupdf.TEXT_FONT_BOLD) or "bold" in self.font.lower()

    @property
    def is_italic(self):
        name = self.font.lower()
        return bool(self.flags & pymupdf.TEXT_FONT_ITALIC) or "italic" in name or "oblique" in name


@dataclass
class ImageItem:
    bbox: pymupdf.Rect
    xref: int


# --------------------------------------------------------------------------
# Índice das fontes instaladas no sistema (usado quando a fonte embutida no
# PDF é um subconjunto que não contém os caracteres digitados).
# --------------------------------------------------------------------------

def _read_name_table(data, offset):
    """Lê nomes (família completa e PostScript) de uma fonte TrueType/OpenType."""
    names = set()
    num_tables = struct.unpack_from(">H", data, offset + 4)[0]
    for i in range(num_tables):
        rec = offset + 12 + i * 16
        tag = data[rec:rec + 4]
        if tag != b"name":
            continue
        table = struct.unpack_from(">I", data, rec + 8)[0]
        _, count, str_offset = struct.unpack_from(">HHH", data, table)
        for j in range(count):
            pid, eid, _, nid, length, off = struct.unpack_from(">HHHHHH", data, table + 6 + j * 12)
            if nid not in (4, 6):
                continue
            raw = data[table + str_offset + off: table + str_offset + off + length]
            try:
                names.add(raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1"))
            except UnicodeDecodeError:
                pass
    return names


class SystemFonts:
    _index = None

    @classmethod
    def _font_dirs(cls):
        dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
                "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                "/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
        return [d for d in dirs if d and os.path.isdir(d)]

    @classmethod
    def index(cls):
        if cls._index is not None:
            return cls._index
        cls._index = {}
        for folder in cls._font_dirs():
            for root, _, files in os.walk(folder):
                for fname in files:
                    if not fname.lower().endswith((".ttf", ".otf", ".ttc")):
                        continue
                    path = os.path.join(root, fname)
                    try:
                        with open(path, "rb") as fh:
                            data = fh.read()
                        # Em coleções .ttc só a primeira fonte pode ser carregada.
                        offset = struct.unpack_from(">I", data, 12)[0] if data[:4] == b"ttcf" else 0
                        for name in _read_name_table(data, offset):
                            cls._index.setdefault(_norm(name), path)
                    except (OSError, struct.error):
                        continue
        return cls._index

    @classmethod
    def find(cls, font_name):
        return cls.index().get(_norm(font_name))


# --------------------------------------------------------------------------

class PdfDocument:
    def __init__(self, path, password=None):
        self.path = path
        with open(path, "rb") as fh:
            data = fh.read()
        # Aberto a partir da memória para não travar o arquivo no Windows
        # (permite salvar por cima do original).
        self.doc = pymupdf.open(stream=data, filetype="pdf")
        if self.doc.needs_pass and not self.doc.authenticate(password or ""):
            raise PermissionError("Senha incorreta ou necessária.")
        self._undo = []
        self.modified = False
        self._font_aliases = {}

    @staticmethod
    def needs_password(path):
        with pymupdf.open(path) as d:
            return d.needs_pass

    # ---------------------------------------------------------------- leitura
    @property
    def page_count(self):
        return self.doc.page_count

    def page_transform(self, page_no):
        """Matriz das coordenadas do PDF para as da imagem renderizada."""
        return self.doc[page_no].rotation_matrix

    def render(self, page_no, zoom):
        """Retorna (dados PPM, largura, altura) da página."""
        pix = self.doc[page_no].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("ppm"), pix.width, pix.height

    def spans(self, page_no):
        page = self.doc[page_no]
        flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
        result = []
        for block in page.get_text("dict", flags=flags)["blocks"]:
            for line in block.get("lines", []):
                for s in line["spans"]:
                    if not s["text"].strip():
                        continue
                    result.append(TextSpan(
                        bbox=pymupdf.Rect(s["bbox"]), origin=pymupdf.Point(s["origin"]),
                        text=s["text"], font=s["font"], size=s["size"], color=s["color"],
                        flags=s["flags"], direction=tuple(line["dir"])))
        return result

    def images(self, page_no):
        page = self.doc[page_no]
        items = []
        for info in page.get_image_info(xrefs=True):
            rect = pymupdf.Rect(info["bbox"]) & page.mediabox
            if not rect.is_empty:
                items.append(ImageItem(rect, info.get("xref", 0)))
        return items

    # ----------------------------------------------------------------- fontes
    def _embedded_font(self, page, font_name):
        """Busca o arquivo da fonte embutida no PDF que tem o nome do trecho."""
        target = clean_font_name(font_name)
        for xref, ext, _ftype, basefont, *_ in page.get_fonts(full=True):
            if clean_font_name(basefont) != target or ext == "n/a":
                continue
            _, ext, _, buffer = self.doc.extract_font(xref)
            if buffer:
                return xref, buffer
        return None, None

    @staticmethod
    def _covers(font, text):
        return all(font.has_glyph(ord(c)) for c in text)

    @staticmethod
    def _base14(span):
        """Fonte padrão do PDF mais parecida com a original."""
        name = span.font.lower()
        if span.flags & pymupdf.TEXT_FONT_MONOSPACED or "courier" in name or "mono" in name:
            family, label = ("cour", "cobo", "coit", "cobi"), "Courier"
        elif span.flags & pymupdf.TEXT_FONT_SERIFED or "times" in name or "serif" in name:
            family, label = ("tiro", "tibo", "tiit", "tibi"), "Times"
        else:
            family, label = ("helv", "hebo", "heit", "hebi"), "Helvetica"
        return family[span.is_bold + 2 * span.is_italic], label

    def _choose_font(self, page, span, new_text):
        """Escolhe a fonte para o novo texto.

        Ordem de preferência:
          1. a própria fonte embutida no PDF (se contém todos os caracteres);
          2. a fonte de mesmo nome instalada no sistema;
          3. uma fonte padrão do PDF parecida (serifa/sem serifa, negrito, itálico).
        Retorna (nome_para_insert_text, kwargs_de_insert_font ou None, descrição).
        """
        clean = clean_font_name(span.font)
        xref, buffer = self._embedded_font(page, span.font)
        if buffer:
            try:
                if self._covers(pymupdf.Font(fontbuffer=buffer), new_text):
                    return f"E{xref}", {"fontbuffer": buffer}, f"{clean} (fonte original do PDF)"
            except Exception:
                pass

        path = SystemFonts.find(span.font)
        if path:
            try:
                if self._covers(pymupdf.Font(fontfile=path), new_text):
                    alias = self._font_aliases.setdefault(path, f"S{len(self._font_aliases)}")
                    return alias, {"fontfile": path}, f"{clean} (instalada no sistema)"
            except Exception:
                pass

        base, label = self._base14(span)
        if _norm(label) in _norm(clean):
            return base, None, f"{label} (fonte padrão do PDF)"
        reason = "não contém os caracteres digitados" if buffer else "não está embutida no PDF"
        return base, None, f"{label} (substituta: '{clean}' {reason})"

    # ----------------------------------------------------------------- edição
    def _snapshot(self):
        self._undo.append(self.doc.tobytes())
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)

    @property
    def can_undo(self):
        return bool(self._undo)

    def undo(self):
        if not self._undo:
            return False
        self.doc.close()
        self.doc = pymupdf.open(stream=self._undo.pop(), filetype="pdf")
        self.modified = True
        return True

    @staticmethod
    def _inner_rect(rect):
        """Retângulo um pouco menor que o trecho, para não apagar linhas vizinhas."""
        h = rect.height
        return pymupdf.Rect(rect.x0 + 0.3, rect.y0 + h * 0.3, rect.x1 - 0.3, rect.y1 - h * 0.3)

    def replace_text(self, page_no, span, new_text):
        """Substitui o texto do trecho mantendo fonte, tamanho, cor e posição.

        Retorna a descrição da fonte utilizada.
        """
        page = self.doc[page_no]
        fontname, font_kwargs, description = self._choose_font(page, span, new_text)
        self._snapshot()

        page.add_redact_annot(self._inner_rect(span.bbox), fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)

        if new_text:
            if font_kwargs:
                page.insert_font(fontname=fontname, **font_kwargs)
            dx, dy = span.direction
            kwargs = {}
            if abs(dx - 1) > 1e-3 or abs(dy) > 1e-3:
                angle = math.degrees(math.atan2(-dy, dx))
                kwargs["morph"] = (span.origin, pymupdf.Matrix(angle))
            page.insert_text(span.origin, new_text, fontname=fontname, fontsize=span.size,
                             color=pymupdf.sRGB_to_pdf(span.color), **kwargs)
        self.modified = True
        return description

    def remove_image(self, page_no, image):
        page = self.doc[page_no]
        self._snapshot()
        page.add_redact_annot(image.bbox, fill=False, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_NONE)
        self.modified = True

    # ------------------------------------------------------------- gravação
    def save(self, path):
        out = pymupdf.open(stream=self.doc.tobytes(), filetype="pdf")
        try:
            out.subset_fonts()  # reduz o tamanho de fontes do sistema embutidas
        except Exception:
            pass
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        self.path = path
        self.modified = False

    def close(self):
        self.doc.close()
