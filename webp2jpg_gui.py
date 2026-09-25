# -*- coding: utf-8 -*-
"""
WebP 批量转 JPG 工具 (带图形界面、多核加速下拉菜单版)
"""

import os
import sys
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk # 引入 ttk 控件库
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
        self.root.geometry("650x520")
        self.root.resizable(False, False)
        
        # 获取最大 CPU 核心数
        self.max_cores = multiprocessing.cpu_count()
        
        self.folder_path = tk.StringVar()
        self.delete_source = tk.BooleanVar(value=True)
        # 使用 StringVar 适配 Combobox
        self.cpu_cores = tk.StringVar(value=str(self.max_cores))
        self.is_running = False

        self.create_widgets()

    def create_widgets(self):
        # 文件夹选择
        frame_top = tk.Frame(self.root, pady=15)
        frame_top.pack(fill=tk.X, padx=10)

        tk.Label(frame_top, text="目标文件夹:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Entry(frame_top, textvariable=self.folder_path, width=45, state='readonly', font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        tk.Button(frame_top, text="选择文件夹", font=("微软雅黑", 10), command=self.select_folder).pack(side=tk.LEFT)

        # 选项区域 (加大间距和字体，方便点击)
        frame_mid = tk.Frame(self.root, pady=10)
        frame_mid.pack(fill=tk.X, padx=10)
        
        tk.Checkbutton(frame_mid, text="转换成功后删除源文件", variable=self.delete_source, font=("微软雅黑", 10)).pack(side=tk.LEFT)
        
        tk.Label(frame_mid, text="  并发核心数:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        
        # 【核心修改】将原来的 Spinbox 替换为 Combobox 下拉框，方便点选
        core_values = [str(i) for i in range(1, self.max_cores + 1)]
        self.core_combo = ttk.Combobox(frame_mid, textvariable=self.cpu_cores, values=core_values, width=4, state="readonly", font=("微软雅黑", 10))
        self.core_combo.pack(side=tk.LEFT, padx=5)

        self.start_btn = tk.Button(frame_mid, text="开始转换", bg="#4CAF50", fg="white", width=15, font=("微软雅黑", 10, "bold"), command=self.start_conversion)
        self.start_btn.pack(side=tk.RIGHT)

        # 日志区域
        frame_bottom = tk.Frame(self.root, pady=10)
        frame_bottom.pack(fill=tk.BOTH, expand=True, padx=10)
        
        tk.Label(frame_bottom, text="转换日志:", font=("微软雅黑", 10)).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_bottom, height=16, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        self.root.after(0, self._log, message)

    def _log(self, message):
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

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
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

        threading.Thread(
            target=self.run_conversion, 
            args=(folder, self.delete_source.get(), int(self.cpu_cores.get())), # 转成 int 传给后台
            daemon=True
        ).start()

    def run_conversion(self, folder, delete_original, workers):
        root_path = Path(folder)
        files = sorted([str(p) for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() == ".webp"])
        
        if not files:
            self.log("未找到任何 .webp 文件。")
            self.finish_conversion(0, 0)
            return

        self.log(f"找到 {len(files)} 个 WebP 文件，开始转换...")
        self.log(f"启用 {workers} 个 CPU 核心并发处理...\n")
        
        success_count = 0
        fail_count = 0
        tasks = [(f, delete_original) for f in files]

        # 使用进程池进行并行处理
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(convert_single, task): task for task in tasks}
            
            for future in as_completed(futures):
                src_name, dst_name, success, err = future.result()
                if success:
                    self.log(f"✓ 成功: {src_name} -> {dst_name}")
                    success_count += 1
                else:
                    self.log(f"✗ 失败: {src_name} | 错误: {err}")
                    fail_count += 1

        self.finish_conversion(success_count, fail_count)

    def finish_conversion(self, success, fail):
        self.log(f"\n处理完成：成功 {success} 个，失败 {fail} 个。")
        self.root.after(0, lambda: self.start_btn.config(state='normal', text="开始转换"))
        self.is_running = False
        self.root.after(0, lambda: messagebox.showinfo("完成", f"转换完成！\n成功: {success} 个\n失败: {fail} 个"))

if __name__ == "__main__":
    # Windows 下打包多进程程序必须加上这一行
    multiprocessing.freeze_support()
    
    root = tk.Tk()
    app = WebpConverterApp(root)
    root.mainloop()
