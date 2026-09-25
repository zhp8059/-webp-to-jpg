# -*- coding: utf-8 -*-
"""
WebP 批量转 JPG 工具 (多任务队列/全功能终极版)
新增：任务队列、取消按钮、双击错误日志打开路径、日志导出、多文件夹拖拽、分批处理
"""

import os
import sys
import json
import re
import subprocess
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

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ================= 配置文件管理 =================
def get_config_path():
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
    src_str, delete_original, quality, overwrite_strategy = args
    src = Path(src_str)
    dst = src.with_suffix(".jpg")
    
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
        
        # 容错清理
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
        self.root.title("WebP 批量转 JPG 工具 (多任务队列终极版)")
        self.root.geometry("920x720")
        self.root.resizable(False, False)
        
        self.config = load_config()
        self.folder_list = [] # 任务队列
        
        self.delete_source = tk.BooleanVar(value=self.config.get("delete_source", True))
        self.cpu_cores = tk.StringVar(value=str(self.config.get("cores", multiprocessing.cpu_count())))
        self.quality = tk.StringVar(value=self.config.get("quality", "95"))
        self.overwrite = tk.StringVar(value=self.config.get("overwrite", "覆盖同名文件"))
        
        self.is_running = False
        self.cancel_event = threading.Event()

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        # 1. 任务队列区
        frame_queue = tk.LabelFrame(self.root, text=" 任务队列 (支持拖拽文件夹到此区域) ", font=("微软雅黑", 10), pady=5, padx=5)
        frame_queue.pack(fill=tk.X, padx=15, pady=10)
        
        self.listbox = tk.Listbox(frame_queue, height=5, font=("微软雅黑", 9), selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        
        scrollbar = tk.Scrollbar(frame_queue, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        
        # 队列操作按钮
        frame_queue_btns = tk.Frame(frame_queue)
        frame_queue_btns.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        tk.Button(frame_queue_btns, text="添加文件夹", font=("微软雅黑", 9), width=10, command=self.select_folder).pack(pady=2)
        tk.Button(frame_queue_btns, text="移除选中", font=("微软雅黑", 9), width=10, command=self.remove_selected).pack(pady=2)
        tk.Button(frame_queue_btns, text="清空队列", font=("微软雅黑", 9), width=10, command=self.clear_queue).pack(pady=2)

        if HAS_DND:
            self.listbox.drop_target_register(DND_FILES)
            self.listbox.dnd_bind('<<Drop>>', self.on_drop)
            frame_queue.config(text=" 任务队列 (支持拖拽文件夹到此区域) ")

        # 2. 选项区
        frame_options = tk.LabelFrame(self.root, text=" 转换设置 ", font=("微软雅黑", 10), pady=5, padx=5)
        frame_options.pack(fill=tk.X, padx=15, pady=5)

        row1 = tk.Frame(frame_options)
        row1.pack(fill=tk.X, pady=5)
        tk.Checkbutton(row1, text="转换成功后删除源文件", variable=self.delete_source, font=("微软雅黑", 10)).pack(side=tk.LEFT)
        tk.Label(row1, text="  并发核心数:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        ttk.Combobox(row1, textvariable=self.cpu_cores, values=[str(i) for i in range(1, multiprocessing.cpu_count() + 1)], width=4, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        row2 = tk.Frame(frame_options)
        row2.pack(fill=tk.X, pady=5)
        tk.Label(row2, text="JPG 质量:", font=("微软雅黑", 10)).pack(side=tk.LEFT)
        ttk.Combobox(row2, textvariable=self.quality, values=["100", "95", "85", "75", "60"], width=5, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        
        tk.Label(row2, text="  覆盖策略:", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=(15, 0))
        ttk.Combobox(row2, textvariable=self.overwrite, values=["覆盖同名文件", "跳过同名文件", "重命名保存"], width=12, state="readonly", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)

        # 3. 操作区 (进度条 + 按钮)
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

        # 左侧：运行日志
        frame_left = tk.Frame(frame_bottom)
        frame_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        tk.Label(frame_left, text="运行日志 (成功/进度):", font=("微软雅黑", 10)).pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(frame_left, height=12, state='disabled', font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 右侧：错误日志
        frame_right = tk.Frame(frame_bottom)
        frame_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        frame_right_header = tk.Frame(frame_right)
        frame_right_header.pack(fill=tk.X)
        tk.Label(frame_right_header, text="错误日志 (双击打开文件位置):", font=("微软雅黑", 10), fg="red").pack(side=tk.LEFT)
        tk.Button(frame_right_header, text="导出日志", font=("微软雅黑", 8), command=self.export_logs).pack(side=tk.RIGHT)
        
        self.error_text = scrolledtext.ScrolledText(frame_right, height=12, state='disabled', font=("Consolas", 9), fg="red")
        self.error_text.pack(fill=tk.BOTH, expand=True)
        # 绑定双击事件
        self.error_text.bind("<Double-Button-1>", self.on_error_double_click)

    # ================= 队列与拖拽操作 =================
    def on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        added = 0
        for path in files:
            if os.path.isdir(path):
                if path not in self.folder_list:
                    self.folder_list.append(path)
                    self.listbox.insert(tk.END, path)
                    added += 1
        if added > 0:
            self.log_info(f"已添加 {added} 个文件夹到任务队列")

    def select_folder(self):
        folder = filedialog.askdirectory(title="选择包含 WebP 的文件夹")
        if folder and folder not in self.folder_list:
            self.folder_list.append(folder)
            self.listbox.insert(tk.END, folder)

    def remove_selected(self):
        selected = self.listbox.curselection()
        for index in reversed(selected):
            self.listbox.delete(index)
            del self.folder_list[index]

    def clear_queue(self):
        self.listbox.delete(0, tk.END)
        self.folder_list.clear()

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
            
        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            title="保存日志",
            initialfile="webp2jpg_log.txt"
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("========== 运行日志 ==========\n")
                    f.write(info_content + "\n\n")
                    f.write("========== 错误日志 ==========\n")
                    f.write(error_content + "\n")
                messagebox.showinfo("成功", "日志已成功导出！")
            except Exception as e:
                messagebox.showerror("错误", f"导出日志失败: {e}")

    def on_error_double_click(self, event):
        """双击错误日志，提取路径并打开文件夹"""
        try:
            # 获取点击位置的行索引
            index = self.error_text.index(f"@{event.x},{event.y}")
            line_num = int(index.split('.')[0])
            line_text = self.error_text.get(f"{line_num}.0", f"{line_num}.end")
            
            # 正则匹配提取路径 (格式: ✗ 失败: D:\path\to\file.webp)
            match = re.search(r'✗ 失败: (.*?)(?:\s*$)', line_text)
            if match:
                path = match.group(1).strip()
                path = os.path.normpath(path)
                if os.path.exists(path):
                    # 调用资源管理器打开并选中文件
                    subprocess.Popen(f'explorer /select,"{path}"')
                else:
                    messagebox.showwarning("提示", f"文件不存在：\n{path}")
        except Exception:
            pass

    # ================= 转换核心逻辑 =================
    def cancel_conversion(self):
        self.cancel_event.set()
        self.btn_cancel.config(state=tk.DISABLED, text="正在取消...")
        self.log_info("\n⚠️ 收到取消信号，正在停止后续任务，请稍候...")

    def on_closing(self):
        self.config["delete_source"] = self.delete_source.get()
        self.config["cores"] = int(self.cpu_cores.get())
        self.config["quality"] = self.quality.get()
        self.config["overwrite"] = self.overwrite.get()
        save_config(self.config)
        self.root.destroy()

    def start_conversion(self):
        if not self.folder_list:
            messagebox.showwarning("提示", "请先添加至少一个文件夹到任务队列！")
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

        threading.Thread(
            target=self.run_conversion, 
            args=(
                list(self.folder_list), # 复制一份队列，防止转换过程中用户修改
                self.delete_source.get(), 
                int(self.cpu_cores.get()), 
                self.quality.get(), 
                self.overwrite.get()
            ),
            daemon=True
        ).start()

    def run_conversion(self, folders, delete_original, workers, quality, overwrite_strategy):
        all_files = []
        for folder in folders:
            root_path = Path(folder)
            if root_path.is_dir():
                # 权限检测
                if not os.access(root_path, os.W_OK) and delete_original:
                    self.log_error(f"⚠️ 无写入权限，跳过文件夹: {folder}")
                    continue
                files = [str(p) for p in root_path.rglob("*") if p.is_file() and p.suffix.lower() == ".webp"]
                all_files.extend(files)

        if not all_files:
            self.log_info("未找到任何 .webp 文件。")
            self.finish_conversion(0, 0, 0, cancelled=False)
            return

        self.log_info(f"共找到 {len(all_files)} 个 WebP 文件，准备分批处理...")
        self.root.after(0, lambda: self.progress.config(maximum=len(all_files)))

        success_count = 0
        fail_count = 0
        skip_count = 0
        cancelled = False
        
        # 分批处理，防止内存溢出 (每批 500 个)
        batch_size = 500
        with ProcessPoolExecutor(max_workers=workers) as executor:
            for i in range(0, len(all_files), batch_size):
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                
                batch_files = all_files[i:i+batch_size]
                tasks = [(f, delete_original, quality, overwrite_strategy) for f in batch_files]
                futures = {executor.submit(convert_single, task): task for task in tasks}
                
                for future in as_completed(futures):
                    if self.cancel_event.is_set():
                        cancelled = True
                        break
                        
                    src_path_str, dst_path_str, status, err = future.result()
                    
                    if status == "success":
                        self.log_info(f"✓ 成功: {Path(src_path_str).name} -> {Path(dst_path_str).name}")
                        success_count += 1
                    elif status == "skipped":
                        self.log_info(f"➖ 跳过: {Path(src_path_str).name}")
                        skip_count += 1
                    else:
                        self.log_error(f"✗ 失败: {src_path_str}\n   原因: {err}\n")
                        fail_count += 1
                    
                    self.root.after(0, lambda: self.progress.step(1))

                if cancelled:
                    # 取消剩余未开始的任务
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        self.finish_conversion(success_count, fail_count, skip_count, cancelled)

    def finish_conversion(self, success, fail, skip, cancelled):
        if cancelled:
            self.log_info(f"\n⚠️ 转换已被用户取消。已完成：成功 {success} 个，失败 {fail} 个，跳过 {skip} 个。")
        else:
            self.log_info(f"\n处理完成：成功 {success} 个，失败 {fail} 个，跳过 {skip} 个。")
            
        if fail > 0:
            self.log_info("⚠️ 有文件转换失败，请查看右侧【错误日志】排查原因。")
            
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
    app = WebpConverterApp(root)
    root.mainloop()
