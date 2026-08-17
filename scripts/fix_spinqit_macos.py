#!/usr/bin/env python3
"""修复 macOS 上 spinqit 原生扩展找不到 dylib 的问题。

spinqit 0.2.4 的 spinq_backends.*.so 只声明了 `$ORIGIN` 作为 rpath。
`$ORIGIN` 是 Linux ELF 的约定，macOS 的 dyld 不会展开它，因此即使
libSpinQInterface_darwin_arm_64.dylib 就放在同一目录下，import spinqit
仍会失败（ImportError: Library not loaded: @rpath/libSpinQInterface_*.dylib）。

本脚本补上等价的 `@loader_path` rpath 并重新 ad-hoc 签名。
在 `uv sync --extra spinq` 之后运行一次即可；重建 venv 后需要重新运行。
非 macOS 平台直接跳过。
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
from pathlib import Path


def main() -> int:
    if sys.platform != "darwin":
        print("[skip] 非 macOS 平台，无需修复。")
        return 0

    pkg = Path(sysconfig.get_paths()["purelib"]) / "spinqit"
    if not pkg.is_dir():
        print("[skip] 未安装 spinqit，无需修复。请先运行 uv sync --extra spinq。")
        return 0

    exts = sorted(pkg.glob("spinq_backends*.so"))
    if not exts:
        print(f"[skip] 未在 {pkg} 找到 spinq_backends 扩展。")
        return 0

    for ext in exts:
        rpaths = subprocess.run(
            ["otool", "-l", str(ext)], capture_output=True, text=True, check=True
        ).stdout
        if "@loader_path" in rpaths:
            print(f"[ok] {ext.name} 已包含 @loader_path，跳过。")
            continue

        subprocess.run(
            ["install_name_tool", "-add_rpath", "@loader_path", str(ext)], check=True
        )
        # 修改 Mach-O 头会使原签名失效，必须重新 ad-hoc 签名。
        subprocess.run(["codesign", "--force", "--sign", "-", str(ext)], check=True)
        print(f"[fixed] 已为 {ext.name} 添加 @loader_path rpath 并重新签名。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
