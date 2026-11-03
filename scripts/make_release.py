"""打包一个干净的发布包，用于分发给同事。

组成：
  发布包/抖音监控/            —— 程序（不含任何个人数据）
  发布包/使用手册（发给同事）.docx
  抖音合集播放量监控（发布包）.zip
"""
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(r"E:\agent\工作\抖音信息抓取")
SRC = ROOT / "dist" / "抖音监控"
PKG = ROOT / "发布包"
DST = PKG / "抖音监控"
ZIP = ROOT / "抖音合集播放量监控（发布包）.zip"
MANUAL = ROOT / "使用手册（发给同事）.docx"

# 这些是运行后产生的个人数据/环境，不随包分发
EXCLUDE = {"data", "logs", "output", "config.json", ".browser_profile"}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not SRC.exists():
        raise SystemExit(f"找不到源目录：{SRC}")

    # 不用 rmtree/unlink：沙箱会拦截删除操作。
    # 目录和 zip 都用覆盖写入；若担心 dist 删过文件产生残留，手动删 发布包/ 后重跑。
    PKG.mkdir(parents=True, exist_ok=True)
    DST.mkdir(parents=True, exist_ok=True)

    print("复制程序 ...")
    for item in SRC.iterdir():
        if item.name in EXCLUDE:
            continue
        target = DST / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    print("  已排除：", "、".join(sorted(EXCLUDE)))

    if MANUAL.exists():
        shutil.copy2(MANUAL, PKG / MANUAL.name)

    print("压缩 ...")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(PKG.rglob("*")):
            if p.is_file():
                # 压缩包根目录直接是 抖音监控/ 和手册，解压即用
                z.write(p, p.relative_to(PKG))
    mb = ZIP.stat().st_size / 1024 / 1024
    print(f"完成：{ZIP}  ({mb:.0f} MB)")


if __name__ == "__main__":
    main()
