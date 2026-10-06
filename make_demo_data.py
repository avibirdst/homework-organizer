"""生成演示数据。

在指定目录下创建一批模拟学生作业文件，用于功能测试与效果演示。批量整理
功能需要覆盖多种分支场景才能验证完整：符合命名规则的文件、不符合规则的
文件、重名冲突场景，以及字数长短不一用于验证字数限制的文档。

用法::

    python make_demo_data.py                      # 生成到当前目录
    python make_demo_data.py E:/计协应聘            # 生成到指定目录

生成内容：

- 8 份 ``.docx``，正文长度各不相同，用于测试字数限制。
- 3 份 ``.txt``，其中一份内容极短。
- 2 份 ``.pdf``。
- 2 个不符合命名规则的文件，用于测试跳过逻辑。
- 1 组重名冲突场景，用于测试防覆盖逻辑。
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """在目标目录生成演示文件。

    Returns:
        进程退出码。
    """
    # 目标目录取自命令行参数，未提供时使用当前目录。
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    target.mkdir(parents=True, exist_ok=True)

    print(f"正在生成演示数据到：{target}")

    try:
        _make_docx_files(target)
    except ImportError:
        print("  [跳过] 未安装 python-docx，无法生成 docx 演示文件。")
        print("         安装命令：pip install python-docx")

    _make_txt_files(target)

    try:
        _make_pdf_files(target)
    except ImportError:
        print("  [跳过] 未安装 pypdf，无法生成 pdf 演示文件。")
        print("         安装命令：pip install pypdf")

    _make_irregular_files(target)

    print("")
    print("演示数据生成完成。")
    print("提示：文件全部以「学号_姓名_作业名.扩展名」格式命名，")
    print("      可以直接用来测试批量改名功能。")

    return 0


def _make_docx_files(target: Path) -> None:
    """生成内容长度各异的 ``.docx`` 文件。

    字数分布有意设置长短差异，使阈值设为 500 时能同时出现达标与不合格
    两类结果，便于验证标红效果。

    Args:
        target: 输出目录。
    """
    from docx import Document

    # (学号, 姓名, 作业名, 正文段落重复次数)
    assignments = [
        ("20230101", "张三", "第一次作业", 12),
        ("20230102", "李四", "第一次作业", 2),
        ("20230103", "王五", "数据结构实验", 20),
        ("20230104", "赵六", "数据结构实验", 1),
        ("20230105", "钱七", "第二次作业", 8),
        ("20230106", "孙八", "第二次作业", 15),
        ("20230107", "周九", "算法分析报告", 3),
        ("20230108", "吴十", "算法分析报告", 25),
    ]

    # 中英文段落交替填充，使生成文件可同时验证中文字数与英文单词的统计。
    chinese_paragraph = (
        "本次实验的主要内容是分析不同排序算法的时间复杂度与空间复杂度。"
        "通过对冒泡排序、快速排序、归并排序以及堆排序的对比实验，"
        "我们发现在数据规模较小时各种算法差异不明显，"
        "但当数据规模增大到百万级别时，快速排序和归并排序的优势就非常显著了。"
    )
    english_paragraph = (
        "In this report we compare several classic sorting algorithms "
        "and discuss their average case and worst case performance. "
        "The experimental results show that quicksort is usually faster "
        "in practice while merge sort provides a stable upper bound."
    )

    for student_id, name, assignment, repeat in assignments:
        filename = f"{student_id}_{name}_{assignment}.docx"
        path = target / filename

        document = Document()
        document.add_heading(f"{assignment} - {name}", level=1)

        for i in range(repeat):
            if i % 2 == 0:
                document.add_paragraph(chinese_paragraph)
            else:
                document.add_paragraph(english_paragraph)

        document.save(str(path))
        print(f"  已生成：{filename}（正文重复 {repeat} 段）")


def _make_txt_files(target: Path) -> None:
    """生成 ``.txt`` 文件，其中一份用于演示字数不足的情况。

    Args:
        target: 输出目录。
    """
    files = [
        (
            "20230201",
            "郑十一",
            "读书笔记",
            "本文是对《算法导论》第一章的读书笔记。" * 30,
        ),
        # 内容极短，用于演示标红效果。
        ("20230202", "冯十二", "读书笔记", "看完了，还行。"),
        (
            "20230203",
            "陈十三",
            "课程总结",
            "本学期我学习了数据结构与算法、操作系统、计算机网络三门核心课程。" * 15,
        ),
    ]

    for student_id, name, assignment, content in files:
        filename = f"{student_id}_{name}_{assignment}.txt"
        (target / filename).write_text(content, encoding="utf-8")
        print(f"  已生成：{filename}（{len(content)} 个原始字符）")


def _make_pdf_files(target: Path) -> None:
    """生成极简 ``.pdf`` 文件。

    仅包含一页空白页，用于验证扫描与字数统计能正确处理 PDF 格式。
    ``pypdf`` 主要用于读取、合并与拆分 PDF，不擅长生成带文本内容的文件；
    空白页同样可验证 PDF 的读取路径。

    Args:
        target: 输出目录。
    """
    from pypdf import PdfWriter

    files = [
        ("20230301", "褚十四", "实验报告"),
        ("20230302", "卫十五", "实验报告"),
    ]

    for student_id, name, assignment in files:
        filename = f"{student_id}_{name}_{assignment}.pdf"
        path = target / filename

        writer = PdfWriter()
        # A4 尺寸，单位为点。
        writer.add_blank_page(width=595, height=842)

        with open(path, "wb") as handle:
            writer.write(handle)

        print(f"  已生成：{filename}（空白页，用于测试 PDF 读取路径）")


def _make_irregular_files(target: Path) -> None:
    """生成不符合命名规则的文件及重名冲突场景。

    前者用于验证改名功能能识别并跳过不合规文件；后者用于验证目标名已被
    占用时的跳过保护。

    Args:
        target: 输出目录。
    """
    irregular = [
        ("随便起的名字.txt", "这个文件的命名不符合规则，改名时应当被跳过。"),
        ("20230401_蒋十六.txt", "只有两段，缺少作业名，改名时应当被跳过。"),
    ]

    for filename, content in irregular:
        (target / filename).write_text(content, encoding="utf-8")
        print(f"  已生成：{filename}（不符合命名规则，用于测试跳过逻辑）")

    # 冲突场景：预先创建目标名文件，使另一文件的改名操作必然触发占用冲突。
    conflict_target_name = "读书笔记_20230402.txt"
    (target / conflict_target_name).write_text(
        "这个文件是故意创建的，用来模拟「目标文件名已被占用」的冲突场景。",
        encoding="utf-8",
    )
    print(f"  已生成：{conflict_target_name}（用于测试重名冲突保护）")

    source_name = "20230402_沈十七_读书笔记.txt"
    (target / source_name).write_text(
        "这个文件按规则改名后，目标名已被上面那个文件占用，应当被跳过。",
        encoding="utf-8",
    )
    print(f"  已生成：{source_name}（改名为「{conflict_target_name}」时会冲突）")


if __name__ == "__main__":
    sys.exit(main())
