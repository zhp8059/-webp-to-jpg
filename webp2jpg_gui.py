# -*- coding: utf-8 -*-
"""
通用图片格式转换工具 (终极版 - 支持多格式输入与多格式输出)
"""

import sys
import json
import re
import io
import subprocess
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from PIL import Image, ImageFile, ImageTk
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except ImportError:
    messagebox.showerror("缺少依赖", "请先安装 Pillow 库：\npip install Pillow")
    sys.exit(1)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ================= 格式与质量配置 =================
SUPPORTED_INPUT_EXTENSIONS = {'.webp', '.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.gif'}
OUTPUT_FORMATS = ["JPG", "PNG", "WEBP", "BMP"]

QUALITY_OPTIONS = [
    "极高画质 (原图100%质量)",
    "高画质 (原图95%质量)",
    "中画质 (原图85%质量)",
    "低画质 (原图75%质量)",
    "极小体积 (原图60%质量)"
]
QUALITY_MAP = {
    "极高画质 (原图100%质量)": 100,
    "高画质 (原图95%质量)": 95,
    "中画质 (原图85%质量)": 85,
    "低画质 (原图75%质量)": 75,
    "极小体积 (原图60%质量)": 60
}

# ================= 配置文件管理 =================
def get_config_path() -> Path:
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent
    return base_dir / "img_converter_config.json"

CONFIG_FILE = get_config_path()

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        "delete_source": True,
        "cores": multiprocessing.cpu_count(),
        "quality": "高画质 (原图95%质量)",
        "overwrite": "覆盖同名文件",
        "output_format": "JPG"
    }

def save_config(config: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding='utf-8')
    except Exception:
        pass

# ================= 独立进程转换函数 =================
def convert_single(args):
    src_str, delete_original, quality_num, overwrite_strategy, target_format = args
    src = Path(src_str)
    
    # 动态决定后缀名
    ext = ".jpg" if target_format.upper() == "JPG" else f".{target_format.lower()}"
    dst = src.with_suffix(ext)
    
    # 智能覆盖策略
    if dst.exists():
        if overwrite_strategy == "跳过同名文件":
            return (str(src), str(dst), "skipped", "文件已存在，跳过")
        elif overwrite_strategy == "重命名保存":
            counter = 1
            while True:
                new_dst = dst.with_name(f"{dst.stem}_{counter}{dst.suffix}")
                if not new_dst.exists():
                    dst = new_dst
                    break
                counter += 1
    
    # 文件占用检测
    try:
        with open(src, 'a+b') as f:
            pass
    except PermissionError:
        return (str(src), "", "failed", "文件被占用或没有读取权限，跳过")
    except Exception as e:
        return (str(src), "", "failed", f"无法访问文件: {str(e)}")

    try:
        with Image.open(src) as im:
            try:
                im.seek(0)
            except Exception:
                pass
            
            im.load()
            icc = im.info.get("icc_profile")

            # 透明背景处理（仅当转换为不支持透明的格式时）
            if target_format.upper() in ["JPG", "JPEG", "BMP"]:
                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))
                    bg.paste(im, mask=im.split()[-1])
                    im = bg
                else:
                    im = im.convert("RGB")
            else:
                # PNG, WEBP 支持透明
                if im.mode not in ("RGBA", "RGB", "L"):
                    im = im.convert("RGBA")

            # 构建保存参数
            save_kwargs = {"format": target_format.upper()}
            if target_format.upper() in ["JPG", "JPEG", "WEBP"]:
                save_kwargs["quality"] = int(quality_num)
                save_kwargs["optimize"] = True
            if icc and target_format.upper() in ["JPG", "JPEG", "WEBP"]:
                save_kwargs["icc_profile"] = icc
                
            im.save(dst, **save_kwargs)

        if delete_original:
            src.unlink()

        return (str(src), str(dst), "success", "")
        
    except Exception as e:
        err_msg = str(e)
        
        # 容错清理
        if dst.exists():
            try:
                dst.unlink()
            except Exception:
                pass
        
        return (str(src), "", "failed", err_msg)

