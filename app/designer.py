"""Template Designer: visual tool for creating and editing PhotoSuit SVG templates."""

from __future__ import annotations

import io
import json
import queue
import re
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any

from jinja2 import BaseLoader, Environment
from PIL import Image

from app import exif_parser, normalizer
from app.compositor import composite
from app.pipeline import _calc_canvas_width
from app.rasterizer import rasterize_svg
from app.renderer import TEMPLATES_DIR, get_default_props, load_template_config

TEMPLATE_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")

SKELETON_CONFIG = {
    "id": "",
    "name": "",
    "description": "",
    "props": [
        {"key": "border_padding", "label": "边距比例", "type": "number", "default": 0.05},
        {"key": "bg_color", "label": "背景颜色", "type": "color", "default": "#FFFFFF"},
    ],
}

SKELETON_SVG = """\
{# New template #}
{% set pad = props.border_padding | default(0.05) %}
{% set img_w = layout.image_width %}
{% set img_h = layout.image_height %}
{% set pad_x = (img_w * pad) | int %}
{% set pad_y = (img_h * pad) | int %}
{% set canvas_w = img_w + pad_x * 2 %}
{% set canvas_h = img_h + pad_y * 2 %}
<svg xmlns="http://www.w3.org/2000/svg"
     width="{{ canvas_w }}" height="{{ canvas_h }}"
     viewBox="0 0 {{ canvas_w }} {{ canvas_h }}">
    <rect width="100%" height="100%" fill="transparent" />
</svg>
"""

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
FILETYPES = [
    ("图片文件", "*.jpg *.jpeg *.png *.tiff *.tif *.webp"),
    ("所有文件", "*.*"),
]

# Debounce delay for preview rendering (ms)
_DEBOUNCE_MS = 800
# Queue polling interval (ms)
_POLL_MS = 100
# Maximum preview thumbnail dimension
_PREVIEW_MAX = 600
# Working copy max width for faster preview rendering
_WORK_COPY_WIDTH = 1600


