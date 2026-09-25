#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量将文件夹中的 WebP 图片转换为 JPG。

用法示例:
    python webp2jpg.py ./images
    python webp2jpg.py ./images -q 90
    python webp2jpg.py ./images --no-recursive --delete
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("缺少 Pillow 库，请先运行：pip install Pillow")


def convert_one(src: Path, quality: int, delete_original: bool) -> bool:
    """转换单个文件，返回是否成功。"""
    dst = src.with_suffix(".jpg")

    if dst.exists():
        print(f"跳过（已存在）: {dst.name}")
        return False

    try:
        with Image.open(src) as im:
            im.load()
            icc = im.info.get("icc_profile")

            # JPG 不支持透明通道，需要把透明部分合成到白色背景上
            if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])  # 用 alpha 通道做蒙版
                im = bg
            else:
                im = im.convert("RGB")

            save_kwargs = {
                "format": "JPEG",
                "quality": quality,
                "optimize": True,
                "progressive": True,
            }
            if icc:
                save_kwargs["icc_profile"] = icc

            im.save(dst, **save_kwargs)

        if delete_original:
            src.unlink()

        print(f"✓ {src.name}  ->  {dst.name}")
        return True

    except Exception as e:
        print(f"✗ 转换失败 {src}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="批量把 WebP 转换成 JPG")
    parser.add_argument("folder", help="存放 WebP 文件的文件夹路径")
    parser.add_argument("-q", "--quality", type=int, default=95,
                        help="JPG 质量 1-100，默认 95")
    parser.add_argument("-r", "--no-recursive", action="store_true",
                        help="只处理当前文件夹，不递归子文件夹")
    parser.add_argument("-d", "--delete", action="store_true",
                        help="转换成功后删除原 WebP 文件")
    args = parser.parse_args()

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"错误：{root} 不是有效文件夹")

    # 收集所有 .webp 文件（后缀大小写不敏感）
    iterator = root.glob("*") if args.no_recursive else root.rglob("*")
    files = sorted(
        p for p in iterator
        if p.is_file() and p.suffix.lower() == ".webp"
    )

    if not files:
        print("未找到任何 .webp 文件")
        return

    print(f"找到 {len(files)} 个 WebP 文件，开始转换...\n")
    ok = sum(convert_one(f, args.quality, args.delete) for f in files)
    print(f"\n完成：成功 {ok} 个，失败/跳过 {len(files) - ok} 个")


if __name__ == "__main__":
    main()