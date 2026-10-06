"""命令行入口。

适用于快速查看目录内容、脚本化批量处理，以及无图形界面的运行环境。

用法示例::

    # 扫描并列出
    python cli.py scan E:/计协应聘

    # 仅扫描 pdf 与 docx
    python cli.py scan E:/计协应聘 --ext .pdf .docx

    # 预览改名结果（不修改文件）
    python cli.py rename E:/计协应聘

    # 原文件名是「姓名_学号_作业名」而非默认顺序时，用 --source-format 指定
    python cli.py rename E:/计协应聘 --source-format name-id-title

    # 提供学生信息表，按学号补出姓名
    python cli.py rename E:/计协应聘 --roster 名单.txt --template "{title}_{id}_{name}"

    # 查看信息表示例内容
    python cli.py rename E:/计协应聘 --show-roster-sample

    # 实际执行改名（须显式附加 --apply --yes）
    python cli.py rename E:/计协应聘 --apply --yes

    # 字数检查
    python cli.py wordcount E:/计协应聘 --min 500

    # 归档与撤销
    python cli.py archive E:/计协应聘 --mode extension --yes
    python cli.py archive E:/计协应聘 --mode title --title-file 作业名表.txt --yes
    python cli.py archive E:/计协应聘 --mode segment --keyword-file 关键词表.txt --yes
    python cli.py undo E:/计协应聘 --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from homework_organizer.core import archive as archive_core  # noqa: E402
from homework_organizer.core import rename as rename_core  # noqa: E402
from homework_organizer.core import roster as roster_core  # noqa: E402
from homework_organizer.core import scan as scan_core  # noqa: E402
from homework_organizer.core import wordcount as wordcount_core  # noqa: E402


#: 原文件名格式的候选值到字段元组的映射。
#:
#: 键用作 ``--source-format`` 的取值，以连字符连接各段，例如 ``id-name-title``
#: 对应「学号_姓名_作业名」。取值用字符串而非元组，是为了让 ``argparse`` 的
#: ``choices`` 能直接校验，并在 ``--help`` 中列出全部选项。
#:
#: ``auto`` 是自动识别模式（推荐）：按段数与各段内容类型套用规则表，无需预先
#: 知道文件名结构。
_FORMAT_LABELS: dict[str, tuple[str, ...]] = {
    "auto": rename_core.AUTO_SOURCE_FORMAT,
    "id-name-title": ("id", "name", "title"),
    "name-id-title": ("name", "id", "title"),
    "id-title": ("id", "title"),
    "name-title": ("name", "title"),
    "title-id": ("title", "id"),
    "title-name": ("title", "name"),
    "id-name-title-extra": ("id", "name", "title", "extra"),
}


def _default_format_label() -> str:
    """返回 ``--source-format`` 的默认取值。

    默认使用自动识别，适用面最广，且与图形界面的默认选项保持一致。

    Returns:
        固定返回 ``"auto"``。
    """
    return "auto"


def _cmd_scan(args: argparse.Namespace) -> int:
    """执行 ``scan`` 子命令：扫描并列出文件。

    Args:
        args: 命令行参数，使用 ``directory``、``ext``、``recursive``。

    Returns:
        进程退出码。
    """
    try:
        records = scan_core.scan_directory(
            args.directory,
            extensions=args.ext,
            recursive=args.recursive,
        )
    except NotADirectoryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(scan_core.format_records(records))
    return 0


def _cmd_rename(args: argparse.Namespace) -> int:
    """执行 ``rename`` 子命令：预览或执行批量改名。

    未指定 ``--apply`` 时仅输出预览；指定 ``--apply`` 后仍需 ``--yes``
    确认方才执行。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码。
    """
    if args.show_roster_sample:
        print("信息表示例（保存为 .txt 后用 --roster 指定，或直接照此格式填写）：")
        print("")
        print(roster_core.SAMPLE_ROSTER_TEXT)
        return 0

    if args.show_title_sample:
        print(
            "作业白名单示例（保存为 .txt 后用 --title-whitelist 指定，"
            "或直接照此格式填写）："
        )
        print("")
        print(archive_core.SAMPLE_TITLE_WHITELIST_TEXT)
        return 0

    try:
        records = scan_core.scan_directory(args.directory)
    except NotADirectoryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    source_format = _FORMAT_LABELS[args.source_format]

    # 作业白名单是可选项，且只在自动识别模式下有意义。
    preferred_titles = None
    if args.title_whitelist:
        try:
            title_text = Path(args.title_whitelist).read_text(encoding="utf-8-sig")
        except OSError as exc:
            print(f"错误：无法读取作业白名单文件：{exc}", file=sys.stderr)
            return 1
        preferred_titles = archive_core.parse_title_whitelist_text(title_text)
        if not preferred_titles:
            print(
                "错误：作业白名单为空（只有注释或空行），至少需要一个作业名。",
                file=sys.stderr,
            )
            return 1
        if tuple(source_format) == rename_core.AUTO_SOURCE_FORMAT:
            print(
                f"作业白名单：已载入 {len(preferred_titles)} 个作业名"
                f"（命中白名单的段将被优先认定为作业名）"
            )
        else:
            # 固定排列下作业名由字段位置决定，白名单不参与，明确告知用户。
            print(
                f"提示：已读取 {len(preferred_titles)} 个作业名，但当前原格式为固定排列，"
                "作业白名单只在 --source-format auto 时生效，本次不参与判断。"
            )

    # 信息表是可选项：不提供时直接使用文件名中自带的学号姓名。
    roster = None
    if args.roster:
        try:
            roster = roster_core.load_roster_file(args.roster)
        except roster_core.RosterError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        print(f"信息表：已载入 {len(roster)} 条记录（{args.roster}）")
    else:
        print("信息表：未提供（如需按学号补姓名，请用 --roster 指定 TXT 文件）")

    plan = rename_core.calculate_rename_plan(
        records,
        template=args.template,
        source_format=source_format,
        roster=roster,
        preferred_titles=preferred_titles,
    )
    print(rename_core.format_plan(plan))

    if not args.apply:
        print("")
        print("（当前仅为预览，未修改任何文件。要真正执行请加 --apply --yes）")
        return 0

    if not args.yes:
        print("")
        print(
            "错误：--apply 会真正修改文件名，必须同时加上 --yes 才执行。",
            file=sys.stderr,
        )
        return 1

    print("")
    print("正在执行改名...")
    succeeded, failed = rename_core.apply_rename_plan(plan)

    for action in succeeded:
        print(f"  ✓ {action.old_name}  →  {action.new_name}")
    for path, reason in failed:
        print(f"  ✗ {path.name}：{reason}")

    print("")
    print(f"完成：成功 {len(succeeded)} 个，失败 {len(failed)} 个。")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    """执行 ``archive`` 子命令：归档文件并生成报告。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码。
    """
    try:
        records = scan_core.scan_directory(args.directory)
    except NotADirectoryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    # 按学期归档时才读取区间文件；读不到或格式有误都直接报错退出，
    # 避免用错误的区间静默地把文件全归进「未归类」。
    semester_ranges = None
    if args.mode == "semester":
        if args.semester_file:
            try:
                text = Path(args.semester_file).read_text(encoding="utf-8-sig")
            except OSError as exc:
                print(f"错误：无法读取学期区间文件：{exc}", file=sys.stderr)
                return 1
        else:
            text = archive_core.SAMPLE_SEMESTER_TEXT
            print("（未指定 --semester-file，使用内置默认学期区间）")

        try:
            semester_ranges = archive_core.parse_semester_ranges_text(text)
        except ValueError as exc:
            print(f"错误：学期区间格式有误 —— {exc}", file=sys.stderr)
            return 1

        if not semester_ranges:
            print("错误：学期区间为空，至少需要一条。", file=sys.stderr)
            return 1

        print(f"学期区间共 {len(semester_ranges)} 条：")
        for name, start, end in semester_ranges:
            print(f"  {name}：{start} ~ {end}")

    # 按作业名归档时才读取作业名表；不指定则视为不设限制。
    title_whitelist = None
    if args.mode == "title":
        if args.title_file:
            try:
                title_text = Path(args.title_file).read_text(encoding="utf-8-sig")
            except OSError as exc:
                print(f"错误：无法读取作业名表文件：{exc}", file=sys.stderr)
                return 1
            title_whitelist = archive_core.parse_title_whitelist_text(title_text)
            if not title_whitelist:
                print(
                    "错误：作业名表为空（只有注释或空行），至少需要一个作业名。",
                    file=sys.stderr,
                )
                return 1
            print(f"作业名表共 {len(title_whitelist)} 条：{'、'.join(title_whitelist)}")
        else:
            print("（未指定 --title-file，不设限制，识别出什么作业名就按什么归类）")

    # 按段归类时才读取关键词表。与 title 模式不同，这里的关键词表是**必需**的：
    # 没有表就无从比对，程序会直接报错退出，而不是静默地把文件全留在原地。
    keywords = None
    if args.mode == "segment":
        if args.keyword_file:
            try:
                keyword_text = Path(args.keyword_file).read_text(encoding="utf-8-sig")
            except OSError as exc:
                print(f"错误：无法读取关键词表文件：{exc}", file=sys.stderr)
                return 1
        else:
            keyword_text = archive_core.SAMPLE_KEYWORD_LIST_TEXT
            print("（未指定 --keyword-file，使用内置示例关键词表）")

        keywords = archive_core.parse_keyword_list_text(keyword_text)
        if not keywords:
            print(
                "错误：关键词表为空（只有注释或空行），按段归类至少需要一项关键词。",
                file=sys.stderr,
            )
            return 1
        print(f"关键词表共 {len(keywords)} 项：{'、'.join(keywords)}")

    print(f"准备按「{args.mode}」模式归档 {len(records)} 个文件。")

    if not args.yes:
        print("（当前仅为预览。要真正执行请加 --yes）")
        return 0

    result = archive_core.archive(
        records,
        args.directory,
        mode=args.mode,
        semester_ranges=semester_ranges,
        title_whitelist=title_whitelist,
        keywords=keywords,
    )
    report = archive_core.build_report(result, args.directory, mode=args.mode)
    print(report)

    saved = archive_core.save_report(report, args.directory)
    print("")
    print(f"报告已保存至：{saved}")
    return 0


def _cmd_undo(args: argparse.Namespace) -> int:
    """执行 ``undo`` 子命令：撤销最近一次归档。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码。
    """
    if not args.yes:
        print("撤销会移动文件。确认请加 --yes。")
        return 0

    result = archive_core.undo_last(args.directory)
    print(archive_core.format_undo_result(result))
    return 0


def _cmd_wordcount(args: argparse.Namespace) -> int:
    """执行 ``wordcount`` 子命令：作业检查。

    统计字数，并按「检索信息表」逐条汇总识别/达标/未达标/读取失败份数。
    存在标红（未通过）的检索信息时，以退出码 ``2`` 结束，便于脚本判定。

    Args:
        args: 命令行参数。

    Returns:
        进程退出码：``0`` 全绿，``1`` 参数/目录错误，``2`` 存在标红项。

    Raises:
        SystemExit: 当 ``--show-info-sample`` 被指定时，打印示例后退出。
    """
    # 先处理「打印示例」这类不需要扫描目录的请求。
    if args.show_info_sample:
        print(wordcount_core.SAMPLE_CHECK_INFO_TEXT)
        return 0

    try:
        records = scan_core.scan_directory(args.directory, extensions=args.ext)
    except NotADirectoryError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    # 读取检索信息表（缺省时为空表，此时只出明细、不产生汇总行）。
    keywords: list[str] = []
    if args.info_file:
        try:
            text = Path(args.info_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"错误：无法读取检索信息表 {args.info_file}：{exc}", file=sys.stderr)
            return 1
        keywords = archive_core.parse_keyword_list_text(text)

    print(f"正在统计 {len(records)} 个文件的字数（要求不低于 {args.min} 字）...")
    print("")

    results, _ = wordcount_core.scan_documents_wordcount(
        records,
        threshold=args.min,
    )

    # 有检索信息时才构建汇总，避免生成一张空表。
    summary = (
        wordcount_core.build_homework_check(results, keywords, threshold=args.min)
        if keywords
        else None
    )

    print(
        wordcount_core.build_wordcount_report(
            results,
            threshold=args.min,
            root=args.directory,
            summary=summary,
        )
    )

    # 命令行无法呈现颜色，另附纯文本的不合格清单。
    below = [r for r in results if r.is_ok and r.below_threshold]
    if below:
        print("")
        print("【不合格清单（图形界面中显示为红色）】")
        for result in below:
            print(f"  ! {result.path.name}  {result.count} 字")

    # 存在标红项即以 2 退出，让脚本能直接判定「检查未通过」。
    if summary is not None and summary.red_rows:
        red_names = "、".join(row.keyword for row in summary.red_rows)
        print("")
        print(f"检查未通过：{red_names}", file=sys.stderr)
        return 2

    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。

    Returns:
        配置完成的解析器，包含全部子命令定义。
    """
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="作业文件批量整理工具（命令行版）",
    )

    # required=True 限定必须指定一个子命令。
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描并列出文件")
    p_scan.add_argument("directory", help="要扫描的目录")
    p_scan.add_argument(
        "--ext",
        nargs="*",
        default=None,
        help="扩展名过滤，例如 --ext .pdf .docx",
    )
    p_scan.add_argument("--recursive", action="store_true", help="是否包含子文件夹")
    p_scan.set_defaults(func=_cmd_scan)

    p_rename = sub.add_parser("rename", help="批量改名（默认仅预览）")
    p_rename.add_argument("directory", help="要处理的目录")
    p_rename.add_argument(
        "--template",
        default=rename_core.DEFAULT_TEMPLATE,
        help=f"新文件名模板，默认 {rename_core.DEFAULT_TEMPLATE}",
    )
    p_rename.add_argument(
        "--source-format",
        default=_default_format_label(),
        choices=list(_FORMAT_LABELS),
        help="原文件名格式，用连字符连接各段，可选段：id/name/title/extra。"
             f"默认 {_default_format_label()}",
    )
    p_rename.add_argument(
        "--roster",
        default=None,
        help="学生信息表 TXT 文件路径，用于补全缺失的学号或姓名",
    )
    p_rename.add_argument(
        "--show-roster-sample",
        action="store_true",
        help="打印信息表示例后退出，便于照着填写",
    )
    p_rename.add_argument(
        "--title-whitelist",
        default=None,
        help="作业白名单 TXT 文件（每行一个作业名，# 开头为注释）。"
        "仅在 --source-format auto 时生效；命中白名单的段会被优先认定为作业名",
    )
    p_rename.add_argument(
        "--show-title-sample",
        action="store_true",
        help="打印作业白名单示例后退出，便于照着填写",
    )
    p_rename.add_argument("--apply", action="store_true", help="真正执行改名")
    p_rename.add_argument("--yes", action="store_true", help="确认执行")
    p_rename.set_defaults(func=_cmd_rename)

    p_archive = sub.add_parser("archive", help="归档到子文件夹")
    p_archive.add_argument("directory", help="要整理的目录")
    p_archive.add_argument(
        "--mode",
        choices=["extension", "semester", "title", "segment"],
        default="extension",
        help=(
            "分类方式：extension 按类型，semester 按学期，title 按作业名，"
            "segment 按文件名分段匹配关键词表"
        ),
    )
    p_archive.add_argument(
        "--semester-file",
        help="学期区间 TXT 文件（每行「学期名, 开始日期, 结束日期」）。"
        "仅在 --mode semester 时生效；不指定则使用内置默认区间",
    )
    p_archive.add_argument(
        "--title-file",
        help="作业名表 TXT 文件（每行一个作业名，# 开头为注释）。"
        "仅在 --mode title 时生效；不指定则不设限制。"
        "不在表中的作业名不会被移动，只在报告中提示",
    )
    p_archive.add_argument(
        "--keyword-file",
        help="关键词表 TXT 文件（每行一项，可混写作业名/姓名/学号，# 开头为注释）。"
        "仅在 --mode segment 时生效；不指定则使用内置示例表。"
        "按段归类完全依赖本表，留空会直接报错",
    )
    p_archive.add_argument("--yes", action="store_true", help="确认执行")
    p_archive.set_defaults(func=_cmd_archive)

    p_undo = sub.add_parser("undo", help="撤销上次归档")
    p_undo.add_argument("directory", help="目录")
    p_undo.add_argument("--yes", action="store_true", help="确认执行")
    p_undo.set_defaults(func=_cmd_undo)

    p_wc = sub.add_parser("wordcount", help="作业检查（字数统计 + 按检索信息汇总）")
    p_wc.add_argument("directory", nargs="?", help="要检查的目录")
    p_wc.add_argument("--min", type=int, default=500, help="字数下限，默认 500")
    p_wc.add_argument("--ext", nargs="*", default=None, help="仅检查指定扩展名")
    p_wc.add_argument("--info-file", default=None, help="检索信息表（TXT，每行一条）")
    p_wc.add_argument(
        "--show-info-sample",
        action="store_true",
        help="打印检索信息表的示例内容后退出",
    )
    p_wc.set_defaults(func=_cmd_wordcount)

    return parser


def main() -> int:
    """程序入口。

    Returns:
        进程退出码。
    """
    parser = build_parser()
    args = parser.parse_args()
    # 各子命令通过 set_defaults 绑定处理函数，此处统一调用。
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
