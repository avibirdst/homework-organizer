"""需求 1：扫描与列出。

本模块负责扫描指定目录并输出文件清单，包含文件大小、修改时间，支持按
扩展名过滤。本模块为纯只读实现，不修改任何文件。

职责划分：
    - ``scan.py``      只读：扫描目录、读取文件属性。
    - ``rename.py``    写入：批量改名（需先预览并确认）。
    - ``archive.py``   写入：归档移动、生成报告、撤销操作；作业归类。
    - ``wordcount.py`` 只读：统计文档字数、按检索信息汇总。

将只读操作与写入操作分离，可保证即使扫描逻辑存在缺陷，也不会造成文件
被删除或改动的后果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "ScanRecord",
    "scan_directory",
    "format_records",
]


@dataclass
class ScanRecord:
    """磁盘上单个文件在某一时刻的属性快照。

    将文件属性读取为独立的数据对象，后续改名与归档逻辑均基于该快照计算，
    避免因文件在处理过程中被外部修改或删除而导致逻辑不一致。
    """

    path: Path
    """文件的完整路径。"""

    name: str
    """文件名，含扩展名。"""

    suffix: str
    """扩展名，统一为小写并带前导点，例如 ``.docx``。"""

    size_bytes: int
    """文件大小，单位为字节。"""

    modified_at: datetime
    """最后修改时间，为本地时区的 ``datetime`` 对象。"""

    word_count: int | None = None
    """文档字数。``None`` 表示尚未统计或该格式不支持统计。"""

    @property
    def size_human(self) -> str:
        """返回人类可读的文件大小文本。

        采用 1024 进制换算，单位为 B / KB / MB / GB / TB。字节单位不保留
        小数，其余单位保留一位小数。

        Returns:
            格式化后的大小文本，例如 ``"1.5 KB"``。
        """
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{self.size_bytes} B"

    @property
    def modified_human(self) -> str:
        """返回格式化后的修改时间文本。

        精度取到秒，不包含微秒。

        Returns:
            格式为 ``YYYY-MM-DD HH:MM:SS`` 的时间文本。
        """
        return self.modified_at.strftime("%Y-%m-%d %H:%M:%S")


def scan_directory(
    root: str | Path,
    extensions: Sequence[str] | None = None,
    recursive: bool = False,
) -> list[ScanRecord]:
    """扫描目录，返回其中所有文件的属性快照。

    Args:
        root: 待扫描的目录。
        extensions: 扩展名白名单，例如 ``[".docx", ".pdf"]``。传入 ``None``
            或空序列表示不过滤。元素可带或不带前导点，大小写不敏感。
        recursive: 是否递归扫描子目录。``False`` 时仅扫描 ``root`` 本层。

    Returns:
        按修改时间升序排列的 :class:`ScanRecord` 列表；修改时间相同时按
        文件名排序。

    Raises:
        NotADirectoryError: ``root`` 不存在，或存在但不是目录。此处有意
            抛出异常而非返回空列表，避免用户将路径错误误认为目录为空。
    """
    root_path = Path(root)

    if not root_path.exists():
        raise NotADirectoryError(f"目录不存在：{root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"这不是一个目录：{root_path}")

    wanted = _normalize_extensions(extensions)

    iterator: Iterable[Path] = (
        root_path.rglob("*") if recursive else root_path.glob("*")
    )

    records: list[ScanRecord] = []
    for entry in iterator:
        # 跳过目录，并忽略因权限或符号链接损坏导致的读取异常，
        # 使单个异常条目不影响整体扫描。
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue

        if wanted and entry.suffix.lower() not in wanted:
            continue

        try:
            stat = entry.stat()
        except OSError:
            continue

        records.append(
            ScanRecord(
                path=entry,
                name=entry.name,
                suffix=entry.suffix.lower(),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
            )
        )

    records.sort(key=lambda record: (record.modified_at, record.name))
    return records


def _normalize_extensions(extensions: Sequence[str] | None) -> set[str]:
    """将扩展名序列标准化为小写并带前导点的集合。

    标准化使 ``".PDF"``、``"pdf"``、``" .Pdf "`` 等写法等价，
    避免调用方需要记忆入参格式。

    Args:
        extensions: 原始扩展名序列，可为 ``None``。

    Returns:
        标准化后的扩展名集合；输入为空时返回空集合，表示不过滤。
    """
    if not extensions:
        return set()

    normalized: set[str] = set()
    for raw in extensions:
        cleaned = raw.strip().lower().lstrip(".")
        if not cleaned:
            continue
        normalized.add("." + cleaned)
    return normalized


def format_records(
    records: Sequence[ScanRecord],
    show_index: bool = True,
) -> str:
    """将扫描结果渲染为等宽对齐的纯文本表格。

    该函数为命令行输出与测试用例共用的渲染入口；图形界面使用表格控件
    自行排版，不依赖此函数。

    Args:
        records: 待渲染的扫描记录序列。
        show_index: 是否在最左侧输出序号列。

    Returns:
        可直接打印的多行文本；``records`` 为空时返回提示文本。
    """
    if not records:
        return "（没有扫描到任何文件）"

    index_col = [str(i) for i in range(1, len(records) + 1)]
    name_col = [record.name for record in records]
    size_col = [record.size_human for record in records]
    time_col = [record.modified_human for record in records]
    # 字数未统计时以 "-" 表示"未知"，与"零字"区分。
    count_col = [
        str(record.word_count) if record.word_count is not None else "-"
        for record in records
    ]

    def _width(header: str, values: list[str]) -> int:
        """计算某一列的对齐宽度。

        Args:
            header: 该列的标题文字。
            values: 该列所有单元格的文字。

        Returns:
            表头与全部单元格中的最大字符数，用作该列的显示宽度。
        """
        return max(len(header), *(len(value) for value in values))

    w_index = _width("序号", index_col)
    w_name = _width("文件名", name_col)
    w_size = _width("大小", size_col)
    w_time = _width("修改时间", time_col)
    w_count = _width("字数", count_col)

    lines: list[str] = []
    if show_index:
        header = (
            f"{'序号':<{w_index}}  {'文件名':<{w_name}}  "
            f"{'大小':>{w_size}}  {'修改时间':<{w_time}}  {'字数':>{w_count}}"
        )
    else:
        header = (
            f"{'文件名':<{w_name}}  {'大小':>{w_size}}  "
            f"{'修改时间':<{w_time}}  {'字数':>{w_count}}"
        )
    lines.append(header)
    lines.append("-" * len(header))

    for i, record in enumerate(records):
        if show_index:
            lines.append(
                f"{index_col[i]:<{w_index}}  {name_col[i]:<{w_name}}  "
                f"{size_col[i]:>{w_size}}  {time_col[i]:<{w_time}}  "
                f"{count_col[i]:>{w_count}}"
            )
        else:
            lines.append(
                f"{name_col[i]:<{w_name}}  {size_col[i]:>{w_size}}  "
                f"{time_col[i]:<{w_time}}  {count_col[i]:>{w_count}}"
            )

    lines.append("")
    lines.append(f"共 {len(records)} 个文件")
    return "\n".join(lines)
