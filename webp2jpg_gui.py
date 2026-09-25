# -*- coding: utf-8 -*-
"""
WebP 批量转 JPG 工具 (全能优化版)
功能：多核加速、拖拽文件夹、进度条、画质调节、智能覆盖、配置持久化、容错清理、权限占用检测
"""

import os
import sys
import json
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path

try:
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except ImportError:
    messagebox.showerror("缺少依赖", "请先安装 Pillow 库：\npip install Pillow")
    sys.exit(1)

# 尝试导入拖拽库
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ================= 配置文件管理 =================
def get_config_path():
    """获取配置文件路径，保证在打包后 exe 同目录下生成"""
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent
    return base_dir / "webp2jpg_config.json"

CONFIG_FILE = get_config_path()

def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        "delete_source": True,
        "cores": multiprocessing.cpu_count(),
        "quality": "95",
        "overwrite": "覆盖同名文件"
    }

def save_config(config):
    try:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=4), encoding='utf-8')
    except Exception:
        pass

# ================= 独立进程转换函数 =================
def convert_single(args):
    """在单独进程中执行的转换函数"""
    src_str, delete_original, quality, overwrite_strategy = args
    src = Path(src_str)
    dst = src.with_suffix(".jpg")
    
    # 1. 智能覆盖策略
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
    
    # 2. 文件占用检测
    try:
        # 尝试以追加模式打开原文件，如果不允许，说明文件被占用
        with open(src, 'a+b') as f:
            pass
    except PermissionError:
        return (str(src), "", "failed", "文件被占用或没有读取权限，跳过")
    except Exception as e:
        return (str(src), "", "failed", f"无法访问文件: {str(e)}")

    # 3. 核心转换与清理逻辑
    try:
        with Image.open(src) as im:
            try:
                im.seek(0)
            except Exception:
                pass
            
            im.load()
            icc = im.info.get("icc_profile")

            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            save_kwargs = {"format": "JPEG", "quality": int(quality), "optimize": True}
            if icc:
                save_kwargs["icc_profile"] = icc
            im.save(dst, **save_kwargs)

        if delete_original:
            src.unlink()

        return (str(src), str(dst), "success", "")
        
    except Exception as e:
        err_msg = str(e)
        if "decoder" in err_msg.lower() or "webp" in err_msg.lower():
            err_msg = f"{err_msg} (可能是损坏或不支持的WebP编码)"
        
        # 容错：清理残缺文件
        if dst.exists():
            try:
                dst.unlink()
            except Exception:
                pass
        
        return (str(src), "", "failed", err_msg)

