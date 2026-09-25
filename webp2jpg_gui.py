# -*- coding: utf-8 -*-
"""
WebP 批量转 JPG 工具 (带图形界面)
功能：
1. 可视化选择文件夹
2. 批量将 WebP 转换为 JPG
3. 自动处理透明背景（转为白色）
4. 默认转换成功后删除源 WebP 文件（可在界面取消勾选）
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    messagebox.showerror("缺少依赖", "请先安装 Pillow 库：\npip install Pillow")
    exit(1)

class WebpConverterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WebP 批量转 JPG 工具")
        self.root.geometry("600x450")
        self.root.resizable(False, False)
        
        self.folder_path = tk.StringVar()
        self.delete_source = tk.BooleanVar(value=True) # 默认勾选删除源文件
        self.is_running = False

        self.create_widgets()

    def create_widgets(self):
        # 1. 文件夹选择区
        frame_top = tk.Frame(self.root, pady=10)
        frame_top.pack(fill=tk.X, padx=10)

        tk.Label(frame_top, text="目标文件夹:").pack(side=tk.LEFT)
        tk.Entry(frame_top, textvariable=self.folder_path, width=40, state='readonly').pack(side=tk.LEFT, padx=5)
        tk.Button(frame_top, text="选择文件夹", command=self.select_folder).pack(side=tk.LEFT)

        # 2. 选项区
        frame_mid = tk.Frame(self.root, pady=5)
        frame_mid.pack(fill=tk.X, padx=10)
        
        tk.Checkbutton(frame_mid, text="转换成功后删除源 WebP 文件", variable=self.delete_source).pack(side=tk.LEFT)
        self.start_btn = tk.Button(frame_mid, text="开始转换", bg="#4CAF50", fg="white", width=15, command=self.start_conversion)
        self.start_btn.pack(side=tk.RIGHT)

        # 3. 日志输出区
        frame_bottom = tk.Frame(self.root, pady=10)
        frame_bottom.pack(fill=tk.BOTH, expand=True, padx=10)
        
        tk.Label(frame_bottom, text="转换日志:").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_bottom, height=15, state='disabled')
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        """线程安全的日志输出"""
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
        
        if self.is_running:
            return

        # 二次确认，防止误删
        if self.delete_source.get():
            confirm = messagebox.askyesno("危险操作确认", 
                "您勾选了【转换成功后删除源文件】。\n\n请确认目标文件夹内的图片已经备份，或您不需要保留原图。\n是否继续？")
            if not confirm:
                return

        self.is_running = True
        self.start_btn.config(state='disabled', text="转换中...")
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

        # 开启新线程运行，防止界面卡死
        threading.Thread(target=self.run_conversion, args=(folder, self.delete_source.get()), daemon=True).start()

    def run_conversion(self, folder, delete_original):
        root_path = Path(folder)
        files = sorted([p for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() == ".webp"])
        
        if not files:
            self.log("未找到任何 .webp 文件。")
            self.finish_conversion(0, 0)
            return

        self.log(f"找到 {len(files)} 个 WebP 文件，开始转换...\n")
        success_count = 0
        fail_count = 0

        for src in files:
            dst = src.with_suffix(".jpg")
            try:
                with Image.open(src) as im:
                    im.load()
                    icc = im.info.get("icc_profile")

                    # 处理透明通道
                    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                        im = im.convert("RGBA")
                        bg = Image.new("RGB", im.size, (255, 255, 255))
                        bg.paste(im, mask=im.split()[-1])
                        im = bg
                    else:
                        im = im.convert("RGB")

                    # 保存为 JPG
                    save_kwargs = {"format": "JPEG", "quality": 95, "optimize": True}
                    if icc:
                        save_kwargs["icc_profile"] = icc
                    im.save(dst, **save_kwargs)

                # 删除源文件
                if delete_original:
                    src.unlink()

                self.log(f"✓ 成功: {src.name} -> {dst.name}")
                success_count += 1

            except Exception as e:
                self.log(f"✗ 失败: {src.name} | 错误: {str(e)}")
                fail_count += 1

        self.finish_conversion(success_count, fail_count)

    def finish_conversion(self, success, fail):
        self.log(f"\n处理完成：成功 {success} 个，失败 {fail} 个。")
        self.root.after(0, lambda: self.start_btn.config(state='normal', text="开始转换"))
        self.is_running = False
        self.root.after(0, lambda: messagebox.showinfo("完成", f"转换完成！\n成功: {success} 个\n失败: {fail} 个"))

if __name__ == "__main__":
    root = tk.Tk()
    app = WebpConverterApp(root)
    root.mainloop()