"""Lee los nombres de senales, datos lentos y comandos de la GUI de javascope.h (R5).

El R5 y la GUI comparten los enums de ``vitis/software/Baremetal/src/app/javascope.h``:

* ``JS_OberservableData``  -> senales seleccionables en los 20 canales (indice = valor enviado);
  se compone de las listas ``MZ_SCOPE_SYSTEM_SIGNALS(X)`` (javascope.h) y ``MZ_SCOPE_SIGNALS(X)``
  (globalData.h), que se expanden aqui igual que hace el preprocesador de C
* ``JS_SlowData``          -> datos lentos (indice = slowDataID). Los que llevan un comentario
  ``/* etiqueta [unidad] */`` se muestran ademas en el panel de lecturas
* ``gui_button_mapping``   -> comandos (indice = id enviado). Se admiten los dos formatos del
  fichero y se detectan solos (``BoardNames.format``):

  - **nuevo** (mz-sw): ZEROVALUE, Enable_System, Enable_Control, Stop, Error_Reset y despues
    cualquier cantidad de ``My_Button_n /* etiqueta */`` y de ``Set_Send_Field_n /* variable [unidad] */``.
    La GUI crea un boton o un campo por linea.
  - **original** (mz-sw (old), JavaScope): ZEROVALUE, Enable_System, Enable_Control, Stop,
    Set_Send_Field_1..n, My_Button_1..n, Error_Reset, sin comentarios. Las etiquetas estan en el
    bloque comentado ``/* Visualization Config for GUI */`` al final del fichero, en las listas
    ``SND_FLD_*`` (nombres de los campos), ``SND_LABELS_*`` (unidades), ``MYBUTTONS_LABELS_*``
    (botones), ``SLOWDAT_DISPLAY_*`` (datos lentos del panel de lecturas), ``RCV_FLD_*`` y
    ``RCV_LABELS_*`` (sus etiquetas y unidades).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ENUM_RE = re.compile(r"enum\s+(\w+)\s*\{(.*?)\}\s*;", re.S)
_PREFIX_RE = re.compile(r"^(JSO_|JSSD_FLOAT_|JSSD_INT_|JSSD_)")
_MACRO_RE = re.compile(r"#define\s+(\w+)\(X\)((?:[^\n]*\\\n)*[^\n]*)")
_ITEM_RE = re.compile(r"^\s*(\w+)\s*(?:=\s*\w+)?\s*,?\s*(?:/\*(.*?)\*/|//(.*))?\s*$")
# listas "PREFIJO_ZEROVALUE=0, ..., PREFIJO_ENDMARKER" del bloque comentado del formato original
_LABEL_LIST_RE = re.compile(r"(\w+)_ZEROVALUE\s*=\s*0\s*,(.*?)\1_ENDMARKER", re.S)

FIXED_COMMANDS = ("Enable_System", "Enable_Control", "Stop", "Error_Reset")


def _strip_comments(text: str) -> str:
    return re.sub(r"//.*", "", text)


def _signal_macros(text: str) -> dict[str, list[str]]:
    """Listas ``#define NOMBRE(X) X(a) X(b, ...) ...`` -> {NOMBRE: [a, b, ...]} (globalData.h / javascope.h)."""
    out = {}
    for m in _MACRO_RE.finditer(text):
        body = _strip_comments(m.group(2))
        out[m.group(1)] = re.findall(r"\bX\(\s*(\w+)", body)
    return out


def _expand_macros(body: str, macros: dict[str, list[str]]) -> str:
    """Sustituye ``LISTA(X)`` dentro de un enum por sus nombres y quita las lineas #define/#undef."""
    m = re.search(r"#define\s+X\([^)]*\)\s*(\w+)##", body)   # "#define X(name, ...) JSO_##name," -> prefijo JSO_
    prefix = m.group(1) if m else ""
    body = re.sub(r"^[ \t]*#(?:define|undef)\b.*$", "", body, flags=re.M)
    for name, items in macros.items():
        body = body.replace(f"{name}(X)", ",".join(prefix + it for it in items) + ",")
    return body


def _parse_enum(body: str) -> list[tuple[str, str]]:
    """Cuerpo de un enum -> [(nombre, comentario de la linea o "")], en el orden del enum (indice = valor)."""
    items = []
    for line in body.splitlines():
        if not line.strip():
            continue
        m = _ITEM_RE.match(line)
        if m:
            items.append((m.group(1), (m.group(2) or m.group(3) or "").strip()))
            continue
        # varios identificadores en una linea (lista expandida "A,B,C,")
        for name in re.findall(r"\b([A-Za-z_]\w*)\b(?=\s*(?:=\s*\w+)?\s*,)", _strip_comments(line)):
            items.append((name, ""))
    return items


