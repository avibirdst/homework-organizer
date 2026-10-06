"""需求 3：归档与报告；作业归类。

本模块按类别、学期或**文件名中命中的段**将文件移动至子文件夹，生成整理报告，
并支持撤销最近一次归档操作。这些能力构成完整闭环：

    - :func:`archive`       按类别 / 学期 / 关键词段移动文件
    - :func:`build_report`  生成整理报告，说明处理数量与跳过原因
    - :func:`undo_last`     撤销最近一次归档，将文件移回原位

三种分类依据共用同一套底层实现（同一次归档调用、同一份撤销日志、同一个报告
生成器），仅 ``mode`` 参数不同：

    - ``"extension"`` 按扩展名（Word文档 / PDF文档 …）
    - ``"semester"``  按文件修改时间落入的学期区间
    - ``"title"``     按识别出的作业名
    - ``"segment"``   按文件名切段后命中的关键词（段数不限）

撤销机制说明：
    每次归档执行后，将"文件由何处移动至何处"记入操作日志
    （默认 ``.homework_organizer/journal.json``，以 JSON 格式存储，便于人工
    查阅）。撤销时读取最新一条记录，逐项将文件由归档位置移回原位置。
    移回过程同样遵循不覆盖原则：原位置已存在文件时跳过并报告。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from shutil import copy2 as shutil_copy2
from shutil import move as shutil_move
from typing import Callable, Sequence

__all__ = [
    "ArchiveAction",
    "JournalEntry",
    "ArchiveResult",
    "UndoResult",
    "archive",
    "build_report",
    "save_report",
    "undo_last",
    "format_undo_result",
    "list_history",
    "load_journal",
    "save_journal",
    "append_journal_entry",
    "classify_by_extension",
    "classify_by_semester",
    "classify_by_title",
    "classify_by_segments",
    "extract_title",
    "parse_semester_ranges_text",
    "parse_title_whitelist_text",
    "parse_keyword_list_text",
    "DEFAULT_SEMESTERS",
    "SAMPLE_SEMESTER_TEXT",
    "SAMPLE_TITLE_WHITELIST_TEXT",
    "SAMPLE_KEYWORD_LIST_TEXT",
    "UNCLASSIFIED_NAME",
    "UNLISTED_TITLE_NAME",
    "JOURNAL_DIR_NAME",
    "JOURNAL_FILE_NAME",
    "JOURNAL_MAX_ENTRIES",
]

#: 操作日志所在子目录名。
JOURNAL_DIR_NAME = ".homework_organizer"

#: 操作日志文件名。
JOURNAL_FILE_NAME = "journal.json"

#: 日志保留的最大条数，用于限制长期使用后的文件体积。
JOURNAL_MAX_ENTRIES = 20

#: 未落入任何学期区间、或无法识别作业名的文件所使用的分组名。
#:
#: 按学期归档时它是真实的文件夹名；按作业名归档时，连作业名都没识别出来的
#: 文件也会归入此文件夹。
UNCLASSIFIED_NAME = "未归类"

#: 按作业名归档时，作业名不在白名单内所使用的**标记**（非文件夹名）。
#:
#: 该值不会出现在磁盘上：命中它的文件按用户要求原地保留、不移动，只进入
#: 归档结果的 skipped 明细与整理报告的提示清单。之所以仍用常量而非 ``None``，
#: 是为了让 :func:`classify_by_title` 的返回值保持"分类名 + 说明"的稳定形状，
#: 调用方只需比对常量即可分辨。
UNLISTED_TITLE_NAME = "未列入作业表"

#: 作业名白名单输入的示例文本。
#:
#: 每行一个作业名，以 ``#`` 开头的是注释行。空行会被忽略。
SAMPLE_TITLE_WHITELIST_TEXT = """# 每行一个作业名，与文件名中识别出的作业名比对
# 以 # 开头的是注释行，空行会被忽略
# 不在本表中的作业名将不予归类，只会在预览里标出
第一次作业
第二次作业
数据结构实验
算法分析报告
读书笔记
课程总结"""

#: 关键词表输入的示例文本（按段归类用）。
#:
#: 表里写什么就匹配什么——作业名、姓名、学号都可以混着写。
SAMPLE_KEYWORD_LIST_TEXT = """# 每行一个关键词，与文件名的**每一段**逐一比对
# 文件名有多少段都无所谓，命中几段就建几个文件夹
# 表里可以写作业名、姓名、学号等任意内容
第一次作业
第二次作业
数据结构实验
算法分析报告
读书笔记
课程总结"""


def parse_keyword_list_text(text: str) -> list[str]:
    """把用户输入的文本解析为按段归类用的关键词表。

    与 :func:`parse_title_whitelist_text` 的解析规则完全一致（每行一项、
    跳过空行与注释、去重保序），单独定义一个函数是为了让调用点的语义清晰：
    同样是"一行一项"，但用途不同，出错时更容易定位。

    Args:
        text: 用户输入的整段文本。

    Returns:
        关键词列表；文本为空或没有有效行时返回空列表。
    """
    return parse_title_whitelist_text(text)


def parse_title_whitelist_text(text: str) -> list[str]:
    """把用户输入的文本解析为作业名白名单。

    每行一个作业名，首尾空白会被去除。空行与 ``#`` 开头的注释行跳过。
    重复的作业名只保留首次出现的那一个，并保持原顺序。

    Args:
        text: 用户输入的整段文本。

    Returns:
        作业名列表；文本为空或没有有效行时返回空列表。
    """
    titles: list[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line in seen:
            continue
        seen.add(line)
        titles.append(line)

    return titles


@dataclass
class ArchiveAction:
    """单条归档动作，记录文件由 ``src`` 移动至 ``dst``。

    路径以字符串形式保存，因为需要序列化为 JSON；仅在需要时才转换回
    :class:`~pathlib.Path`。

    按段归类（``mode="segment"``）时一个文件可能命中多个段，从而被放进多个
    文件夹。此时 ``dst`` 记录**主目标**（第一个命中的段），其余目标依次记录在
    :attr:`copies` 中。撤销时需要把这些副本一并删除，再把 ``src`` 还原回去。
    """

    src: str
    """移动前的完整路径。"""

    dst: str
    """移动后的完整路径（多目标时为主目标）。"""

    category: str
    """所属分类或学期，仅用于报告展示。首次命中时即为 ``dst`` 所在文件夹名。"""

    mtime: str = ""
    """文件在**移动前**的修改时间，ISO 格式字符串。

    必须在移动之前采集：文件一旦被移走，``src`` 指向的旧路径即不存在，
    事后无法再读取其修改时间。按学期归档时报告需要展示这一信息。
    """

    copies: list[str] = field(default_factory=list)
    """除主目标外，该文件被复制到的其它位置（按段归类多命中时使用）。

    这些路径是**真实存在的文件副本**，撤销时必须逐个删除。默认空列表表示
    只有 ``dst`` 一个目标，行为与单目标归档完全一致。
    """

    def all_targets(self) -> list[str]:
        """返回该动作涉及的全部落点路径（主目标在前）。

        Returns:
            路径字符串列表，至少含一个元素（``dst``）。
        """
        return [self.dst, *self.copies]

    def mtime_text(self) -> str:
        """返回便于阅读的修改时间文本。

        Returns:
            ``YYYY-MM-DD HH:MM`` 形式的文本；缺失或无法解析时返回占位说明。
        """
        if not self.mtime:
            return "（时间不可读）"
        try:
            return datetime.fromisoformat(self.mtime).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return "（时间不可读）"


@dataclass
class JournalEntry:
    """一条操作日志，对应一次 :func:`archive` 调用。"""

    timestamp: str
    """操作时间，ISO 格式字符串。"""

    operation: str
    """操作类型，当前为 ``"archive"``。"""

    actions: list[ArchiveAction] = field(default_factory=list)
    """本次操作涉及的全部归档动作。"""

    def to_dict(self) -> dict:
        """转换为可 JSON 序列化的字典。

        Returns:
            含 ``timestamp``、``operation``、``actions`` 三个键的字典。
        """
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "actions": [
                {
                    "src": action.src,
                    "dst": action.dst,
                    "category": action.category,
                    "mtime": action.mtime,
                    "copies": list(action.copies),
                }
                for action in self.actions
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> JournalEntry:
        """由字典构造 :class:`JournalEntry`。

        使用 ``dict.get`` 取值以兼容缺少字段的旧版本日志。

        Args:
            data: :meth:`to_dict` 产出的字典。

        Returns:
            重建的日志条目。
        """
        return JournalEntry(
            timestamp=data.get("timestamp", ""),
            operation=data.get("operation", "archive"),
            actions=[
                ArchiveAction(
                    src=item.get("src", ""),
                    dst=item.get("dst", ""),
                    category=item.get("category", ""),
                    # 旧日志没有 mtime 字段，缺失时留空即可，不影响撤销。
                    mtime=item.get("mtime", ""),
                    # 旧日志没有 copies 字段，缺失时视为单目标。
                    copies=list(item.get("copies", [])),
                )
                for item in data.get("actions", [])
            ],
        )


@dataclass
class ArchiveResult:
    """一次归档执行的结果摘要，用于生成报告。"""

    moved: list[ArchiveAction] = field(default_factory=list)
    """成功移动的动作列表。"""

    skipped: list[tuple[str, str]] = field(default_factory=list)
    """被跳过的文件及原因，元素为 ``(路径, 原因)``。"""

    categories: set[str] = field(default_factory=set)
    """本次操作涉及的分类集合。"""


@dataclass
class UndoResult:
    """一次撤销执行的结果摘要。"""

    restored: list[tuple[str, str]] = field(default_factory=list)
    """成功还原的文件，元素为 ``(归档位置, 原位置)``。"""

    removed_copies: list[str] = field(default_factory=list)
    """被删除的副本路径。

    按段归类产生多份副本时，撤销会先把这些副本删掉，再把本体移回原位。
    单目标归档时该列表恒为空。
    """

    failed: list[tuple[str, str]] = field(default_factory=list)
    """还原失败的文件及原因。"""

    entry_timestamp: str = ""
    """被撤销操作的时间戳，用于确认撤销对象。"""


#: 扩展名到子文件夹名的映射。未覆盖的扩展名归入"其他"。
_EXTENSION_CATEGORY_MAP: dict[str, str] = {
    ".docx": "Word文档",
    ".doc": "Word文档",
    ".pdf": "PDF文档",
    ".txt": "文本文件",
    ".md": "文本文件",
    ".xlsx": "Excel表格",
    ".xls": "Excel表格",
    ".csv": "Excel表格",
    ".pptx": "PPT演示",
    ".ppt": "PPT演示",
    ".zip": "压缩包",
    ".rar": "压缩包",
    ".7z": "压缩包",
    ".png": "图片",
    ".jpg": "图片",
    ".jpeg": "图片",
    ".gif": "图片",
}

#: 默认学期区间，元素为 ``(学期名, 开始日期, 结束日期)``。
#:
#: 覆盖两个学年共四个学期：每年秋季学期 9 月初开学至次年 1 月中旬，
#: 春季学期 2 月下旬开学至 7 月上旬。**这是常见校历的估计值，各校校历不同，
#: 请在界面「学期区间设置」里按自己学校的情况填写**，否则文件可能落进
#: 「未归类」。
_DEFAULT_SEMESTERS: list[tuple[str, str, str]] = [
    ("2025-2026-1", "2025-09-01", "2026-01-15"),
    ("2025-2026-2", "2026-02-20", "2026-07-10"),
    ("2026-2027-1", "2026-09-01", "2027-01-15"),
    ("2026-2027-2", "2027-02-20", "2027-07-10"),
]

#: 默认学期区间的公开别名，供界面层读取。
DEFAULT_SEMESTERS: list[tuple[str, str, str]] = _DEFAULT_SEMESTERS

#: 学期区间输入的示例文本，供界面「填入示例」与导入 TXT 使用。
#:
#: 每行一条，格式为 ``学期名, 开始日期, 结束日期``，日期用 ``YYYY-MM-DD``。
#: 以 ``#`` 开头的行会被忽略，空行同样跳过。
SAMPLE_SEMESTER_TEXT = """# 每行一条：学期名, 开始日期, 结束日期（日期格式 YYYY-MM-DD）
# 以 # 开头的是注释行，会与空行一起被忽略
2025-2026-1, 2025-09-01, 2026-01-15
2025-2026-2, 2026-02-20, 2026-07-10
2026-2027-1, 2026-09-01, 2027-01-15
2026-2027-2, 2027-02-20, 2027-07-10"""

#: 学期区间文本中允许的分隔符，与信息表保持一致以便用户复用习惯。
_SEMESTER_SEPARATORS = (",", "，", "\t", "，", ";")


def parse_semester_ranges_text(text: str) -> list[tuple[str, str, str]]:
    """把用户输入的文本解析为学期区间序列。

    每行一条记录，格式为 ``学期名, 开始日期, 结束日期``。分隔符支持中英文逗号、
    制表符与分号。空行与 ``#`` 开头的注释行会被跳过。日期统一转为
    ``YYYY-MM-DD`` 形式，便于后续用 :func:`datetime.fromisoformat` 解析。

    Args:
        text: 用户输入的整段文本。

    Returns:
        解析出的区间列表，元素为 ``(学期名, 开始日期, 结束日期)``。
        文本为空或没有任何有效行时返回空列表。

    Raises:
        ValueError: 某行字段数不为 3，或日期不是 ``YYYY-MM-DD`` 形式。
            错误信息中会带上出错的行号，便于用户定位。
    """
    ranges: list[tuple[str, str, str]] = []

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        # 空行与注释行直接跳过，不算错误。
        if not line or line.startswith("#"):
            continue

        # 依次尝试各分隔符，取第一个能切出 3 段的分隔符。
        parts: list[str] = []
        for sep in _SEMESTER_SEPARATORS:
            if sep in line:
                parts = [seg.strip() for seg in line.split(sep)]
                break
        else:
            # 没有命中任何分隔符，退化为按空白切分，兼容空格分隔的写法。
            parts = line.split()

        if len(parts) != 3:
            raise ValueError(
                f"第 {line_no} 行应为「学期名, 开始日期, 结束日期」三个字段，"
                f"实际得到 {len(parts)} 个：{line}"
            )

        name, start_str, end_str = parts
        if not name:
            raise ValueError(f"第 {line_no} 行的学期名为空：{line}")

        # 校验并规范化日期。用户常写成 2026-9-1 这类省略前导零的形式，
        # 先补零再交给 date.fromisoformat——它同时兼顾「格式合法」与
        # 「真实存在的日期」两项检查（如 2026-02-30 会被拒绝）。
        for label, value in (("开始日期", start_str), ("结束日期", end_str)):
            normalized = _normalize_iso_date(value)
            if normalized is None:
                raise ValueError(
                    f"第 {line_no} 行的{label}「{value}」不是合法的 YYYY-MM-DD 日期"
                )
            # 回写规范化结果，统一为补零形式。
            if label == "开始日期":
                start_str = normalized
            else:
                end_str = normalized

        if start_str > end_str:
            raise ValueError(
                f"第 {line_no} 行的开始日期晚于结束日期：{start_str} > {end_str}"
            )

        ranges.append((name, start_str, end_str))

    return ranges


def _normalize_iso_date(value: str) -> str | None:
    """把用户输入的日期规范化为 ``YYYY-MM-DD``。

    容忍省略前导零的写法（``2026-9-1`` → ``2026-09-01``）以及 ``/`` 作分隔符的
    写法（``2026/9/1``）。同时校验日期真实存在，``2026-02-30`` 这类会被拒绝。

    Args:
        value: 用户输入的日期文本。

    Returns:
        规范化后的 ``YYYY-MM-DD`` 文本；无法解析时返回 ``None``。
    """
    text = value.strip().replace("/", "-").replace(".", "-")
    parts = text.split("-")
    if len(parts) != 3:
        return None

    try:
        year, month, day = (int(part) for part in parts)
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def classify_by_extension(path: Path) -> str:
    """按扩展名确定文件所属分类。

    Args:
        path: 文件路径。

    Returns:
        分类名，即子文件夹名；映射表中没有的扩展名返回 ``"其他"``。
    """
    return _EXTENSION_CATEGORY_MAP.get(path.suffix.lower(), "其他")


def extract_title(
    path: Path,
    preferred_titles: Sequence[str] | None = None,
) -> tuple[str, str]:
    """从文件名中识别作业名。

    识别规则**完全复用需求二的段推断引擎**（:func:`~homework_organizer.core.rename.infer_fields_from_segments`），
    保证「改名」与「归类」对同一个文件名的理解一致：两处不会一个认得出、
    另一个认不出。

    规则回顾（完整版见 ``rename`` 模块）：

    ==================  ====================================================
    文件名形态           作业名
    ==================  ====================================================
    仅 1 段              无法识别
    2 段：数字 + 文本     文本段直接作作业名
    2 段：文本 + 文本     含作业特征词者作作业名；都含或都不含时取后置段
    3 段：数字 + 两段文本  余下两段文本套用上面的「文本 + 文本」规则
    其余情况             无法识别
    ==================  ====================================================

    传入 ``preferred_titles``（作业归类时即用户的作业名表）后，会先做一次
    精确匹配：某一段整体等于表内某项时直接认定其为作业名。用户手写的表比
    内置特征词表更权威，可纠正「读书笔记」这类不含内置特征词的作业名被
    误判为姓名的情况。

    Args:
        path: 文件路径，仅使用其文件名部分。
        preferred_titles: 用户提供的作业名清单，用于优先匹配。可传 ``None``。

    Returns:
        二元组 ``(作业名, 说明)``。识别失败时作业名为空字符串，说明为原因。
    """
    # 延迟导入：archive 与 rename 互相引用对方的数据类型，放在模块顶层会形成
    # 循环导入；此处仅在真正需要时导入。
    from . import rename as rename_core

    fields, _rule, description = rename_core.infer_fields_from_segments(
        path.stem, preferred_titles
    )

    if fields is None:
        return "", description

    # 三元组位置为 (学号, 姓名, 作业名)。
    title = fields[2]
    if not title:
        return "", f"{description}；但未能确定作业名"
    return title, description


def classify_by_title(
    path: Path,
    whitelist: Sequence[str] | None = None,
) -> tuple[str, str]:
    """按文件名中识别出的作业名确定分类。

    Args:
        path: 文件路径。
        whitelist: 作业名白名单。**非空**时，识别出的作业名必须命中其中一项
            才予归类，否则返回 :data:`UNLISTED_TITLE_NAME` 表示"不在作业表内"。
            传 ``None`` 或空序列表示不设白名单，识别出什么就按什么归类。

    Returns:
        二元组 ``(分类名, 说明)``。分类名为子文件夹名，说明为判定依据，
        直接展示在界面上。

        分类名有两种"不归档"取值，调用方需分辨：

        * :data:`UNCLASSIFIED_NAME` —— 连作业名都没识别出来；
        * :data:`UNLISTED_TITLE_NAME` —— 识别出了作业名，但不在用户提供的
          作业表内。此时按用户要求**不移动文件**，仅在报告中提示。
    """
    title, description = extract_title(path, whitelist)

    if not title:
        return UNCLASSIFIED_NAME, description

    if whitelist and title not in whitelist:
        return (
            UNLISTED_TITLE_NAME,
            f"识别出作业名「{title}」，但不在作业表中，按设定不移动",
        )

    return title, f"识别出作业名「{title}」，归入同名文件夹"


def _clean_keywords(keywords: Sequence[str] | None) -> list[str]:
    """清洗关键词表：去除空白项、去重并保持原顺序。

    Args:
        keywords: 用户提供的关键词序列，可为 ``None``。

    Returns:
        清洗后的关键词列表；输入为空时返回空列表。
    """
    if not keywords:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in keywords:
        word = str(item).strip()
        if not word or word in seen:
            continue
        seen.add(word)
        cleaned.append(word)
    return cleaned


def classify_by_segments(
    path: Path,
    keywords: Sequence[str] | None = None,
) -> tuple[list[str], str]:
    """把文件名的每一段与关键词表比对，返回全部命中的段。

    与 :func:`classify_by_title` 不同，本函数**不做作业名推断**，只做逐段
    匹配：文件名切成多少段都无所谓，命中几段就有几个分类。

    Args:
        path: 文件路径，仅使用其文件名部分。
        keywords: 关键词表。文件名的每一段都会与表比对。

    Returns:
        二元组 ``(命中列表, 说明)``：

        * 命中列表 —— 命中的段，按出现顺序排列（已去重）。空列表表示
          文件名的任何一段都不在表中。
        * 说明 —— 一句话解释判定结果，直接展示在界面上。
    """
    # 延迟导入避免循环依赖，原因同 extract_title。
    from . import rename as rename_core

    cleaned = _clean_keywords(keywords)
    if not cleaned:
        return [], "关键词表为空，无法比对"

    matched = rename_core.match_segments(path.stem, cleaned)
    if not matched:
        return [], f"文件名中没有段命中关键词表（共 {len(cleaned)} 个关键词）"

    joined = "、".join(f"「{item}」" for item in matched)
    return matched, f"命中 {len(matched)} 段：{joined}"


def classify_by_semester(
    path: Path,
    semester_ranges: Sequence[tuple[str, str, str]] | None = None,
) -> str:
    """按文件修改时间确定其所属学期。

    判定依据是文件的**修改时间**（mtime），而非创建时间或文件名中的字样。
    拿 mtime 逐条比对 ``semester_ranges``，命中即返回该学期名。

    Args:
        path: 文件路径，以其修改时间为判定依据。
        semester_ranges: 学期区间序列，元素为 ``(学期名, 开始日期, 结束日期)``，
            日期格式为 ``YYYY-MM-DD``。传入 ``None`` 或空序列时使用
            :data:`DEFAULT_SEMESTERS`。

    Returns:
        学期名；未落入任何区间时返回 :data:`UNCLASSIFIED_NAME`（``"未归类"``）。
    """
    ranges = semester_ranges if semester_ranges else _DEFAULT_SEMESTERS

    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return UNCLASSIFIED_NAME

    for semester_name, start_str, end_str in ranges:
        try:
            # 区间表里的日期已由 parse_semester_ranges_text 规范化，
            # 但直接调用本函数时仍可能传入非法值，故保留容错。
            start = datetime.fromisoformat(start_str)
            end = datetime.fromisoformat(end_str)
        except ValueError:
            continue

        # 结束日期需包含当天全部时刻，否则当天提交的文件会落入区间之外。
        end_inclusive = end.replace(hour=23, minute=59, second=59)
        if start <= mtime <= end_inclusive:
            return semester_name

    return UNCLASSIFIED_NAME


def _journal_path(root: Path) -> Path:
    """返回指定根目录下操作日志的完整路径。

    Args:
        root: 被整理的根目录。

    Returns:
        日志文件路径。
    """
    return root / JOURNAL_DIR_NAME / JOURNAL_FILE_NAME


def load_journal(root: str | Path) -> list[JournalEntry]:
    """读取操作日志。

    日志文件不存在、无法读取或内容损坏时均返回空列表，不抛出异常，以免
    辅助功能影响程序启动。

    Args:
        root: 被整理的根目录。

    Returns:
        按时间从旧到新排列的日志条目列表。
    """
    path = _journal_path(Path(root))
    if not path.exists():
        return []

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    entries: list[JournalEntry] = []
    for item in data:
        if isinstance(item, dict):
            entries.append(JournalEntry.from_dict(item))
    return entries


def save_journal(root: str | Path, entries: Sequence[JournalEntry]) -> Path:
    """将日志条目写入磁盘。

    Args:
        root: 被整理的根目录。
        entries: 完整的日志条目序列，调用方需保证其长度不超过
            :data:`JOURNAL_MAX_ENTRIES`。

    Returns:
        写入的日志文件路径。
    """
    root_path = Path(root)
    journal_dir = root_path / JOURNAL_DIR_NAME
    journal_dir.mkdir(parents=True, exist_ok=True)

    path = journal_dir / JOURNAL_FILE_NAME
    # ensure_ascii=False 保留中文原文，避免日志被转义为 \uXXXX 而无法阅读。
    payload = json.dumps(
        [entry.to_dict() for entry in entries],
        ensure_ascii=False,
        indent=2,
    )
    path.write_text(payload, encoding="utf-8")
    return path


def append_journal_entry(root: str | Path, entry: JournalEntry) -> Path:
    """追加一条日志，并将总条数裁剪至上限以内。

    Args:
        root: 被整理的根目录。
        entry: 待追加的日志条目。

    Returns:
        写入的日志文件路径。
    """
    entries = load_journal(root)
    entries.append(entry)
    if len(entries) > JOURNAL_MAX_ENTRIES:
        entries = entries[-JOURNAL_MAX_ENTRIES:]
    return save_journal(root, entries)


def archive(
    records: Sequence,
    root: str | Path,
    mode: str = "extension",
    template: str = "{category}",
    semester_ranges: Sequence[tuple[str, str, str]] | None = None,
    title_whitelist: Sequence[str] | None = None,
    keywords: Sequence[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> ArchiveResult:
    """按规则将文件移动至子文件夹，会修改磁盘上的文件位置。

    目标子文件夹不存在时自动创建；目标位置已存在同名文件时跳过，不覆盖。
    归档仅移动文件，不修改文件名。

    各模式的行为差异：

    ==============  ==========================================================
    模式             分类依据与落点
    ==============  ==========================================================
    ``extension``   按扩展名，单目标
    ``semester``    按文件修改时间所属学期，单目标
    ``title``       按文件名中识别出的作业名，单目标
    ``segment``     按文件名中**每一段**与关键词表的匹配结果，**可多目标**
    ==============  ==========================================================

    多目标（``segment`` 模式）时，命中的每个关键词都会得到一个文件夹，
    文件本体移入第一个命中项，其余命中项各放一份**副本**。


    Args:
        records: 扫描记录序列，需具有 ``path`` 属性。
        root: 被整理的根目录，子文件夹建立于其下。
        mode: 分类方式，``"extension"`` 按扩展名，``"semester"`` 按学期，
            ``"title"`` 按文件名中的作业名，``"segment"`` 按段匹配关键词表，
            大小写不敏感。
        template: 子文件夹命名模板，使用 ``{category}`` 占位符。
        semester_ranges: 学期区间，仅在 ``mode="semester"`` 时生效。
        title_whitelist: 作业名白名单，仅在 ``mode="title"`` 时生效。
            非空时不在表内的作业名不予归类。
        keywords: 关键词表，仅在 ``mode="segment"`` 时生效。文件名的每一段
            都会与表比对，命中即归类；**表为空时不执行任何操作**（该模式完全
            依赖用户给出的表）。
        progress: 进度回调，签名为 ``(当前序号, 总数, 说明文本)``。

    Returns:
        含成功移动、跳过明细与涉及分类的 :class:`ArchiveResult`。

    Raises:
        ValueError: ``mode`` 不是受支持的取值，或 ``segment`` 模式未提供关键词表。
    """
    root_path = Path(root)

    mode_normalized = mode.strip().lower()
    if mode_normalized not in ("extension", "semester", "title", "segment"):
        raise ValueError(
            f"不支持的归档模式：{mode}"
            "（仅支持 extension / semester / title / segment）"
        )

    # 按段归类完全依赖用户提供的表，空表下无法做任何判断。与其静默地什么
    # 都不做，不如直接报错，让界面层把"请先填表"这句话告诉用户。
    if mode_normalized == "segment" and not _clean_keywords(keywords):
        raise ValueError("按段归类必须提供非空的关键词表")

    result = ArchiveResult()
    total = len(records)

    for index, record in enumerate(records, start=1):
        src: Path = record.path

        if mode_normalized == "segment":
            _archive_one_by_segments(
                src=src,
                root_path=root_path,
                template=template,
                keywords=keywords,
                result=result,
                index=index,
                total=total,
                progress=progress,
            )
            continue

        if mode_normalized == "extension":
            category = classify_by_extension(src)
        elif mode_normalized == "semester":
            category = classify_by_semester(src, semester_ranges)
        else:
            category, reason = classify_by_title(src, title_whitelist)
            # 开启作业名白名单时，「识别出了作业名但不在表内」的文件按用户
            # 要求原地保留，只进 skipped 明细，在整理报告里提示用户核对。
            if category == UNLISTED_TITLE_NAME:
                result.skipped.append((str(src), reason))
                if progress is not None:
                    progress(index, total, f"{src.name} → 不在作业表，跳过")
                continue

        target_dir_name = template.format(category=category)
        target_dir = root_path / target_dir_name
        # 保持原文件名不变，归档只负责移动；改名由 rename 模块负责。
        dst = target_dir / src.name

        if progress is not None:
            progress(index, total, f"{src.name} → {target_dir_name}/")

        # 使用 resolve 规范化路径后比较，避免形如 "./a/../a" 的等价路径误判。
        try:
            same_location = src.resolve() == dst.resolve()
        except OSError:
            same_location = False

        if same_location:
            result.skipped.append((str(src), "文件已位于目标文件夹中，无需移动"))
            continue

        if dst.exists():
            result.skipped.append(
                (str(src), f"目标位置已存在同名文件，为避免覆盖已跳过：{dst.name}")
            )
            continue

        # 修改时间必须在移动**之前**采集：移动完成后 src 指向的旧路径
        # 已不存在，届时再 stat 会失败。报告里的「未归类明细」依赖此值。
        try:
            mtime_text = datetime.fromtimestamp(src.stat().st_mtime).isoformat(
                timespec="seconds"
            )
        except OSError:
            mtime_text = ""

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            # shutil.move 在跨文件系统时自动降级为复制后删除，比 Path.rename 更健壮。
            shutil_move(str(src), str(dst))
            result.moved.append(
                ArchiveAction(
                    src=str(src),
                    dst=str(dst),
                    category=category,
                    mtime=mtime_text,
                )
            )
            result.categories.add(category)
        except (OSError, PermissionError) as exc:
            result.skipped.append((str(src), f"移动失败：{type(exc).__name__}: {exc}"))

    # 仅在确有文件移动时写入日志，避免产生可撤销但无实际内容的记录。
    if result.moved:
        entry = JournalEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            operation="archive",
            actions=result.moved,
        )
        append_journal_entry(root_path, entry)

    return result


def _archive_one_by_segments(
    src: Path,
    root_path: Path,
    template: str,
    keywords: Sequence[str] | None,
    result: ArchiveResult,
    index: int,
    total: int,
    progress: Callable[[int, int, str], None] | None,
) -> None:
    """按段归档单个文件，命中多段时产生多份副本。

    这是 ``mode="segment"`` 的单文件处理逻辑，从 :func:`archive` 的主循环中
    抽出，避免把多目标特有的复制/回滚细节混进单目标主流程。

    落盘策略：

    1. 文件名的每一段与关键词表比对，得到命中列表（保持出现顺序）；
    2. 一个都没命中 → 记入 skipped，文件原地不动；
    3. 命中一处 → 与普通的单目标归档完全相同，用 ``shutil.move``；
    4. 命中多处 → 文件本体移入**第一个**命中项，其余命中项各
       ``shutil.copy2`` 一份副本。任一副本创建失败时，把已创建的副本全部
       删除并回滚，保证不留半成品（否则撤销逻辑会面对残缺状态）。

    Args:
        src: 待处理的源文件路径。
        root_path: 被整理的根目录。
        template: 子文件夹命名模板，使用 ``{category}`` 占位符。
        keywords: 关键词表。
        result: 累积结果的 :class:`ArchiveResult`，就地修改。
        index: 当前文件序号（从 1 开始），仅用于进度回调。
        total: 文件总数，仅用于进度回调。
        progress: 进度回调，可为 ``None``。
    """
    matched, reason = classify_by_segments(src, keywords)

    if not matched:
        result.skipped.append((str(src), reason))
        if progress is not None:
            progress(index, total, f"{src.name} → 未命中关键词，跳过")
        return

    # 命中项的文件夹与目标路径，顺序与 matched 一致。
    targets: list[Path] = [
        root_path / template.format(category=category) / src.name
        for category in matched
    ]

    if progress is not None:
        dest_text = "、".join(f"{item}/" for item in matched)
        progress(index, total, f"{src.name} → {dest_text}")

    # 主目标与源文件同处一地时无需任何操作（已在目标文件夹里）。
    try:
        same_location = src.resolve() == targets[0].resolve()
    except OSError:
        same_location = False

    if same_location and len(matched) == 1:
        result.skipped.append((str(src), "文件已位于目标文件夹中，无需移动"))
        return
    if same_location:
        # 主目标就是原地：把它从目标列表里摘掉，只处理其余副本。
        targets = targets[1:]
        if not targets:
            result.skipped.append((str(src), "文件已位于目标文件夹中，无需移动"))
            return

    # 任何一个目标位置已存在同名文件，都放弃整个文件——只做一部分会造成
    # "某些文件夹里有、某些没有"的中间状态，比整体跳过更难排查。
    conflicts = [target for target in targets if target.exists()]
    if conflicts:
        names = "、".join(target.name for target in conflicts)
        result.skipped.append(
            (str(src), f"目标位置已存在同名文件，为避免覆盖已跳过：{names}")
        )
        return

    # 与单目标归档一致：mtime 必须在文件移动前采集。
    try:
        mtime_text = datetime.fromtimestamp(src.stat().st_mtime).isoformat(
            timespec="seconds"
        )
    except OSError:
        mtime_text = ""

    primary = targets[0]
    extras = targets[1:]

    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        for extra in extras:
            extra.parent.mkdir(parents=True, exist_ok=True)

        # 先移动本体到主目标，再复制出其余副本。
        shutil_move(str(src), str(primary))

        created: list[Path] = []
        for extra in extras:
            # copy2 保留修改时间等元数据，使各副本的 mtime 与本体一致。
            shutil_copy2(str(primary), str(extra))
            created.append(extra)

        result.moved.append(
            ArchiveAction(
                src=str(src),
                dst=str(primary),
                category=matched[0],
                mtime=mtime_text,
                copies=[str(path) for path in created],
            )
        )
        for category in matched:
            result.categories.add(category)
    except (OSError, PermissionError) as exc:
        # 回滚：删除已创建的副本，并把本体放回原位，避免留下半成品。
        for path in [primary, *extras]:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        try:
            if primary.exists():
                shutil_move(str(primary), str(src))
        except OSError:
            pass
        result.skipped.append(
            (str(src), f"多目标归档失败（已回滚）：{type(exc).__name__}: {exc}")
        )


def build_report(
    result: ArchiveResult,
    root: str | Path,
    extra_info: dict | None = None,
    mode: str = "semester",
) -> str:
    """生成整理报告文本。

    报告包含汇总、分类统计、跳过明细、未归类明细、不在作业表（按作业名
    归档时）、未命中关键词（按段归类时）、移动明细等区块。汇总同时给出
    处理数量与跳过数量，跳过明细逐条给出原因。

    「未归类明细」的提示语随 ``mode`` 变化：按学期归档时列出每个文件的
    **修改时间**，便于用户判断该补哪一段学期区间；按作业名归档时则提示
    文件名中缺少作业名。

    Args:
        result: :func:`archive` 返回的结果。
        root: 被整理的根目录。
        extra_info: 附加信息键值对，输出在报告头部。
        mode: 归档模式，``"extension"`` / ``"semester"`` / ``"title"`` /
            ``"segment"``，用于决定各明细区块的措辞与内容。

    Returns:
        多行报告文本。
    """
    root_path = Path(root)
    mode_normalized = str(mode or "").strip().lower()
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("           作业文件整理报告")
    lines.append("=" * 70)
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"整理目录：{root_path}")

    if extra_info:
        for key, value in extra_info.items():
            lines.append(f"{key}：{value}")

    lines.append("")
    lines.append("-" * 70)
    lines.append("【汇总】")
    lines.append("-" * 70)
    lines.append(f"  成功移动：{len(result.moved)} 个")
    lines.append(f"  跳过未动：{len(result.skipped)} 个")
    lines.append(f"  本次合计：{len(result.moved) + len(result.skipped)} 个")

    if result.categories:
        lines.append("")
        lines.append("-" * 70)
        lines.append("【分类统计】")
        lines.append("-" * 70)

        counter: dict[str, int] = {}
        for action in result.moved:
            counter[action.category] = counter.get(action.category, 0) + 1

        # 按数量降序排列，文件最多的分类优先展示。
        for category, count in sorted(counter.items(), key=lambda item: -item[1]):
            lines.append(f"  {category}：{count} 个")

    lines.append("")
    lines.append("-" * 70)
    lines.append("【跳过明细】")
    lines.append("-" * 70)
    if result.skipped:
        for i, (path_str, reason) in enumerate(result.skipped, start=1):
            lines.append(f"  {i:>3}. {Path(path_str).name}")
            lines.append(f"       原因：{reason}")
    else:
        lines.append("  （无跳过文件，本次全部处理成功）")

    _append_unclassified_section(lines, result, mode=mode_normalized)
    _append_unlisted_title_section(lines, result)
    _append_unmatched_section(lines, result)

    lines.append("")
    lines.append("-" * 70)
    lines.append("【移动明细】")
    lines.append("-" * 70)
    if result.moved:
        for i, action in enumerate(result.moved, start=1):
            src_name = Path(action.src).name
            dst_dir_name = Path(action.dst).parent.name
            lines.append(f"  {i:>3}. {src_name}  →  {dst_dir_name}/")
            # 多目标时把其余落点也列出来，用户才能知道文件被复制到了哪几处。
            for copy_str in action.copies:
                copy_dir_name = Path(copy_str).parent.name
                lines.append(f"       副本  →  {copy_dir_name}/")
    else:
        lines.append("  （无文件被移动）")

    lines.append("")
    lines.append("=" * 70)
    lines.append("提示：如需回滚本次整理，请使用「撤销上次操作」功能。")
    lines.append("=" * 70)

    return "\n".join(lines)


def _append_unclassified_section(
    lines: list[str],
    result: ArchiveResult,
    mode: str = "semester",
) -> None:
    """向报告中追加「未归类明细」区块。

    仅在确有文件被归入 :data:`UNCLASSIFIED_NAME` 时输出。提示语随归档模式
    而变——按学期归档时是"修改时间落在区间外"，按作业名归档时则是"文件名里
    识别不出作业名"，两者的处理办法完全不同，不能共用一套说辞。

    Args:
        lines: 报告文本的行列表，直接在其后追加。
        result: :func:`archive` 返回的结果。
        mode: 归档模式，``"semester"`` 或 ``"title"``，决定提示语措辞。
    """
    unclassified = [
        action for action in result.moved if action.category == UNCLASSIFIED_NAME
    ]
    if not unclassified:
        return

    lines.append("")
    lines.append("-" * 70)
    lines.append("【未归类明细】")
    lines.append("-" * 70)

    if mode == "title":
        lines.append("  按作业名归档时无法从下列文件名中识别出作业名，已归入")
        lines.append("  「未归类」文件夹。请检查文件名是否缺少作业名，或改用")
        lines.append("  「批量改名」先统一命名后再归档：")
        lines.append("")
        for i, action in enumerate(unclassified, start=1):
            lines.append(f"  {i:>3}. {Path(action.src).name}")
        lines.append("")
        lines.append("  处理办法：确认文件名中包含作业名（可对照「作业名表」），")
        lines.append("  用「批量改名」规范命名后重新归档。")
        return

    lines.append("  以下文件的修改时间不落在任何已配置的学期区间内，")
    lines.append("  它们的实际修改时间如下，可据此补充学期区间后重新归档：")
    lines.append("")

    for i, action in enumerate(unclassified, start=1):
        lines.append(f"  {i:>3}. {Path(action.src).name}")
        lines.append(f"       修改时间：{action.mtime_text()}")

    lines.append("")
    lines.append("  处理办法：在「学期区间设置」中补上覆盖上述时间的区间，")
    lines.append("  再执行一次归档即可把文件移入对应学期文件夹。")


def _append_unlisted_title_section(lines: list[str], result: ArchiveResult) -> None:
    """向报告中追加「不在作业表」区块。

    仅在按作业名归档、且确有文件因不在白名单内而被跳过时输出。这些文件
    **未被移动**，仍在原位置，报告在此列出它们供用户核对：是漏填了作业名，
    还是文件名里的作业名写错了。

    Args:
        lines: 报告文本的行列表，直接在其后追加。
        result: :func:`archive` 返回的结果。
    """
    unlisted = [
        (path_str, reason)
        for path_str, reason in result.skipped
        if "不在作业表" in reason
    ]
    if not unlisted:
        return

    lines.append("")
    lines.append("-" * 70)
    lines.append("【不在作业表 · 未移动】")
    lines.append("-" * 70)
    lines.append("  以下文件识别出了作业名，但该作业名不在你提供的作业名表中，")
    lines.append("  按设定**未被移动**，仍保留在原文件夹。请核对：")
    lines.append("  · 若是新的作业，把它补进作业名表后重新归档；")
    lines.append("  · 若是文件名里的作业名写错了，先用「批量改名」修正再归档。")
    lines.append("")

    for i, (path_str, reason) in enumerate(unlisted, start=1):
        lines.append(f"  {i:>3}. {Path(path_str).name}")
        lines.append(f"       原因：{reason}")

    lines.append("")


def _append_unmatched_section(lines: list[str], result: ArchiveResult) -> None:
    """向报告中追加「未命中关键词」区块（按段归类专用）。

    仅在 ``mode="segment"`` 且确有文件的所有段都没命中关键词表时输出。
    这些文件**未被移动**，仍在原位置，报告在此列出它们供用户核对：
    是关键词表漏填了，还是文件名本身不规范。

    Args:
        lines: 报告文本的行列表，直接在其后追加。
        result: :func:`archive` 返回的结果。
    """
    unmatched = [
        (path_str, reason)
        for path_str, reason in result.skipped
        if "没有段命中关键词表" in reason
    ]
    if not unmatched:
        return

    lines.append("")
    lines.append("-" * 70)
    lines.append("【未命中关键词 · 未移动】")
    lines.append("-" * 70)
    lines.append("  以下文件的**任何一段**都不在你的关键词表中，按设定未被移动，")
    lines.append("  仍保留在原文件夹。请核对：")
    lines.append("  · 若是新的作业/学生，把对应内容补进关键词表后重新归类；")
    lines.append("  · 若是文件名本身不规范，先用「批量改名」整理后再归类。")
    lines.append("")

    for i, (path_str, reason) in enumerate(unmatched, start=1):
        lines.append(f"  {i:>3}. {Path(path_str).name}")
        lines.append(f"       原因：{reason}")

    lines.append("")


def save_report(
    report_text: str,
    root: str | Path,
    filename: str | None = None,
) -> Path:
    """将报告保存至 ``.homework_organizer/reports/`` 目录。

    报告需要留档以备核对，故保存为文件而非仅在界面显示。默认文件名包含
    时间戳，避免多次整理互相覆盖。

    Args:
        report_text: 报告正文。
        root: 被整理的根目录。
        filename: 目标文件名。``None`` 时按当前时间生成。

    Returns:
        报告文件路径。
    """
    root_path = Path(root)
    reports_dir = root_path / JOURNAL_DIR_NAME / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    if filename is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"整理报告-{stamp}.txt"

    path = reports_dir / filename
    path.write_text(report_text, encoding="utf-8")
    return path


def undo_last(
    root: str | Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> UndoResult:
    """撤销最近一次归档，将文件移回原位置，会修改磁盘上的文件位置。

    读取日志中最新一条记录，反向遍历其动作列表，逐项将文件由归档位置移回
    原位置。原位置已存在同名文件时跳过。撤销成功后将对应日志记录移除，
    以避免重复处理同一条记录。

    按段归类（多目标）产生的**副本**会在还原本体之前先被删除——副本是本次
    操作凭空造出来的，不属于用户原有文件，不删就会在文件夹里越堆越多。

    Args:
        root: 被整理的根目录。
        progress: 进度回调，签名为 ``(当前序号, 总数, 说明文本)``。

    Returns:
        含还原明细与失败原因的 :class:`UndoResult`。
    """
    root_path = Path(root)
    result = UndoResult()

    entries = load_journal(root_path)
    if not entries:
        result.failed.append(("", "没有找到任何操作记录，无法撤销"))
        return result

    last = entries[-1]
    result.entry_timestamp = last.timestamp

    for i, action in enumerate(reversed(last.actions), start=1):
        src_path = Path(action.src)
        dst_path = Path(action.dst)

        if progress is not None:
            progress(i, len(last.actions), f"还原 {dst_path.name}")

        # 先删副本：它们是本次操作复制出来的，无论本体能否还原都应清理干净。
        for copy_str in action.copies:
            copy_path = Path(copy_str)
            try:
                if copy_path.exists():
                    copy_path.unlink()
                    result.removed_copies.append(str(copy_path))
            except OSError as exc:
                result.failed.append(
                    (str(copy_path), f"副本删除失败：{type(exc).__name__}: {exc}")
                )

        if not dst_path.exists():
            result.failed.append(
                (str(dst_path), "文件已不在归档位置（可能被手动移动或删除），无法还原")
            )
            continue

        if src_path.exists():
            result.failed.append(
                (str(src_path), f"原位置已存在同名文件，为避免覆盖已跳过：{src_path.name}")
            )
            continue

        try:
            src_path.parent.mkdir(parents=True, exist_ok=True)
            shutil_move(str(dst_path), str(src_path))
            result.restored.append((str(dst_path), str(src_path)))
        except (OSError, PermissionError) as exc:
            result.failed.append(
                (str(dst_path), f"还原失败：{type(exc).__name__}: {exc}")
            )

    # 仅在确有文件还原时移除日志记录；全部失败则保留，供用户重试。
    if result.restored:
        save_journal(root_path, entries[:-1])

    return result


def format_undo_result(result: UndoResult) -> str:
    """将撤销结果渲染为可读文本。

    Args:
        result: :func:`undo_last` 返回的结果。

    Returns:
        多行文本。
    """
    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("           撤销操作结果")
    lines.append("=" * 70)

    if result.entry_timestamp:
        lines.append(f"撤销的操作记录时间：{result.entry_timestamp}")
    lines.append("")

    lines.append("-" * 70)
    lines.append("【已还原的文件】")
    lines.append("-" * 70)
    if result.restored:
        for i, (new_pos, old_pos) in enumerate(result.restored, start=1):
            lines.append(f"  {i:>3}. {Path(new_pos).name}")
            lines.append(f"       已还原至：{Path(old_pos).parent}")
    else:
        lines.append("  （无）")

    # 副本区块仅在确有多目标副本被删除时出现，避免单目标归档下多出无用小节。
    if result.removed_copies:
        lines.append("")
        lines.append("-" * 70)
        lines.append("【已删除的副本】")
        lines.append("-" * 70)
        lines.append("  以下文件是归类时复制出来的副本，撤销时已被清理：")
        lines.append("")
        for i, copy_str in enumerate(result.removed_copies, start=1):
            copy_display = f"{Path(copy_str).parent.name}/{Path(copy_str).name}"
            lines.append(f"  {i:>3}. {copy_display}")

    lines.append("")
    lines.append("-" * 70)
    lines.append("【还原失败的】")
    lines.append("-" * 70)
    if result.failed:
        for i, (path_str, reason) in enumerate(result.failed, start=1):
            label = Path(path_str).name if path_str else "（全局）"
            lines.append(f"  {i:>3}. {label}")
            lines.append(f"       原因：{reason}")
    else:
        lines.append("  （无）")

    lines.append("")
    lines.append("-" * 70)
    summary = f"汇总：还原 {len(result.restored)} 个 | 失败 {len(result.failed)} 个"
    if result.removed_copies:
        summary += f" | 清理副本 {len(result.removed_copies)} 个"
    lines.append(summary)
    lines.append("=" * 70)

    return "\n".join(lines)


def list_history(root: str | Path) -> list[JournalEntry]:
    """列出全部历史操作记录。

    Args:
        root: 被整理的根目录。

    Returns:
        按时间从新到旧排列的日志条目列表。
    """
    return list(reversed(load_journal(root)))
