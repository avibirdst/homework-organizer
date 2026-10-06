"""程序入口。

启动方式::

    python -m homework_organizer.gui.app

或使用项目根目录下的便捷脚本::

    python run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入模块搜索路径。
# 直接运行本文件时，Python 仅将本文件所在目录加入搜索路径，而导入
# homework_organizer 包需要其上级目录，故此处显式插入以保证无论从何处
# 运行都能正确导入。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    # 插入至首位，确保优先使用本项目的模块，避免与同名第三方包冲突。
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from homework_organizer.gui.main_window import MainWindow  # noqa: E402


def main() -> int:
    """启动图形界面并进入事件循环。

    Returns:
        进程退出码，``0`` 表示正常退出。
    """
    # QApplication 管理事件循环与全局状态，必须在创建任何窗口之前实例化。
    app = QApplication(sys.argv)

    # 微软雅黑在 Windows 上的中文渲染效果较好。
    # 非 Windows 系统可能不存在该字体，设置失败不影响程序运行。
    try:
        app.setFont(QFont("Microsoft YaHei UI", 10))
    except Exception:
        pass

    window = MainWindow()
    window.show()

    # 进入事件循环，阻塞至用户关闭窗口。
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