def _split_label(comment: str) -> tuple[str, str]:
    """``variable [unidad]`` -> (variable, unidad); sin corchetes -> (texto, "")."""
    m = re.fullmatch(r"(.*?)\s*\[([^\]]*)\]\s*", comment)
    return (m.group(1).strip(), m.group(2).strip()) if m else (comment.strip(), "")


def _label_lists(text: str) -> dict[str, list[str]]:
    """Listas de etiquetas del formato original -> {PREFIJO: [item, ...]} (sin ZEROVALUE ni ENDMARKER).

    Van dentro de un comentario ``/* ... */`` y no son C valido (hay lineas sin coma), asi que
    se toma un item por linea, ignorando lineas vacias y ``//``.
    """
    out = {}
    for m in _LABEL_LIST_RE.finditer(text):
        items = []
        for line in m.group(2).splitlines():
            line = line.split("//")[0].strip().rstrip(",").strip()
            if line:
                items.append(line)
        out[m.group(1)] = items
    return out


@dataclass
class GuiItem:
    cmd_id: int          # id que se envia (indice en gui_button_mapping) o slowDataID
    name: str            # identificador del enum
    label: str           # texto del comentario (o el nombre si no hay)
    unit: str = ""


@dataclass
class BoardNames:
    observables: list[str] = field(default_factory=list)    # JS_OberservableData (con el ZEROVALUE en 0)
    slow_data: list[str] = field(default_factory=list)      # JS_SlowData
    buttons: list[str] = field(default_factory=list)        # gui_button_mapping (indice = id)
    user_buttons: list[GuiItem] = field(default_factory=list)   # My_Button_n
    send_fields: list[GuiItem] = field(default_factory=list)    # Set_Send_Field_n
    receive_fields: list[GuiItem] = field(default_factory=list) # datos lentos con comentario
    source: str = ""
    format: str = "nuevo"        # "nuevo" (mz-sw) | "original" (mz-sw (old) / JavaScope)

    @staticmethod
    def pretty(name: str) -> str:
        return _PREFIX_RE.sub("", name)

    def observable_labels(self) -> list[str]:
        return [self.pretty(n) for n in self.observables]

    @property
    def n_slow(self) -> int:
        """Numero de slowDataID distintos que envia la placa: el contador va de 0 a JSSD_ENDMARKER-1
        (javascope.c: ``if (js_cnt_slowData >= JSSD_ENDMARKER) js_cnt_slowData = 0``), el ENDMARKER
        nunca se envia."""
        n = len(self.slow_data)
        if n and self.slow_data[-1].endswith("_ENDMARKER"):
            n -= 1
        return max(n, 1)

    def slow_index(self, name: str) -> int:
        try:
            return self.slow_data.index(name)
        except ValueError:
            return -1

    def cmd(self, name: str) -> int:
        """Id de un comando de gui_button_mapping (-1 si no existe)."""
        try:
            return self.buttons.index(name)
        except ValueError:
            return -1


def _build_gui_items(names: BoardNames, buttons: list[tuple[str, str]], slow: list[tuple[str, str]]) -> None:
    """Formato nuevo: las etiquetas son los comentarios de cada linea del enum."""
    names.format = "nuevo"
    names.buttons = [n for n, _ in buttons]
    names.user_buttons = [GuiItem(i, n, c or n) for i, (n, c) in enumerate(buttons) if n.startswith("My_Button")]
    names.send_fields = [GuiItem(i, n, *_split_label(c or n)) for i, (n, c) in enumerate(buttons) if n.startswith("Set_Send_Field")]
    names.slow_data = [n for n, _ in slow]
    names.receive_fields = [GuiItem(i, n, *_split_label(c)) for i, (n, c) in enumerate(slow) if c and i > 0]