class TemplateDesigner:
    """Visual template designer with live preview."""

    def __init__(
        self,
        parent: tk.Tk | None = None,
        templates_dir: Path | None = None,
    ) -> None:
        if parent is None:
            self._root = tk.Tk()
            self._standalone = True
        else:
            self._root = tk.Toplevel(parent)
            self._standalone = False

        self._root.title("PhotoSuit - 模板设计器")
        self._root.minsize(960, 650)

        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._current_template_id: str | None = None
        self._sample_image_path: Path | None = None
        self._sample_work_copy: Path | None = None  # downscaled working copy
        self._dirty = False
        self._preview_after_id: str | None = None
        self._render_queue: queue.Queue[Image.Image | str] = queue.Queue()
        self._preview_photo: tk.PhotoImage | None = None

        self._build_menubar()
        self._build_ui()
        self._build_status_bar()
        self._reload_template_list()
        self._poll_render_queue()

    # ── Menu bar ──────────────────────────────────────────────────

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self._root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="新建模板", command=self._new_template, accelerator="Ctrl+N")
        file_menu.add_command(label="保存", command=self._save_template, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="关闭", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        tpl_menu = tk.Menu(menubar, tearoff=0)
        tpl_menu.add_command(label="复制模板", command=self._duplicate_template)
        tpl_menu.add_command(label="删除模板", command=self._delete_template)
        menubar.add_cascade(label="模板", menu=tpl_menu)

        self._root.config(menu=menubar)
        self._root.bind("<Control-n>", lambda _: self._new_template())
        self._root.bind("<Control-s>", lambda _: self._save_template())
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Main layout ───────────────────────────────────────────────
    #
    # ┌──────────────┬────────────────────────────────────────────┐
    # │  左侧 250px   │  右侧 (弹性宽度)                           │
    # │              │  ┌──────────────────────────────────────┐  │
    # │ ┌──────────┐ │  │ Tab: 编辑  |  预览                    │  │
    # │ │模板列表   │ │  ├──────────────────────────────────────┤  │
    # │ │ Listbox  │ │  │  编辑 tab:                           │  │
    # │ │          │ │  │  - 配置编辑器 (id/name/desc)          │  │
    # │ │[新建][复制]│ │  │  - 参数定义表格                      │  │
    # │ │[删除]     │ │  │  - SVG 代码编辑器                    │  │
    # │ └──────────┘ │  │                                      │  │
    # │              │  │  预览 tab:                            │  │
    # │ ┌──────────┐ │  │  - 示例图片选择                       │  │
    # │ │参数测试   │ │  │  - 参数测试控件                       │  │
    # │ │(动态控件) │ │  │  - 实时预览缩略图                     │  │
    # │ └──────────┘ │  └──────────────────────────────────────┘  │
    # ├──────────────┴────────────────────────────────────────────┤
    # │ 状态栏                                                     │
    # └──────────────────────────────────────────────────────────┘

    def _build_ui(self) -> None:
        outer = ttk.PanedWindow(self._root, orient=tk.HORIZONTAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left panel — template list
        left = ttk.Frame(outer, width=250)
        outer.add(left, weight=0)

        # Right panel — tabbed notebook
        right = ttk.Frame(outer)
        outer.add(right, weight=1)

        self._build_left_panel(left)
        self._build_right_tabs(right)

    def _build_status_bar(self) -> None:
        self._status_var = tk.StringVar(value="就绪")
        bar = ttk.Frame(self._root)
        bar.pack(fill=tk.X, padx=4, pady=(0, 4))
        ttk.Label(bar, textvariable=self._status_var, anchor=tk.W).pack(fill=tk.X)

    # ── Left panel ────────────────────────────────────────────────

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        # Template list
        list_frame = ttk.LabelFrame(parent, text="模板列表")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 4))

        self._tpl_listbox = tk.Listbox(list_frame, exportselection=False)
        self._tpl_listbox.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._tpl_listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        btn_frame = ttk.Frame(list_frame)
        btn_frame.pack(fill=tk.X, padx=4, pady=(0, 4))
        ttk.Button(btn_frame, text="新建", command=self._new_template, width=6).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_frame, text="复制", command=self._duplicate_template, width=6).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_frame, text="删除", command=self._delete_template, width=6).pack(side=tk.LEFT)

    # ── Right tabs ────────────────────────────────────────────────

    def _build_right_tabs(self, parent: ttk.Frame) -> None:
        self._notebook = ttk.Notebook(parent)
        self._notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Edit
        edit_tab = ttk.Frame(self._notebook)
        self._notebook.add(edit_tab, text="编辑")

        # Tab 2: Preview
        preview_tab = ttk.Frame(self._notebook)
        self._notebook.add(preview_tab, text="预览")

        self._build_edit_tab(edit_tab)
        self._build_preview_tab(preview_tab)

    # ── Edit tab ──────────────────────────────────────────────────

    def _build_edit_tab(self, parent: ttk.Frame) -> None:
        # Use a PanedWindow so config area and editor can be resized
        pw = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        pw.pack(fill=tk.BOTH, expand=True)

        # Top: config + props definition (scrollable)
        config_outer = ttk.Frame(pw)
        pw.add(config_outer, weight=0)

        config_canvas = tk.Canvas(config_outer, highlightthickness=0, height=200)
        config_scrollbar = ttk.Scrollbar(config_outer, orient=tk.VERTICAL, command=config_canvas.yview)
        config_inner = ttk.Frame(config_canvas)

        config_inner.bind("<Configure>", lambda _: config_canvas.configure(scrollregion=config_canvas.bbox("all")))
        config_canvas.create_window((0, 0), window=config_inner, anchor=tk.NW)
        config_canvas.configure(yscrollcommand=config_scrollbar.set)

        config_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        config_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_config_section(config_inner)

        # Bottom: SVG code editor
        editor_frame = ttk.LabelFrame(pw, text="SVG 代码编辑器")
        pw.add(editor_frame, weight=1)
        self._build_editor(editor_frame)

    def _build_config_section(self, parent: ttk.Frame) -> None:
        config_frame = ttk.LabelFrame(parent, text="配置编辑器")
        config_frame.pack(fill=tk.X, padx=4, pady=4)

        # ID (readonly)
        row = ttk.Frame(config_frame)
        row.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(row, text="ID:", width=8, anchor=tk.E).pack(side=tk.LEFT)
        self._cfg_id_var = tk.StringVar()
        ttk.Entry(row, textvariable=self._cfg_id_var, state="readonly", width=20).pack(side=tk.LEFT, padx=4)

        # Name
        row2 = ttk.Frame(config_frame)
        row2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(row2, text="名称:", width=8, anchor=tk.E).pack(side=tk.LEFT)
        self._cfg_name_var = tk.StringVar()
        self._cfg_name_entry = ttk.Entry(row2, textvariable=self._cfg_name_var, width=20)
        self._cfg_name_entry.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        self._cfg_name_var.trace_add("write", lambda *_: self._mark_dirty())

        # Description
        row3 = ttk.Frame(config_frame)
        row3.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(row3, text="描述:", width=8, anchor=tk.E).pack(side=tk.LEFT)
        self._cfg_desc_var = tk.StringVar()
        self._cfg_desc_entry = ttk.Entry(row3, textvariable=self._cfg_desc_var, width=20)
        self._cfg_desc_entry.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        self._cfg_desc_var.trace_add("write", lambda *_: self._mark_dirty())

        # Props definition
        props_label_frame = ttk.LabelFrame(parent, text="参数定义")
        props_label_frame.pack(fill=tk.X, padx=4, pady=4)

        self._props_container = ttk.Frame(props_label_frame)
        self._props_container.pack(fill=tk.X, padx=4, pady=4)

        ttk.Button(props_label_frame, text="+ 添加参数", command=self._add_prop_row).pack(padx=4, pady=(0, 4))

        self._prop_rows: list[dict[str, Any]] = []

    def _build_editor(self, parent: ttk.Frame) -> None:
        editor_inner = ttk.Frame(parent)
        editor_inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Line numbers
        self._line_numbers = tk.Text(
            editor_inner, width=4, padx=4, takefocus=0,
            border=0, background="#f0f0f0", foreground="#999999",
            state=tk.DISABLED, wrap=tk.NONE,
            font=("Menlo", 12),
        )
        self._line_numbers.pack(side=tk.LEFT, fill=tk.Y)

        # Scrollbar
        sv = ttk.Scrollbar(editor_inner, orient=tk.VERTICAL)
        sv.pack(side=tk.RIGHT, fill=tk.Y)

        self._editor = tk.Text(
            editor_inner, wrap=tk.NONE, undo=True,
            font=("Menlo", 12),
            yscrollcommand=self._on_editor_scroll,
        )
        self._editor.pack(fill=tk.BOTH, expand=True)
        sv.config(command=self._on_scrollbar)

        self._editor.bind("<<Modified>>", self._on_editor_modified)
        self._editor.bind("<KeyRelease>", self._on_editor_key)

        # Define syntax tags
        self._editor.tag_configure("xml_tag", foreground="#0550ae")
        self._editor.tag_configure("jinja_block", foreground="#cf6800")
        self._editor.tag_configure("jinja_comment", foreground="#999999")
        self._editor.tag_configure("attr_name", foreground="#116329")
        self._editor.tag_configure("string_literal", foreground="#8a4b08")

    def _on_editor_scroll(self, *args: Any) -> None:
        """Sync line numbers with editor scrolling."""
        self._line_numbers.yview_moveto(args[0])
        return None  # type: ignore[return-value]

    def _on_scrollbar(self, *args: Any) -> None:
        self._editor.yview(*args)
        self._line_numbers.yview(*args)

    # ── Preview tab ────────────────────────────────────────────────

    def _build_preview_tab(self, parent: ttk.Frame) -> None:
        # Vertical PanedWindow so user can drag to resize params vs preview
        pw = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Top pane: sample image selector + scrollable param test
        top_pane = ttk.Frame(pw)
        pw.add(top_pane, weight=0)

        # Sample image selector (fixed height, always visible)
        sample_frame = ttk.LabelFrame(top_pane, text="示例图片")
        sample_frame.pack(fill=tk.X, pady=(0, 4))

        sample_row = ttk.Frame(sample_frame)
        sample_row.pack(fill=tk.X, padx=4, pady=4)
        self._sample_label = ttk.Label(sample_row, text="未选择", foreground="gray")
        self._sample_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(sample_row, text="选择图片", command=self._select_sample_image).pack(side=tk.RIGHT)

        # Scrollable parameter test area
        params_outer = ttk.LabelFrame(top_pane, text="参数测试")
        params_outer.pack(fill=tk.BOTH, expand=True)

        self._params_canvas = tk.Canvas(params_outer, highlightthickness=0)
        params_scrollbar = ttk.Scrollbar(params_outer, orient=tk.VERTICAL, command=self._params_canvas.yview)
        self._params_test_frame = ttk.Frame(self._params_canvas)

        self._params_test_frame.bind(
            "<Configure>",
            lambda _: self._params_canvas.configure(scrollregion=self._params_canvas.bbox("all")),
        )
        self._params_canvas_window = self._params_canvas.create_window(
            (0, 0), window=self._params_test_frame, anchor=tk.NW,
        )
        self._params_canvas.configure(yscrollcommand=params_scrollbar.set)
        # Stretch inner frame to canvas width
        self._params_canvas.bind(
            "<Configure>",
            lambda e: self._params_canvas.itemconfigure(self._params_canvas_window, width=e.width),
        )

        params_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._params_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._param_test_widgets: dict[str, tk.Variable] = {}

        # Enable mousewheel scrolling on the params canvas area
        self._params_canvas.bind("<MouseWheel>", self._forward_mousewheel)
        self._params_test_frame.bind("<MouseWheel>", self._forward_mousewheel)

        # Bottom pane: live preview (expands to fill)
        preview_frame = ttk.LabelFrame(pw, text="实时预览")
        pw.add(preview_frame, weight=1)

        self._preview_label = ttk.Label(
            preview_frame, text="选择示例图片以启用预览", anchor=tk.CENTER,
        )
        self._preview_label.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ── Template list management ──────────────────────────────────

    def _reload_template_list(self) -> None:
        self._tpl_listbox.delete(0, tk.END)
        if not self._templates_dir.exists():
            return
        for d in sorted(self._templates_dir.iterdir()):
            if d.is_dir() and (d / "config.json").exists():
                self._tpl_listbox.insert(tk.END, d.name)

    def _on_listbox_select(self, _event: tk.Event | None = None) -> None:
        sel = self._tpl_listbox.curselection()
        if not sel:
            return
        tpl_id = self._tpl_listbox.get(sel[0])
        if tpl_id == self._current_template_id:
            return
        if self._dirty:
            if not self._prompt_save():
                return
        self._load_template(tpl_id)

    def _load_template(self, tpl_id: str) -> None:
        self._current_template_id = tpl_id
        config = load_template_config(tpl_id, templates_dir=self._templates_dir)

        # Populate config fields
        self._cfg_id_var.set(config.get("id", tpl_id))
        self._cfg_name_var.set(config.get("name", ""))
        self._cfg_desc_var.set(config.get("description", ""))

        # Populate prop rows
        self._clear_prop_rows()
        for prop in config.get("props", []):
            self._add_prop_row(
                key=prop.get("key", ""),
                label=prop.get("label", ""),
                ptype=prop.get("type", "string"),
                default=str(prop.get("default", "")),
            )

        # Load SVG
        svg_path = self._templates_dir / tpl_id / "template.svg"
        svg_text = svg_path.read_text(encoding="utf-8") if svg_path.exists() else ""
        self._editor.delete("1.0", tk.END)
        self._editor.insert("1.0", svg_text)
        self._editor.edit_modified(False)
        self._editor.edit_reset()
        self._update_line_numbers()
        self._apply_syntax_highlight()

        # Build param test widgets
        self._rebuild_param_test(config)

        self._dirty = False
        self._status_var.set(f"已加载: {tpl_id}")
        self._schedule_preview()

    # ── New / Duplicate / Delete ──────────────────────────────────

    def _new_template(self) -> None:
        if self._dirty and not self._prompt_save():
            return
        tpl_id = simpledialog.askstring("新建模板", "模板 ID (英文字母开头, 字母/数字/下划线):", parent=self._root)
        if not tpl_id:
            return
        if not TEMPLATE_ID_RE.match(tpl_id):
            messagebox.showerror("错误", "无效的模板 ID，只能包含英文字母、数字和下划线，且以字母开头。", parent=self._root)
            return
        tpl_dir = self._templates_dir / tpl_id
        if tpl_dir.exists():
            messagebox.showerror("错误", f"模板 '{tpl_id}' 已存在。", parent=self._root)
            return

        tpl_dir.mkdir(parents=True)
        config = {**SKELETON_CONFIG, "id": tpl_id}
        (tpl_dir / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        (tpl_dir / "template.svg").write_text(SKELETON_SVG, encoding="utf-8")

        self._reload_template_list()
        self._select_listbox_item(tpl_id)
        self._load_template(tpl_id)
        self._status_var.set(f"已创建: {tpl_id}")

    def _duplicate_template(self) -> None:
        if not self._current_template_id:
            messagebox.showinfo("提示", "请先选择一个模板。", parent=self._root)
            return
        new_id = simpledialog.askstring(
            "复制模板", "新模板 ID:", parent=self._root,
            initialvalue=f"{self._current_template_id}_copy",
        )
        if not new_id:
            return
        if not TEMPLATE_ID_RE.match(new_id):
            messagebox.showerror("错误", "无效的模板 ID。", parent=self._root)
            return
        dest = self._templates_dir / new_id
        if dest.exists():
            messagebox.showerror("错误", f"模板 '{new_id}' 已存在。", parent=self._root)
            return

        src = self._templates_dir / self._current_template_id
        shutil.copytree(src, dest)
        # Update id in config
        cfg_path = dest / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["id"] = new_id
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

        self._reload_template_list()
        self._select_listbox_item(new_id)
        self._load_template(new_id)
        self._status_var.set(f"已复制为: {new_id}")

    def _delete_template(self) -> None:
        if not self._current_template_id:
            messagebox.showinfo("提示", "请先选择一个模板。", parent=self._root)
            return
        if not messagebox.askyesno("确认删除", f"确定删除模板 '{self._current_template_id}'？此操作不可撤销。", parent=self._root):
            return

        shutil.rmtree(self._templates_dir / self._current_template_id)
        self._current_template_id = None
        self._dirty = False
        self._editor.delete("1.0", tk.END)
        self._clear_prop_rows()
        self._cfg_id_var.set("")
        self._cfg_name_var.set("")
        self._cfg_desc_var.set("")
        self._reload_template_list()
        self._status_var.set("模板已删除")

    # ── Prop definition rows ──────────────────────────────────────

    def _clear_prop_rows(self) -> None:
        for row_data in self._prop_rows:
            row_data["frame"].destroy()
        self._prop_rows.clear()

    def _add_prop_row(
        self,
        key: str = "",
        label: str = "",
        ptype: str = "number",
        default: str = "",
    ) -> None:
        row = ttk.Frame(self._props_container)
        row.pack(fill=tk.X, pady=1)

        key_var = tk.StringVar(value=key)
        label_var = tk.StringVar(value=label)
        type_var = tk.StringVar(value=ptype)
        default_var = tk.StringVar(value=default)

        ttk.Entry(row, textvariable=key_var, width=10).pack(side=tk.LEFT, padx=1)
        ttk.Entry(row, textvariable=label_var, width=10).pack(side=tk.LEFT, padx=1)
        cb = ttk.Combobox(row, textvariable=type_var, values=["number", "color", "boolean", "string"], width=7, state="readonly")
        cb.pack(side=tk.LEFT, padx=1)
        ttk.Entry(row, textvariable=default_var, width=8).pack(side=tk.LEFT, padx=1)

        row_data: dict[str, Any] = {
            "frame": row, "key": key_var, "label": label_var,
            "type": type_var, "default": default_var,
        }

        del_btn = ttk.Button(row, text="-", width=2, command=lambda rd=row_data: self._remove_prop_row(rd))
        del_btn.pack(side=tk.LEFT, padx=1)

        self._prop_rows.append(row_data)

        # Trace changes
        for var in (key_var, label_var, type_var, default_var):
            var.trace_add("write", lambda *_, rd=row_data: self._on_prop_def_change())

    def _remove_prop_row(self, row_data: dict[str, Any]) -> None:
        row_data["frame"].destroy()
        self._prop_rows.remove(row_data)
        self._mark_dirty()
        self._rebuild_param_test_from_rows()
        self._schedule_preview()

    def _on_prop_def_change(self) -> None:
        self._mark_dirty()
        self._rebuild_param_test_from_rows()
        self._schedule_preview()

    def _collect_config(self) -> dict[str, Any]:
        props = []
        for rd in self._prop_rows:
            key = rd["key"].get().strip()
            if not key:
                continue
            ptype = rd["type"].get()
            raw_default = rd["default"].get().strip()
            # Convert default value to proper type
            if ptype == "number":
                try:
                    default: Any = float(raw_default) if "." in raw_default else int(raw_default)
                except ValueError:
                    default = 0
            elif ptype == "boolean":
                default = raw_default.lower() in ("true", "1", "yes")
            else:
                default = raw_default
            props.append({
                "key": key,
                "label": rd["label"].get().strip() or key,
                "type": ptype,
                "default": default,
            })
        return {
            "id": self._cfg_id_var.get(),
            "name": self._cfg_name_var.get(),
            "description": self._cfg_desc_var.get(),
            "props": props,
        }

    # ── Param test widgets (preview tab) ─────────────────────────

    def _forward_mousewheel(self, event: tk.Event) -> None:
        """Forward mousewheel events to the params canvas for scrolling."""
        self._params_canvas.yview_scroll(
            -1 * (event.delta // 120 or (-1 if event.delta < 0 else 1)), "units"
        )

    def _bind_mousewheel_recursive(self, widget: tk.Widget) -> None:
        """Bind mousewheel forwarding to a widget and all its children."""
        widget.bind("<MouseWheel>", self._forward_mousewheel)
        for child in widget.winfo_children():
            self._bind_mousewheel_recursive(child)

    def _rebuild_param_test(self, config: dict[str, Any]) -> None:
        for w in self._params_test_frame.winfo_children():
            w.destroy()
        self._param_test_widgets.clear()

        for prop in config.get("props", []):
            self._add_param_test_row(prop)

    def _rebuild_param_test_from_rows(self) -> None:
        config = self._collect_config()
        self._rebuild_param_test(config)

    def _add_param_test_row(self, prop: dict[str, Any]) -> None:
        key = prop["key"]
        label = prop.get("label", key)
        ptype = prop.get("type", "string")
        default = prop.get("default", "")

        row = ttk.Frame(self._params_test_frame)
        row.pack(fill=tk.X, padx=4, pady=1)
        ttk.Label(row, text=f"{label}:", width=12, anchor=tk.E).pack(side=tk.LEFT)

        if ptype == "boolean":
            var = tk.BooleanVar(value=bool(default))
            cb = ttk.Checkbutton(row, variable=var, command=self._schedule_preview)
            cb.pack(side=tk.LEFT, padx=4)
        elif ptype == "color":
            var = tk.StringVar(value=str(default))
            entry = ttk.Entry(row, textvariable=var, width=9)
            entry.pack(side=tk.LEFT, padx=4)
            ttk.Button(
                row, text="选色", width=3,
                command=lambda v=var: self._pick_color(v),
            ).pack(side=tk.LEFT)
            var.trace_add("write", lambda *_: self._schedule_preview())
        else:
            var = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=var, width=12).pack(side=tk.LEFT, padx=4)
            var.trace_add("write", lambda *_: self._schedule_preview())

        self._param_test_widgets[key] = var

        # Enable mousewheel scrolling on this row and its children
        self._bind_mousewheel_recursive(row)

    @staticmethod
    def _pick_color(var: tk.StringVar) -> None:
        result = colorchooser.askcolor(color=var.get())
        if result[1]:
            var.set(result[1])

    def _collect_test_props(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        config = self._collect_config()
        prop_map = {p["key"]: p for p in config.get("props", [])}

        for key, var in self._param_test_widgets.items():
            ptype = prop_map.get(key, {}).get("type", "string")
            val = var.get()
            if ptype == "number":
                try:
                    val = int(val)
                except ValueError:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
            props[key] = val
        return props

    # ── Sample image ──────────────────────────────────────────────

    def _select_sample_image(self) -> None:
        path = filedialog.askopenfilename(filetypes=FILETYPES, parent=self._root)
        if not path:
            return
        self._sample_image_path = Path(path)
        self._sample_label.config(text=self._sample_image_path.name, foreground="")

        # Create downscaled working copy for faster preview
        try:
            img = Image.open(self._sample_image_path)
            if img.width > _WORK_COPY_WIDTH:
                ratio = _WORK_COPY_WIDTH / img.width
                new_h = int(img.height * ratio)
                img = img.resize((_WORK_COPY_WIDTH, new_h), Image.LANCZOS)
            # Save as temporary working copy
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".jpg")
            img.save(tmp, "JPEG", quality=90)
            # Preserve EXIF from original
            try:
                import piexif
                exif_bytes = piexif.load(str(self._sample_image_path))
                piexif.insert(piexif.dump(exif_bytes), tmp)
            except Exception:
                pass
            self._sample_work_copy = Path(tmp)
        except Exception:
            self._sample_work_copy = self._sample_image_path

        self._schedule_preview()

    # ── Syntax highlighting ───────────────────────────────────────

    def _apply_syntax_highlight(self) -> None:
        for tag in ("xml_tag", "jinja_block", "jinja_comment", "attr_name", "string_literal"):
            self._editor.tag_remove(tag, "1.0", tk.END)

        text = self._editor.get("1.0", tk.END)

        patterns = [
            ("jinja_comment", r"\{#.*?#\}"),
            ("jinja_block", r"\{%.*?%\}"),
            ("jinja_block", r"\{\{.*?\}\}"),
            ("xml_tag", r"</?[a-zA-Z][a-zA-Z0-9:]*"),
            ("xml_tag", r"/?>"),
            ("attr_name", r'\b[a-zA-Z_][\w\-]*(?=\s*=)'),
            ("string_literal", r'"[^"]*"'),
        ]

        for tag_name, pattern in patterns:
            for match in re.finditer(pattern, text, re.DOTALL):
                start = f"1.0+{match.start()}c"
                end = f"1.0+{match.end()}c"
                self._editor.tag_add(tag_name, start, end)

    def _update_line_numbers(self) -> None:
        self._line_numbers.config(state=tk.NORMAL)
        self._line_numbers.delete("1.0", tk.END)
        line_count = int(self._editor.index("end-1c").split(".")[0])
        lines = "\n".join(str(i) for i in range(1, line_count + 1))
        self._line_numbers.insert("1.0", lines)
        self._line_numbers.config(state=tk.DISABLED)

    # ── Editor events ─────────────────────────────────────────────

    def _on_editor_modified(self, _event: tk.Event | None = None) -> None:
        if self._editor.edit_modified():
            self._mark_dirty()
            self._editor.edit_modified(False)
            self._update_line_numbers()
            self._apply_syntax_highlight()
            self._schedule_preview()

    def _on_editor_key(self, _event: tk.Event | None = None) -> None:
        self._update_line_numbers()

    # ── Dirty state & save ────────────────────────────────────────

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            title = self._root.title()
            if not title.endswith(" *"):
                self._root.title(title + " *")

    def _clear_dirty(self) -> None:
        self._dirty = False
        title = self._root.title()
        if title.endswith(" *"):
            self._root.title(title[:-2])

    def _prompt_save(self) -> bool:
        """Prompt to save. Returns True if OK to proceed, False to cancel."""
        result = messagebox.askyesnocancel("未保存的更改", "当前模板有未保存的更改，是否保存？", parent=self._root)
        if result is None:
            return False  # Cancel
        if result:
            self._save_template()
        return True

    def _save_template(self) -> None:
        if not self._current_template_id:
            self._status_var.set("没有模板可保存")
            return

        tpl_dir = self._templates_dir / self._current_template_id

        # Save config.json
        config = self._collect_config()
        cfg_path = tpl_dir / "config.json"
        cfg_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        # Save template.svg
        svg_text = self._editor.get("1.0", "end-1c")
        svg_path = tpl_dir / "template.svg"
        svg_path.write_text(svg_text + "\n", encoding="utf-8")

        self._clear_dirty()
        self._status_var.set(f"已保存: {self._current_template_id}")

    # ── Live preview ──────────────────────────────────────────────

    def _schedule_preview(self) -> None:
        if self._preview_after_id is not None:
            self._root.after_cancel(self._preview_after_id)
        self._preview_after_id = self._root.after(_DEBOUNCE_MS, self._trigger_preview)

    def _trigger_preview(self) -> None:
        self._preview_after_id = None
        if not self._sample_work_copy:
            return

        svg_text = self._editor.get("1.0", "end-1c")
        props = self._collect_test_props()
        sample = self._sample_work_copy

        self._status_var.set("渲染预览中...")
        threading.Thread(
            target=self._render_preview_background,
            args=(svg_text, sample, props),
            daemon=True,
        ).start()

    def _render_preview_background(
        self, svg_text: str, sample_path: Path, props: dict[str, Any]
    ) -> None:
        try:
            # Parse EXIF & normalize
            raw_exif = exif_parser.parse_exif(sample_path)
            context = normalizer.normalize_exif(raw_exif)

            # Render SVG from editor text directly (not from disk)
            env = Environment(loader=BaseLoader(), autoescape=False)
            template = env.from_string(svg_text)
            full_context = {**context, "props": props}
            rendered_svg = template.render(**full_context)

            # Rasterize
            canvas_width = _calc_canvas_width(context, props)
            frame_png = rasterize_svg(rendered_svg, output_width=canvas_width)

            # Composite in memory
            border_padding = float(props.get("border_padding", 0.05))
            bg_color = str(props.get("bg_color", "#FFFFFF"))

            original = Image.open(sample_path).convert("RGB")
            orig_w, orig_h = original.size

            frame = Image.open(io.BytesIO(frame_png)).convert("RGBA")
            canvas_w, canvas_h = frame.size

            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_color)

            if "image_offset_x" in props or "image_offset_y" in props:
                off_x = int(props.get("image_offset_x", 0))
                off_y = int(props.get("image_offset_y", 0))
                pad_x = int(orig_w * border_padding)
                pad_y = int(orig_h * border_padding)
                canvas.paste(original, (off_x + pad_x, off_y + pad_y))
            else:
                pad_x = int(orig_w * border_padding)
                pad_y = int(orig_h * border_padding)
                canvas.paste(original, (pad_x, pad_y))

            canvas.paste(frame, (0, 0), mask=frame.split()[3])

            self._render_queue.put(canvas)
        except Exception as e:
            self._render_queue.put(f"预览失败: {e}")

    def _poll_render_queue(self) -> None:
        try:
            result = self._render_queue.get_nowait()
            if isinstance(result, Image.Image):
                result.thumbnail((_PREVIEW_MAX, _PREVIEW_MAX))
                # Use tk.PhotoImage with PNG data to avoid ImageTk C extension issues
                buf = io.BytesIO()
                result.save(buf, format="PNG")
                self._preview_photo = tk.PhotoImage(data=buf.getvalue(), master=self._root)
                self._preview_label.config(image=self._preview_photo, text="")
                self._status_var.set("预览已更新")
            else:
                self._preview_label.config(image="", text=str(result))
                self._preview_photo = None
                self._status_var.set(str(result))
        except queue.Empty:
            pass
        self._root.after(_POLL_MS, self._poll_render_queue)

    # ── Helpers ───────────────────────────────────────────────────

    def _select_listbox_item(self, tpl_id: str) -> None:
        for i in range(self._tpl_listbox.size()):
            if self._tpl_listbox.get(i) == tpl_id:
                self._tpl_listbox.selection_clear(0, tk.END)
                self._tpl_listbox.selection_set(i)
                self._tpl_listbox.see(i)
                break

    def _on_close(self) -> None:
        if self._dirty:
            if not self._prompt_save():
                return
        # Clean up temp working copy
        if self._sample_work_copy and self._sample_work_copy != self._sample_image_path:
            try:
                self._sample_work_copy.unlink(missing_ok=True)
            except Exception:
                pass
        if self._standalone:
            self._root.destroy()
        else:
            self._root.destroy()

    def run(self) -> None:
        if self._standalone:
            self._root.mainloop()


def main(templates_dir: Path | None = None) -> None:
    designer = TemplateDesigner(templates_dir=templates_dir)
    designer.run()


if __name__ == "__main__":
    main()