# ================= GUI 主程序 =================
class WebpConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WebP 批量转 JPG 工具 (全能优化版)")
        self.root.geometry("850x620")
        self.root.resizable(False, False)
        
        # 读取配置
        self.config = load_config()
        
        self.folder_path = tk.StringVar()
        self.delete_source = tk.BooleanVar(value=self.config.get("delete_source", True))
        self.cpu_cores = tk.StringVar(value=str(self.config.get("cores", multiprocessing.cpu_count())))
        self.quality = tk.StringVar(value=self.config.get("quality", "95"))
        self.overwrite = tk.StringVar(value=self.config.get("overwrite", "覆盖同名文件"))
        self.is_running = False

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        # 1. 文件夹选择区 (支持拖拽)
        frame_top = tk.Frame(self.root, pady=15)
        frame_top.pack(fill=tk.X, padx=15)

        tk.Label(frame_top, text="目标文件夹:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        self.path_entry = tk.Entry(frame_top, textvariable=self.folder_path, width=60, state='readonly', font=("微软雅黑", 10))
        self.path_entry.pack(side=tk.LEFT, padx=5)
        
        if HAS_DND:
            # 注册拖拽事件
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.on_drop)
            tk.Label(frame_top, text="(支持拖拽文件夹)", font=("微软雅黑", 9), fg="gray").pack(side=tk.LEFT, padx=5)
        
        tk.Button(frame_top, text="选择文件夹", font=("微软雅黑", 10), command=self.select_folder).pack(side=tk.LEFT)

        # 2. 选项区
        frame_mid = tk.Frame(self.root, pady=10)
        frame_mid.pack(fill=tk.X, padx=15)
        
        # 第一行选项
        row1 = tk.Frame(frame_mid)
        row1.pack(fill=tk.X, pady=5)
        tk.Checkbutton(row1, text="转换成功后删除源文件", variable=self.delete_source, font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Label(row1, text="  并发核心数:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        core_values = [str(i) for i in range(1, multiprocessing.cpu_count() + 1)]
        ttk.Combobox(row1, textvariable=self.cpu_cores, values=core_values, width=4, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        # 第二行选项
        row2 = tk.Frame(frame_mid)
        row2.pack(fill=tk.X, pady=5)
        tk.Label(row2, text="JPG 质量:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        ttk.Combobox(row2, textvariable=self.quality, values=["100", "95", "85", "75", "60"], width=5, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        
        tk.Label(row2, text="  覆盖策略:", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(15, 0))
        ttk.Combobox(row2, textvariable=self.overwrite, values=["覆盖同名文件", "跳过同名文件", "重命名保存"], width=12, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        self.start_btn = tk.Button(row2, text="开始转换", bg="#4CAF50", fg="white", width=15, font=("微软雅黑", 10, "bold"), command=self.start_conversion)
        self.start_btn.pack(side=tk.RIGHT, padx=10)

        # 3. 进度条区
        frame_progress = tk.Frame(self.root, pady=5)
        frame_progress.pack(fill=tk.X, padx=15)
        self.progress = ttk.Progressbar(frame_progress, orient="horizontal", length=100, mode="determinate")
        self.progress.pack(fill=tk.X)

        # 4. 日志区域 (左右分栏)
        frame_bottom = tk.Frame(self.root, pady=10)
        frame_bottom.pack(fill=tk.BOTH, expand=True, padx=15)

        frame_left = tk.Frame(frame_bottom)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        tk.Label(frame_left, text="运行日志 (成功/进度):", font=("微软雅黑", 10)).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_left, height=15, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        frame_right = tk.Frame(frame_bottom)
        frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        tk.Label(frame_right, text="错误日志 (仅记录失败):", font=("微软雅黑", 10), fg="red").pack(anchor=tk.W)
        self.error_text = scrolledtext.ScrolledText(frame_right, height=15, state='disabled', font=("Consolas", 9), fg="red")
        self.error_text.pack(fill=tk.BOTH, expand=True)

    # ================= 事件处理 =================
    def on_drop(self, event):
        """处理拖拽文件夹事件"""
        files = self.root.tk.splitlist(event.data)
        if files:
            path = files[0]
            if os.path.isdir(path):
                self.folder_path.set(path)
            else:
                messagebox.showwarning("提示", "请拖拽文件夹，而不是文件！")

    def on_closing(self):
        """关闭窗口时保存配置"""
        self.config["delete_source"] = self.delete_source.get()
        self.config["cores"] = int(self.cpu_cores.get())
        self.config["quality"] = self.quality.get()
        self.config["overwrite"] = self.overwrite.get()
        save_config(self.config)
        self.root.destroy()

    def log_info(self, message):
        self.root.after(0, lambda: self._log(self.log_text, message))

    def log_error(self, message):
        self.root.after(0, lambda: self._log(self.error_text, message))

    def _log(self, widget, message):
        widget.config(state='normal')
        widget.insert(tk.END, message + "\n")
        widget.see(tk.END)
        widget.config(state='disabled')

    def select_folder(self):
        folder = filedialog.askdirectory(title="选择包含 WebP 的文件夹")
        if folder:
            self.folder_path.set(folder)

    def start_conversion(self):
        folder = self.folder_path.get()
        if not folder:
            messagebox.showwarning("提示", "请先选择文件夹！")
            return
        
        if not os.path.isdir(folder):
            messagebox.showerror("错误", "目标路径无效！")
            return
            
        if not os.access(folder, os.W_OK):
            messagebox.showerror("权限错误", "目标文件夹没有写入权限，请更换文件夹或以管理员身份运行！")
            return

        if self.is_running: return

        if self.delete_source.get():
            if not messagebox.askyesno("危险确认", "您勾选了删除源文件，是否继续？"):
                return

        self.is_running = True
        self.start_btn.config(state='disabled', text="转换中...")
        
        for widget in [self.log_text, self.error_text]:
            widget.config(state='normal')
            widget.delete(1.0, tk.END)
            widget.config(state='disabled')

        self.progress['value'] = 0

        threading.Thread(
            target=self.run_conversion, 
            args=(folder, self.delete_source.get(), int(self.cpu_cores.get()), self.quality.get(), self.overwrite.get()),
            daemon=True
        ).start()

    def run_conversion(self, folder, delete_original, workers, quality, overwrite_strategy):
        root_path = Path(folder)
        files = sorted([str(p) for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() == ".webp"])
        
        if not files:
            self.log_info("未找到任何 .webp 文件。")
            self.finish_conversion(0, 0, 0)
            return

        self.log_info(f"找到 {len(files)} 个 WebP 文件，开始转换...")
        self.log_info(f"参数: 质量={quality}, 覆盖策略={overwrite_strategy}, 并发数={workers}\n")
        
        # 设置进度条最大值
        self.root.after(0, lambda: self.progress.config(maximum=len(files)))

        success_count = 0
        fail_count = 0
        skip_count = 0
        tasks = [(f, delete_original, quality, overwrite_strategy) for f in files]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(convert_single, task): task for task in tasks}
            
            for future in as_completed(futures):
                src_path_str, dst_path_str, status, err = future.result()
                
                if status == "success":
                    self.log_info(f"✓ 成功: {Path(src_path_str).name} -> {Path(dst_path_str).name}")
                    success_count += 1
                elif status == "skipped":
                    self.log_info(f"➖ 跳过: {Path(src_path_str).name} ({err})")
                    skip_count += 1
                else:
                    self.log_error(f"✗ 失败: {src_path_str}\n   原因: {err}\n")
                    fail_count += 1
                
                # 更新进度条
                self.root.after(0, lambda: self.progress.step(1))

        self.finish_conversion(success_count, fail_count, skip_count)

    def finish_conversion(self, success, fail, skip):
        self.log_info(f"\n处理完成：成功 {success} 个，失败 {fail} 个，跳过 {skip} 个。")
        if fail > 0:
            self.log_info("⚠️ 有文件转换失败，请查看右侧【错误日志】排查原因。")
            
        self.root.after(0, lambda: self.start_btn.config(state='normal', text="开始转换"))
        self.is_running = False
        self.root.after(0, lambda: messagebox.showinfo("完成", f"转换完成！\n成功: {success} 个\n失败: {fail} 个\n跳过: {skip} 个"))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # 优先使用带拖拽功能的根窗口
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
        # 如果没有安装拖拽库，弹窗提示（但不影响使用）
        root.after(100, lambda: messagebox.showinfo("提示", "未检测到 tkinterdnd2 库，拖拽功能不可用。\n如需使用拖拽，请执行: pip install tkinterdnd2"))
        
    app = WebpConverterApp(root)
    root.mainloop()