def _build_gui_items_original(names: BoardNames, buttons: list[tuple[str, str]], slow: list[tuple[str, str]],
                              labels: dict[str, list[str]]) -> None:
    """Formato original: las etiquetas salen de las listas del bloque comentado (si faltan, el nombre)."""
    names.format = "original"
    names.buttons = [n for n, _ in buttons]
    names.slow_data = [n for n, _ in slow]

    def at(key: str, i: int, default: str = "") -> str:
        lst = labels.get(key, [])
        return lst[i] if i < len(lst) else default

    btn = [(i, n) for i, (n, _) in enumerate(buttons) if n.startswith("My_Button")]
    names.user_buttons = [GuiItem(cid, n, at("MYBUTTONS_LABELS", k, n)) for k, (cid, n) in enumerate(btn)]
    snd = [(i, n) for i, (n, _) in enumerate(buttons) if n.startswith("Set_Send_Field")]
    names.send_fields = [GuiItem(cid, n, at("SND_FLD", k, n), at("SND_LABELS", k).replace("-", ""))
                         for k, (cid, n) in enumerate(snd)]
    names.receive_fields = []
    for k, sname in enumerate(labels.get("SLOWDAT_DISPLAY", [])):
        sid = names.slow_index(sname)
        if sid > 0:
            names.receive_fields.append(GuiItem(sid, sname, at("RCV_FLD", k, names.pretty(sname)),
                                                at("RCV_LABELS", k).replace("-", "")))


def default_names() -> BoardNames:
    """Nombres minimos si no se encuentra javascope.h."""
    n = BoardNames()
    n.observables = ["JSO_ZEROVALUE"] + [f"JSO_ch{i}" for i in range(1, 64)]
    buttons = [("GUI_BTN_ZEROVALUE", "")] + [(c, "") for c in FIXED_COMMANDS] + \
              [(f"My_Button_{i}", f"Boton {i}") for i in range(1, 5)] + \
              [(f"Set_Send_Field_{i}", f"campo {i} [-]") for i in range(1, 5)] + [("GUI_BTN_ENDMARKER", "")]
    slow = [("JSSD_ZEROVALUE", ""), ("JSSD_FLOAT_SecondsSinceSystemStart", "Uptime [s]"),
            ("JSSD_FLOAT_ISR_ExecTime_us", "ISR [us]"), ("JSSD_FLOAT_ISR_Period_us", "Periodo ISR [us]"),
            ("JSSD_ENDMARKER", "")]
    _build_gui_items(n, buttons, slow)
    n.source = "(valores por defecto)"
    return n


def load_header(path: str | Path) -> BoardNames:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    # listas X(...) de senales: en el propio header y en globalData.h (src/ o la misma carpeta)
    macros = {}
    for extra in (path.parent.parent / "globalData.h", path.parent / "globalData.h"):
        if extra.is_file():
            macros.update(_signal_macros(extra.read_text(encoding="utf-8", errors="replace")))
    macros.update(_signal_macros(text))
    enums = {m.group(1): _parse_enum(_expand_macros(m.group(2), macros)) for m in _ENUM_RE.finditer(text)}
    n = BoardNames()
    obs = enums.get("JS_OberservableData", enums.get("JS_ObservableData", []))
    n.observables = [name for name, _ in obs]
    buttons = enums.get("gui_button_mapping", [])
    slow = enums.get("JS_SlowData", [])
    if not n.observables or not slow or not buttons:
        raise ValueError(f"{path}: no se encontraron los enums JS_OberservableData / JS_SlowData / gui_button_mapping")
    button_names = [b for b, _ in buttons]
    missing = [c for c in FIXED_COMMANDS if c not in button_names]
    if missing:
        raise ValueError(f"{path}: faltan en gui_button_mapping los comandos fijos {', '.join(missing)}")
    if detect_format(button_names) == "nuevo":
        _build_gui_items(n, buttons, slow)
    else:
        _build_gui_items_original(n, buttons, slow, _label_lists(text))
    n.source = str(path)
    return n


def detect_format(button_names: list[str]) -> str:
    """Formato de gui_button_mapping: "nuevo" si Error_Reset va justo despues de Stop (indice 4);
    "original" si esta al final (tras los My_Button_n), como en mz-sw (old)."""
    return "nuevo" if len(button_names) > 4 and button_names[4] == "Error_Reset" else "original"


def find_header(start: Path) -> Path | None:
    """Busca javascope.h subiendo desde ``start``: en cada carpeta, dentro de ella y en un repositorio
    ``mz-sw`` hermano (mzscope vive en su propio repositorio, normalmente junto a mz-sw)."""
    for base in [start] + list(start.parents):
        for repo in (base, base / "mz-sw"):
            for sub in ("app", "include"):   # "include": ruta antigua
                cand = repo / "vitis" / "software" / "Baremetal" / "src" / sub / "javascope.h"
                if cand.is_file():
                    return cand
    return None
