# -*- coding: utf-8 -*-
"""
WebP 批量转 JPG 工具 (带图形界面、多核加速、独立错误日志版)
"""

import os
import sys
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    messagebox.showerror("缺少依赖", "请先安装 Pillow 库：\npip install Pillow")
    sys.exit(1)

# ================= 独立进程转换函数 =================
def convert_single(args):
    """在单独进程中执行的转换函数"""
    src_str, delete_original = args
    src = Path(src_str)
    dst = src.with_suffix(".jpg")
    
    try:
        with Image.open(src) as im:
            im.load()
            icc = im.info.get("icc_profile")

            # 透明背景处理
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")

            save_kwargs = {"format": "JPEG", "quality": 95, "optimize": True}
            if icc:
                save_kwargs["icc_profile"] = icc
            im.save(dst, **save_kwargs)

        if delete_original:
            src.unlink()

        return (src.name, dst.name, True, "")
    except Exception as e:
        return (src.name, "", False, str(e))

# ================= GUI 主程序 =================
class WebpConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WebP 批量转 JPG 工具 (多核加速版)")
        self.root.geometry("800x550") # 增加窗口宽度以容纳左右两个日志框
        self.root.resizable(False, False)
        
        self.max_cores = multiprocessing.cpu_count()
        
        self.folder_path = tk.StringVar()
        self.delete_source = tk.BooleanVar(value=True)
        self.cpu_cores = tk.StringVar(value=str(self.max_cores))
        self.is_running = False

        self.create_widgets()

    def create_widgets(self):
        # 1. 文件夹选择区
        frame_top = tk.Frame(self.root, pady=15)
        frame_top.pack(fill=tk.X, padx=10)

        tk.Label(frame_top, text="目标文件夹:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Entry(frame_top, textvariable=self.folder_path, width=55, state='readonly', font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        tk.Button(frame_top, text="选择文件夹", font=("微软雅黑", 10), command=self.select_folder).pack(side=tk.LEFT)

        # 2. 选项区
        frame_mid = tk.Frame(self.root, pady=10)
        frame_mid.pack(fill=tk.X, padx=10)
        
        tk.Checkbutton(frame_mid, text="转换成功后删除源文件", variable=self.delete_source, font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Label(frame_mid, text="  并发核心数:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        
        core_values = [str(i) for i in range(1, self.max_cores + 1)]
        self.core_combo = ttk.Combobox(frame_mid, textvariable=self.cpu_cores, values=core_values, width=4, state="readonly", font=("微软雅黑", 10))
        self.core_combo.pack(side=tk.LEFT, padx=5)

        self.start_btn = tk.Button(frame_mid, text="开始转换", bg="#4CAF50", fg="white", width=15, font=("微软雅黑", 10, "bold"), command=self.start_conversion)
        self.start_btn.pack(side=tk.RIGHT)

        # 3. 日志区域 (改为左右分栏)
        frame_bottom = tk.Frame(self.root, pady=10)
        frame_bottom.pack(fill=tk.BOTH, expand=True, padx=10)

        # 左侧：运行日志
        frame_left = tk.Frame(frame_bottom)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        tk.Label(frame_left, text="运行日志 (成功/进度):", font=("微软雅黑", 10)).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_left, height=18, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 右侧：错误日志
        frame_right = tk.Frame(frame_bottom)
        frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        tk.Label(frame_right, text="错误日志 (仅记录失败):", font=("微软雅黑", 10), fg="red").pack(anchor=tk.W)
        self.error_text = scrolledtext.ScrolledText(frame_right, height=18, state='disabled', font=("Consolas", 9), fg="red")
        self.error_text.pack(fill=tk.BOTH, expand=True)

    # ================= 日志输出方法 =================
    def log_info(self, message):
        """输出普通信息到左侧运行日志"""
        self.root.after(0, self._log_info, message)

    def _log_info(self, message):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def log_error(self, message):
        """输出错误信息到右侧错误日志"""
        self.root.after(0, self._log_error, message)

    def _log_error(self, message):
        self.error_text.config(state='normal')
        self.error_text.insert(tk.END, message + "\n")
        self.error_text.see(tk.END)
        self.error_text.config(state='disabled')

    # ================= 业务逻辑 =================
    def select_folder(self):
        folder = filedialog.askdirectory(title="选择包含 WebP 的文件夹")
        if folder:
            self.folder_path.set(folder)

    def start_conversion(self):
        folder = self.folder_path.get()
        if not folder:
            messagebox.showwarning("提示", "请先选择文件夹！")
            return
        
        if self.is_running: return

        if self.delete_source.get():
            if not messagebox.askyesno("危险确认", "您勾选了删除源文件，是否继续？"):
                return

        self.is_running = True
        self.start_btn.config(state='disabled', text="转换中...")
        
        # 清空两个日志框
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')
        
        self.error_text.config(state='normal')
        self.error_text.delete(1.0, tk.END)
        self.error_text.config(state='disabled')

        threading.Thread(
            target=self.run_conversion, 
            args=(folder, self.delete_source.get(), int(self.cpu_cores.get())),
            daemon=True
        ).start()

    def run_conversion(self, folder, delete_original, workers):
        root_path = Path(folder)
        files = sorted([str(p) for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() == ".webp"])
        
        if not files:
            self.log_info("未找到任何 .webp 文件。")
            self.finish_conversion(0, 0)
            return

        self.log_info(f"找到 {len(files)} 个 WebP 文件，开始转换...")
        self.log_info(f"启用 {workers} 个 CPU 核心并发处理...\n")
        
        success_count = 0
        fail_count = 0
        tasks = [(f, delete_original) for f in files]

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(convert_single, task): task for task in tasks}
            
            for future in as_completed(futures):
                src_name, dst_name, success, err = future.result()
                if success:
                    self.log_info(f"✓ 成功: {src_name} -> {dst_name}")
                    success_count += 1
                else:
                    # 错误信息发往专用的错误日志框
                    error_msg = f"✗ 失败: {src_name}\n   原因: {err}"
                    self.log_error(error_msg)
                    fail_count += 1

        self.finish_conversion(success_count, fail_count)

    def finish_conversion(self, success, fail):
        self.log_info(f"\n处理完成：成功 {success} 个，失败 {fail} 个。")
        if fail > 0:
            self.log_info("⚠️ 有文件转换失败，请查看右侧【错误日志】排查原因。")
            
        self.root.after(0, lambda: self.start_btn.config(state='normal', text="开始转换"))
        self.is_running = False
        self.root.after(0, lambda: messagebox.showinfo("完成", f"转换完成！\n成功: {success} 个\n失败: {fail} 个"))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    root = tk.Tk()
    app = WebpConverterApp(root)
    root.mainloop()