# ================= GUI 主程序 =================
class UniversalConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("通用图片格式转换工具 (终极版)")
        self.root.geometry("920x750")
        self.root.resizable(False, False)
        
        self.config = load_config()
        self.file_queue: list[Path] = []
        self.is_running = False
        self.cancel_event = threading.Event()

        self.delete_source = tk.BooleanVar(value=self.config.get("delete_source", True))
        self.cpu_cores = tk.StringVar(value=str(self.config.get("cores", multiprocessing.cpu_count())))
        self.output_format = tk.StringVar(value=self.config.get("output_format", "JPG"))
        
        quality_val = self.config.get("quality", "高画质 (原图95%质量)")
        if quality_val not in QUALITY_MAP: quality_val = "高画质 (原图95%质量)"
        self.quality = tk.StringVar(value=quality_val)
        self.overwrite = tk.StringVar(value=self.config.get("overwrite", "覆盖同名文件"))

        self.create_widgets()
        self.update_quality_state() # 初始化时根据格式刷新质量下拉框状态
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        # 1. 任务队列区
        frame_queue = tk.LabelFrame(self.root, text=" 任务队列 (支持拖拽文件夹或图片文件) ", font=("微软雅黑", 10), pady=5, padx=5)
        frame_queue.pack(fill=tk.X, padx=15, pady=10)
        
        self.listbox = tk.Listbox(frame_queue, height=5, font=("微软雅黑", 9), selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.listbox.bind("<Double-Button-1>", self.preview_conversion_effect)
        
        scrollbar = tk.Scrollbar(frame_queue, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        
        frame_queue_btns = tk.Frame(frame_queue)
        frame_queue_btns.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        tk.Button(frame_queue_btns, text="添加文件/文件夹", font=("微软雅黑", 9), width=15, command=self.select_paths).pack(pady=2)
        tk.Button(frame_queue_btns, text="预览效果", font=("微软雅黑", 9), width=15, fg="blue", command=self.preview_conversion_effect).pack(pady=2)
        tk.Button(frame_queue_btns, text="移除选中", font=("微软雅黑", 9), width=15, command=self.remove_selected).pack(pady=2)
        tk.Button(frame_queue_btns, text="清空队列", font=("微软雅黑", 9), width=15, command=self.clear_queue).pack(pady=2)

        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.on_drop)

        # 2. 选项区
        frame_options = tk.LabelFrame(self.root, text=" 转换设置 ", font=("微软雅黑", 10), pady=5, padx=5)
        frame_options.pack(fill=tk.X, padx=15, pady=5)

        # 第一行：输出格式、删除源文件、核心数
        row1 = tk.Frame(frame_options)
        row1.pack(fill=tk.X, pady=5)
        
        tk.Label(row1, text="输出格式:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        fmt_combo = ttk.Combobox(row1, textvariable=self.output_format, values=OUTPUT_FORMATS, width=8, state="readonly", font=("微软雅黑", 10))
        fmt_combo.pack(side=tk.LEFT, padx=5)
        fmt_combo.bind("<<ComboboxSelected>>", self.on_format_change)
        
        tk.Checkbutton(row1, text="转换成功后删除源文件", variable=self.delete_source, font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(15, 0))
        tk.Label(row1, text="  并发核心数:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        ttk.Combobox(row1, textvariable=self.cpu_cores, values=[str(i) for i in range(1, multiprocessing.cpu_count() + 1)], width=4, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        # 第二行：质量、覆盖策略
        row2 = tk.Frame(frame_options)
        row2.pack(fill=tk.X, pady=5)
        tk.Label(row2, text="质量:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        
        self.quality_combo = ttk.Combobox(row2, textvariable=self.quality, values=QUALITY_OPTIONS, width=22, state="readonly", font=("微软雅黑", 10))
        self.quality_combo.pack(side=tk.LEFT, padx=5)
        self.quality_combo.bind("<<ComboboxSelected>>", self.on_option_change)
        
        tk.Label(row2, text="  覆盖策略:", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(15, 0))
        ttk.Combobox(row2, textvariable=self.overwrite, values=["覆盖同名文件", "跳过同名文件", "重命名保存"], width=12, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        # 3. 操作区
        frame_action = tk.Frame(self.root, pady=10)
        frame_action.pack(fill=tk.X, padx=15)

        self.progress = ttk.Progressbar(frame_action, orient="horizontal", length=400, mode="determinate")
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.btn_cancel = tk.Button(frame_action, text="取消", bg="#f44336", fg="white", width=8, font=("微软雅黑", 10, "bold"), state=tk.DISABLED, command=self.cancel_conversion)
        self.btn_cancel.pack(side=tk.RIGHT, padx=5)

        self.btn_start = tk.Button(frame_action, text="开始转换", bg="#4CAF50", fg="white", width=12, font=("微软雅黑", 10, "bold"), command=self.start_conversion)
        self.btn_start.pack(side=tk.RIGHT, padx=5)

        # 4. 日志区域
        frame_bottom = tk.Frame(self.root, pady=5)
        frame_bottom.pack(fill=tk.BOTH, expand=True, padx=15)

        frame_left = tk.Frame(frame_bottom)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        tk.Label(frame_left, text="运行日志 (双击可预览):", font=("微软雅黑", 10)).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_left, height=12, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.bind("<Double-Button-1>", self.on_log_double_click)

        frame_right = tk.Frame(frame_bottom)
        frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        frame_right_header = tk.Frame(frame_right)
        frame_right_header.pack(fill=tk.X)
        tk.Label(frame_right_header, text="错误日志 (双击打开文件位置):", font=("微软雅黑", 10), fg="red").pack(side=tk.LEFT)
        tk.Button(frame_right_header, text="导出日志", font=("微软雅黑", 8), command=self.export_logs).pack(side=tk.RIGHT)
        
        self.error_text = scrolledtext.ScrolledText(frame_right, height=12, state='disabled', font=("Consolas", 9), fg="red")
        self.error_text.pack(fill=tk.BOTH, expand=True)
        self.error_text.bind("<Double-Button-1>", self.on_error_double_click)

    # ================= 事件与联动 =================
    def update_quality_state(self):
        """根据输出格式自动禁用/启用质量选项"""
        fmt = self.output_format.get().upper()
        if fmt in ["JPG", "WEBP"]:
            self.quality_combo.config(state="readonly")
        else:
            self.quality_combo.config(state="disabled")

    def on_format_change(self, event=None):
        self.update_quality_state()
        if self.listbox.curselection():
            self.root.after(300, self.preview_conversion_effect)

    def on_option_change(self, *args):
        if self.listbox.curselection():
            self.root.after(300, self.preview_conversion_effect)

    # ================= 队列与拖拽 =================
    def on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        files_to_add = []
        
        for p in paths:
            path = Path(p)
            if path.is_dir():
                for ext in SUPPORTED_INPUT_EXTENSIONS:
                    files_to_add.extend(list(path.rglob(f"*{ext}")))
                    files_to_add.extend(list(path.rglob(f"*{ext.upper()}")))
            elif path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_EXTENSIONS:
                files_to_add.append(path)
                
        if files_to_add:
            self.add_paths_to_queue(files_to_add)

    def select_paths(self):
        # 支持选择多种格式
        filetypes = [("图片文件", " ".join(f"*{ext}" for ext in SUPPORTED_INPUT_EXTENSIONS)), ("所有文件", "*.*")]
        files = filedialog.askopenfilenames(title="选择图片文件", filetypes=filetypes)
        if files:
            paths = [Path(f) for f in files]
            self.add_paths_to_queue(paths)

    def add_paths_to_queue(self, paths: list[Path]):
        added = 0
        for path in paths:
            if path not in self.file_queue:
                self.file_queue.append(path)
                self.listbox.insert(tk.END, str(path))
                added += 1
        if added > 0:
            self.log_info(f"已添加 {added} 个文件到任务队列")

    def remove_selected(self):
        selected = self.listbox.curselection()
        for index in reversed(selected):
            self.listbox.delete(index)
            del self.file_queue[index]

    def clear_queue(self):
        self.listbox.delete(0, tk.END)
        self.file_queue.clear()

    # ================= 预览功能 =================
    def preview_conversion_effect(self, event=None):
        selected = self.listbox.curselection()
        if not selected:
            if not self.file_queue:
                messagebox.showinfo("提示", "任务队列为空，请先添加文件。")
                return
            index = 0
            self.listbox.selection_set(0)
        else:
            index = selected[0]

        src_path = self.file_queue[index]
        if not src_path.exists():
            messagebox.showerror("预览失败", f"文件不存在：\n{src_path}")
            return

        target_format = self.output_format.get().upper()
        try:
            img_orig = Image.open(src_path)
            img_orig.load()
            orig_w, orig_h = img_orig.size
            orig_size_kb = src_path.stat().st_size / 1024

            im_sim = img_orig.copy()
            # 透明处理逻辑
            if target_format in ["JPG", "BMP"]:
                if im_sim.mode in ("RGBA", "LA") or (im_sim.mode == "P" and "transparency" in im_sim.info):
                    im_sim = im_sim.convert("RGBA")
                    bg = Image.new("RGB", im_sim.size, (255, 255, 255))
                    bg.paste(im_sim, mask=im_sim.split()[-1])
                    im_sim = bg
                else:
                    im_sim = im_sim.convert("RGB")
            else:
                if im_sim.mode not in ("RGBA", "RGB", "L"):
                    im_sim = im_sim.convert("RGBA")

            buffer = io.BytesIO()
            save_kwargs = {"format": target_format}
            if target_format in ["JPG", "WEBP"]:
                quality_num = QUALITY_MAP.get(self.quality.get(), 95)
                save_kwargs["quality"] = int(quality_num)
                save_kwargs["optimize"] = True
                
            im_sim.save(buffer, **save_kwargs)
            buffer.seek(0)
            sim_size_kb = len(buffer.getvalue()) / 1024

            img_orig.thumbnail((400, 400))
            img_sim_display = Image.open(buffer)
            img_sim_display.thumbnail((400, 400))

            top = tk.Toplevel(self.root)
            top.title(f"预览 - {src_path.name} -> {target_format}")
            top.geometry("900x600")
            top.resizable(False, False)
            
            frame_left = tk.Frame(top, relief=tk.GROOVE, borderwidth=1)
            frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
            tk.Label(frame_left, text=f"原图 ({src_path.suffix.upper()})", font=("微软雅黑", 12, "bold"), fg="blue").pack(pady=5)
            tk_img_orig = ImageTk.PhotoImage(img_orig)
            lbl_orig = tk.Label(frame_left, image=tk_img_orig)
            lbl_orig.image = tk_img_orig
            lbl_orig.pack(expand=True)
            tk.Label(frame_left, text=f"尺寸: {orig_w}x{orig_h} | 大小: {orig_size_kb:.1f} KB", font=("微软雅黑", 9)).pack(pady=5)
            
            frame_right = tk.Frame(top, relief=tk.GROOVE, borderwidth=1)
            frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)
            tk.Label(frame_right, text=f"预览效果 ({target_format})", font=("微软雅黑", 12, "bold"), fg="green").pack(pady=5)
            tk_img_sim = ImageTk.PhotoImage(img_sim_display)
            lbl_sim = tk.Label(frame_right, image=tk_img_sim)
            lbl_sim.image = tk_img_sim
            lbl_sim.pack(expand=True)
            tk.Label(frame_right, text=f"预估大小: {sim_size_kb:.1f} KB", font=("微软雅黑", 9)).pack(pady=5)
            
            bottom_frame = tk.Frame(top)
            bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=10)
            if orig_size_kb > 0:
                ratio = (1 - sim_size_kb / orig_size_kb) * 100
                if ratio >= 0:
                    info = f"预估体积缩减 {ratio:.1f}%"
                    color = "blue"
                else:
                    info = f"预估体积增大 {abs(ratio):.1f}%"
                    color = "red"
                tk.Label(bottom_frame, text=info, font=("微软雅黑", 12, "bold"), fg=color).pack()
            
            tk.Label(bottom_frame, text="* 注：此为内存模拟预览，与最终实际转换结果可能存在细微差异。", font=("微软雅黑", 8), fg="gray").pack(pady=(5, 0))
            top.bind("<Destroy>", lambda e: buffer.close() if not buffer.closed else None)

        except Exception as e:
            messagebox.showerror("预览失败", f"无法生成预览效果：\n{e}")

    # ================= 日志与导出 =================
    def log_info(self, message):
        self.root.after(0, lambda: self._log(self.log_text, message))

    def log_error(self, message):
        self.root.after(0, lambda: self._log(self.error_text, message))

    def _log(self, widget, message):
        widget.config(state='normal')
        widget.insert(tk.END, message + "\n")
        widget.see(tk.END)
        widget.config(state='disabled')

    def export_logs(self):
        info_content = self.log_text.get(1.0, tk.END).strip()
        error_content = self.error_text.get(1.0, tk.END).strip()
        if not info_content and not error_content:
            messagebox.showinfo("提示", "当前没有日志可以导出。")
            return
        file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("文本文件", "*.txt")], initialfile="converter_log.txt")
        if file_path:
            try:
                Path(file_path).write_text(f"========== 运行日志 ==========\n{info_content}\n\n========== 错误日志 ==========\n{error_content}\n", encoding='utf-8')
                messagebox.showinfo("成功", "日志已成功导出！")
            except Exception as e:
                messagebox.showerror("错误", f"导出日志失败: {e}")

    def on_log_double_click(self, event):
        try:
            index = self.log_text.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])
            line_text = self.log_text.get(f"{line_num}.0", f"{line_num}.end")
            match = re.search(r'✓ 成功: (.*?) -> (.*?)$', line_text)
            if match:
                self.show_comparison(match.group(1).strip(), match.group(2).strip())
        except Exception:
            pass

    def show_comparison(self, src_path_str, dst_path_str):
        src_path, dst_path = Path(src_path_str), Path(dst_path_str)
        if not src_path.exists() or not dst_path.exists():
            messagebox.showerror("预览失败", "源文件或目标文件不存在。")
            return
        try:
            img_src, img_dst = Image.open(src_path), Image.open(dst_path)
            img_src.thumbnail((400, 400))
            img_dst.thumbnail((400, 400))
            top = tk.Toplevel(self.root)
            top.title("对比预览 - 原图 vs 转换后")
            top.geometry("900x600")
            top.resizable(False, False)
            
            for img, path, side, title, color in [(img_src, src_path, tk.LEFT, "原图", "blue"), (img_dst, dst_path, tk.RIGHT, "转换后", "green")]:
                frame = tk.Frame(top, relief=tk.GROOVE, borderwidth=1)
                frame.pack(side=side, fill=tk.BOTH, expand=True, padx=10, pady=10)
                tk.Label(frame, text=title, font=("微软雅黑", 12, "bold"), fg=color).pack(pady=5)
                tk_img = ImageTk.PhotoImage(img)
                lbl = tk.Label(frame, image=tk_img)
                lbl.image = tk_img
                lbl.pack(expand=True)
                tk.Label(frame, text=f"大小: {path.stat().st_size / 1024:.1f} KB", font=("微软雅黑", 9)).pack(pady=5)
            
            src_size, dst_size = src_path.stat().st_size, dst_path.stat().st_size
            if src_size > 0:
                ratio = (1 - dst_size / src_size) * 100
                tk.Label(top, text=f"实际体积变化: {'缩减' if ratio >= 0 else '增大'} {abs(ratio):.1f}%", font=("微软雅黑", 12, "bold"), fg="blue" if ratio >= 0 else "red").pack(side=tk.BOTTOM, pady=10)
        except Exception as e:
            messagebox.showerror("预览失败", f"无法打开图片进行对比：\n{e}")

    def on_error_double_click(self, event):
        try:
            index = self.error_text.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])
            line_text = self.error_text.get(f"{line_num}.0", f"{line_num}.end")
            match = re.search(r'✗ 失败: (.*?)(?:\s*$)', line_text)
            if match:
                path = Path(match.group(1).strip())
                if path.exists():
                    subprocess.Popen(f'explorer /select,"{path.resolve()}"')
                else:
                    messagebox.showwarning("提示", f"文件不存在：\n{path}")
        except Exception:
            pass

    # ================= 转换核心逻辑 =================
    def cancel_conversion(self):
        self.cancel_event.set()
        self.btn_cancel.config(state=tk.DISABLED, text="正在取消...")
        self.log_info("\n⚠️ 收到取消信号，正在停止后续任务...")

    def on_closing(self):
        self.config["delete_source"] = self.delete_source.get()
        self.config["cores"] = int(self.cpu_cores.get())
        self.config["quality"] = self.quality.get()
        self.config["overwrite"] = self.overwrite.get()
        self.config["output_format"] = self.output_format.get()
        save_config(self.config)
        self.root.destroy()

    def start_conversion(self):
        if not self.file_queue:
            messagebox.showwarning("提示", "任务队列为空！")
            return
        if self.is_running: return
        if self.delete_source.get():
            if not messagebox.askyesno("危险确认", "您勾选了删除源文件，是否继续？"):
                return

        self.is_running = True
        self.cancel_event.clear()
        self.btn_start.config(state='disabled', text="转换中...")
        self.btn_cancel.config(state=tk.NORMAL, text="取消")
        for widget in [self.log_text, self.error_text]:
            widget.config(state='normal')
            widget.delete(1.0, tk.END)
            widget.config(state='disabled')
        self.progress['value'] = 0

        quality_num = QUALITY_MAP.get(self.quality.get(), 95)
        target_fmt = self.output_format.get()

        threading.Thread(
            target=self.run_conversion, 
            args=(list(self.file_queue), self.delete_source.get(), int(self.cpu_cores.get()), quality_num, self.overwrite.get(), target_fmt),
            daemon=True
        ).start()

    def run_conversion(self, files: list[Path], delete_original, workers, quality_num, overwrite_strategy, target_format):
        if not files:
            self.finish_conversion(0, 0, 0, cancelled=False)
            return

        self.log_info(f"共 {len(files)} 个文件，目标格式: {target_format}，准备处理...")
        self.root.after(0, lambda: self.progress.config(maximum=len(files)))

        success_count, fail_count, skip_count = 0, 0, 0
        cancelled = False
        
        batch_size = 500
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for i in range(0, len(files), batch_size):
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                
                batch_files = files[i:i+batch_size]
                tasks = [(str(f), delete_original, quality_num, overwrite_strategy, target_format) for f in batch_files]
                futures = {executor.submit(convert_single, task): task for task in tasks}
                
                for future in as_completed(futures):
                    if self.cancel_event.is_set():
                        cancelled = True
                        break
                    src_path_str, dst_path_str, status, err = future.result()
                    if status == "success":
                        self.log_info(f"✓ 成功: {src_path_str} -> {dst_path_str}")
                        success_count += 1
                    elif status == "skipped":
                        self.log_info(f"➖ 跳过: {Path(src_path_str).name}")
                        skip_count += 1
                    else:
                        self.log_error(f"✗ 失败: {src_path_str}\n   原因: {err}\n")
                        fail_count += 1
                    self.root.after(0, lambda: self.progress.step(1))

                if cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        self.finish_conversion(success_count, fail_count, skip_count, cancelled)

    def finish_conversion(self, success, fail, skip, cancelled):
        msg = f"\n⚠️ 转换已取消。已完成：" if cancelled else f"\n处理完成："
        self.log_info(f"{msg}成功 {success} 个，失败 {fail} 个，跳过 {skip} 个。")
        if fail > 0:
            self.log_info("⚠️ 有文件转换失败，请查看右侧【错误日志】。")
        self.root.after(0, lambda: self.btn_start.config(state='normal', text="开始转换"))
        self.root.after(0, lambda: self.btn_cancel.config(state=tk.DISABLED, text="取消"))
        self.is_running = False
        self.root.after(0, lambda: messagebox.showinfo("完成", f"转换结束！\n成功: {success} 个\n失败: {fail} 个\n跳过: {skip} 个"))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        root.after(100, lambda: messagebox.showinfo("提示", "未检测到 tkinterdnd2 库，拖拽功能不可用。"))
    app = UniversalConverterApp(root)
    root.mainloop()
