"""核心逻辑层。

本包内所有模块为纯 Python 实现，不依赖 PySide6。如此设计的目的：

1. 核心逻辑可脱离图形界面单独测试，见 ``tests/`` 目录。
2. 更换界面实现（命令行、Web）时无需改动核心逻辑。
3. 逻辑缺陷与界面缺陷可分别定位。

模块划分：

- :mod:`~homework_organizer.core.scan`      需求 1：扫描与列出
- :mod:`~homework_organizer.core.rename`    需求 2：批量改名
- :mod:`~homework_organizer.core.roster`    需求 2：学生信息表（学号 ↔ 姓名）
- :mod:`~homework_organizer.core.archive`   需求 3：归档、报告与撤销；作业归类
- :mod:`~homework_organizer.core.wordcount` 作业检查：字数统计与按检索信息汇总
"""
