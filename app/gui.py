"""PhotoSuit Tkinter GUI — graphical entry point for image frame compositing."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, ttk

from PIL import Image, ImageTk

from app import exif_parser, normalizer, pipeline, renderer

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
FILETYPES = [
    ("图片文件", "*.jpg *.jpeg *.png *.tiff *.tif *.webp"),
    ("所有文件", "*.*"),
]


class PhotoSuitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PhotoSuit")
        self.minsize(820, 520)

        # State
        self._input_path: Path | None = None
        self._input_dir: Path | None = None
        self._output_path = tk.StringVar()
        self._status = tk.StringVar(value="就绪")
        self._template_var = tk.StringVar()
        self._templates: list[dict] = renderer.list_templates()
        self._param_widgets: dict[str, tk.Variable] = {}
        self._preview_photo: ImageTk.PhotoImage | None = None  # prevent GC
        self._task_queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self._populate_templates()
        self._poll_queue()

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Main paned window
        pw = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(pw, width=340)
        right = ttk.Frame(pw, width=420)
        pw.add(left, weight=1)
        pw.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

    # ── Left panel ───────────────────────────────────────────────────

    def _build_left(self, parent: ttk.Frame) -> None:
        # Preview
        preview_frame = ttk.LabelFrame(parent, text="图片预览")
        preview_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))

        self._preview_label = ttk.Label(
            preview_frame, text="请选择图片", anchor=tk.CENTER
        )
        self._preview_label.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # File selection
        sel_frame = ttk.Frame(parent)
        sel_frame.pack(fill=tk.X, padx=4, pady=2)

        self._btn_file = ttk.Button(
            sel_frame, text="选择图片", command=self._select_file
        )
        self._btn_file.pack(side=tk.LEFT, padx=(0, 4))

        self._btn_dir = ttk.Button(
            sel_frame, text="选择目录", command=self._select_dir
        )
        self._btn_dir.pack(side=tk.LEFT)

        self._file_info = ttk.Label(parent, text="未选择文件", foreground="gray")
        self._file_info.pack(fill=tk.X, padx=4, pady=(0, 4))

        # Output path
        out_frame = ttk.Frame(parent)
        out_frame.pack(fill=tk.X, padx=4, pady=2)

        ttk.Label(out_frame, text="输出:").pack(side=tk.LEFT)
        ttk.Entry(out_frame, textvariable=self._output_path).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )
        ttk.Button(out_frame, text="...", width=3, command=self._browse_output).pack(
            side=tk.LEFT
        )

        # Action buttons
        act_frame = ttk.Frame(parent)
        act_frame.pack(fill=tk.X, padx=4, pady=(4, 6))

        self._btn_process = ttk.Button(
            act_frame, text="处理图片", command=self._run_process
        )
        self._btn_process.pack(side=tk.LEFT, padx=(0, 4))

        self._btn_batch = ttk.Button(
            act_frame, text="批量处理", command=self._run_batch
        )
        self._btn_batch.pack(side=tk.LEFT)

    # ── Right panel ──────────────────────────────────────────────────

    def _build_right(self, parent: ttk.Frame) -> None:
        # Scrollable canvas wrapper
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        self._right_inner = ttk.Frame(canvas)

        self._right_inner.bind(
            "<Configure>",
            lambda _: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self._right_inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_right_content(self._right_inner)

    def _build_right_content(self, parent: ttk.Frame) -> None:
        # Template selector
        tpl_frame = ttk.Frame(parent)
        tpl_frame.pack(fill=tk.X, padx=4, pady=(6, 2))

        ttk.Label(tpl_frame, text="模板:").pack(side=tk.LEFT)
        self._tpl_combo = ttk.Combobox(
            tpl_frame,
            textvariable=self._template_var,
            state="readonly",
            width=24,
        )
        self._tpl_combo.pack(side=tk.LEFT, padx=4)
        self._tpl_combo.bind("<<ComboboxSelected>>", self._on_template_change)

        # Dynamic params container
        self._params_frame = ttk.LabelFrame(parent, text="模板参数")
        self._params_frame.pack(fill=tk.X, padx=4, pady=4)

        # EXIF info
        self._exif_frame = ttk.LabelFrame(parent, text="EXIF 信息")
        self._exif_frame.pack(fill=tk.X, padx=4, pady=4)

        self._exif_text = tk.Text(
            self._exif_frame, height=8, state=tk.DISABLED, wrap=tk.WORD, font=("TkDefaultFont", 11)
        )
        self._exif_text.pack(fill=tk.X, padx=4, pady=4)

        # Status bar
        ttk.Separator(parent).pack(fill=tk.X, padx=4, pady=(4, 0))
        status_frame = ttk.Frame(parent)
        status_frame.pack(fill=tk.X, padx=4, pady=4)

        ttk.Label(status_frame, text="状态:").pack(side=tk.LEFT)
        ttk.Label(status_frame, textvariable=self._status).pack(
            side=tk.LEFT, padx=4
        )

    # ── Template handling ────────────────────────────────────────────

    def _populate_templates(self) -> None:
        ids = [t["id"] for t in self._templates]
        self._tpl_combo["values"] = ids
        if ids:
            self._tpl_combo.current(0)
            self._refresh_params()

    def _on_template_change(self, _event: tk.Event | None = None) -> None:
        self._refresh_params()

    def _refresh_params(self) -> None:
        for w in self._params_frame.winfo_children():
            w.destroy()
        self._param_widgets.clear()

        tpl_id = self._template_var.get()
        if not tpl_id:
            return

        config = renderer.load_template_config(tpl_id)
        for prop in config.get("props", []):
            self._add_param_row(self._params_frame, prop)

    def _add_param_row(self, parent: ttk.Frame, prop: dict) -> None:
        key = prop["key"]
        label = prop.get("label", key)
        ptype = prop.get("type", "string")
        default = prop.get("default", "")

        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(row, text=f"{label}:", width=14, anchor=tk.E).pack(side=tk.LEFT)

        if ptype == "boolean":
            var = tk.BooleanVar(value=bool(default))
            ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT, padx=4)
        elif ptype == "color":
            var = tk.StringVar(value=str(default))
            entry = ttk.Entry(row, textvariable=var, width=10)
            entry.pack(side=tk.LEFT, padx=4)
            ttk.Button(
                row,
                text="选色",
                width=4,
                command=lambda v=var: self._pick_color(v),
            ).pack(side=tk.LEFT)
        else:
            var = tk.StringVar(value=str(default))
            ttk.Entry(row, textvariable=var, width=14).pack(side=tk.LEFT, padx=4)

        self._param_widgets[key] = var

    @staticmethod
    def _pick_color(var: tk.StringVar) -> None:
        result = colorchooser.askcolor(color=var.get())
        if result[1]:
            var.set(result[1])

    def _collect_props(self) -> dict:
        props: dict = {}
        tpl_id = self._template_var.get()
        if not tpl_id:
            return props

        config = renderer.load_template_config(tpl_id)
        prop_map = {p["key"]: p for p in config.get("props", [])}

        for key, var in self._param_widgets.items():
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

    # ── File selection ───────────────────────────────────────────────

    def _select_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=FILETYPES)
        if not path:
            return
        self._input_path = Path(path)
        self._input_dir = None
        self._file_info.config(text=f"已选: {self._input_path.name}", foreground="")

        # Default output
        out = self._input_path.with_name(
            f"{self._input_path.stem}_framed.jpg"
        )
        self._output_path.set(str(out))

        self._show_preview(self._input_path)
        self._show_exif(self._input_path)

    def _select_dir(self) -> None:
        path = filedialog.askdirectory()
        if not path:
            return
        self._input_dir = Path(path)
        self._input_path = None
        self._file_info.config(text=f"目录: {self._input_dir.name}/", foreground="")

        out = self._input_dir / "output"
        self._output_path.set(str(out))

        # Show first image preview if available
        self._preview_label.config(image="", text=f"批量模式: {self._input_dir.name}/")
        self._preview_photo = None
        self._clear_exif()

        for f in sorted(self._input_dir.iterdir()):
            if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                self._show_preview(f)
                self._show_exif(f)
                break

    def _browse_output(self) -> None:
        if self._input_dir:
            path = filedialog.askdirectory()
        else:
            path = filedialog.asksaveasfilename(
                defaultextension=".jpg",
                filetypes=[("JPEG", "*.jpg"), ("所有文件", "*.*")],
            )
        if path:
            self._output_path.set(path)

    # ── Preview & EXIF ───────────────────────────────────────────────

    def _show_preview(self, path: Path) -> None:
        try:
            img = Image.open(path)
            img.thumbnail((320, 320))
            self._preview_photo = ImageTk.PhotoImage(img)
            self._preview_label.config(image=self._preview_photo, text="")
        except Exception:
            self._preview_label.config(image="", text="无法预览")
            self._preview_photo = None

    def _show_exif(self, path: Path) -> None:
        try:
            raw = exif_parser.parse_exif(path)
            norm = normalizer.normalize_exif(raw)
            exif = norm.get("exif", {})
            lines = [
                f"相机: {exif.get('make', '-')} {exif.get('model', '-')}",
                f"镜头: {exif.get('lens_model', '-')}",
                f"焦距: {exif.get('focal_length', '-')}",
                f"光圈: {exif.get('aperture', '-')}",
                f"快门: {exif.get('exposure_time', '-')}",
                f"ISO:  {exif.get('iso', '-')}",
                f"日期: {exif.get('datetime_original', '-')}",
            ]
            self._set_exif_text("\n".join(lines))
        except Exception as e:
            self._set_exif_text(f"读取 EXIF 失败: {e}")

    def _set_exif_text(self, text: str) -> None:
        self._exif_text.config(state=tk.NORMAL)
        self._exif_text.delete("1.0", tk.END)
        self._exif_text.insert("1.0", text)
        self._exif_text.config(state=tk.DISABLED)

    def _clear_exif(self) -> None:
        self._set_exif_text("")

    # ── Processing ───────────────────────────────────────────────────

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._btn_process.config(state=state)
        self._btn_batch.config(state=state)
        self._btn_file.config(state=state)
        self._btn_dir.config(state=state)

    def _run_process(self) -> None:
        if not self._input_path:
            self._status.set("请先选择一张图片")
            return
        output = self._output_path.get().strip()
        if not output:
            self._status.set("请指定输出路径")
            return

        tpl_id = self._template_var.get() or "default_white"
        props = self._collect_props()

        self._set_buttons_enabled(False)
        self._status.set("处理中...")

        def task() -> None:
            try:
                pipeline.process_image(
                    self._input_path, output, template_id=tpl_id, **props
                )
                self._task_queue.put(f"处理完成: {Path(output).name}")
            except Exception as e:
                self._task_queue.put(f"处理失败: {e}")

        threading.Thread(target=task, daemon=True).start()

    def _run_batch(self) -> None:
        if not self._input_dir:
            self._status.set("请先选择一个目录")
            return
        output = self._output_path.get().strip()
        if not output:
            self._status.set("请指定输出目录")
            return

        tpl_id = self._template_var.get() or "default_white"
        props = self._collect_props()

        self._set_buttons_enabled(False)
        self._status.set("批量处理中...")

        def task() -> None:
            try:
                results = pipeline.batch_process(
                    self._input_dir, output, template_id=tpl_id, **props
                )
                self._task_queue.put(f"批量完成: 共处理 {len(results)} 张图片")
            except Exception as e:
                self._task_queue.put(f"批量处理失败: {e}")

        threading.Thread(target=task, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            msg = self._task_queue.get_nowait()
            self._status.set(msg)
            self._set_buttons_enabled(True)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


def main() -> None:
    app = PhotoSuitApp()
    app.mainloop()


if __name__ == "__main__":
    main()
