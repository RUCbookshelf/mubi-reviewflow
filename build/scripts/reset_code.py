# -*- coding: utf-8 -*-
"""找回密码·服务器管理员取码（2.1.5）：为指定用户名生成 15 分钟一次性重置码。

用法（数据目录定位与后端一致，经环境变量）::

    COBOOKSHELF_DATA_DIR=/var/lib/reviewflow REVIEWFLOW_MODE=server \
        python build/scripts/reset_code.py <用户名>

码打印到 stdout——请线下转交本人，勿经聊天/工单等留痕渠道。用户名不存在
时中性提示（exit 1，不区分「不存在」与「数据目录不对」）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_backend import auth, auth_recovery  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python build/scripts/reset_code.py <用户名>", file=sys.stderr)
        return 2
    username = sys.argv[1].strip()
    if auth.get_user(username) is None:
        print(f"未能为「{username}」生成重置码：用户名不存在或数据目录不对。",
              file=sys.stderr)
        return 1
    code = auth_recovery.issue_reset_code(username)
    if code is None:
        print("重置码生成失败：无法写入数据目录。", file=sys.stderr)
        return 1
    print(f"用户「{username}」的重置码（15 分钟内有效、一次性，请线下转交）：")
    print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
