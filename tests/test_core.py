"""核心逻辑单元测试。

基于标准库 ``unittest`` 编写，无需额外依赖。

运行方式（在项目根目录下）::

    python -m unittest tests.test_core -v

测试重点集中于最易出错的分支：

- 扩展名过滤的大小写与格式兼容性
- 改名过程中的不覆盖保证
- 同批次内目标名冲突的拦截
- 归档后撤销的完整性
- 中英文字数统计口径
- 阈值判定的边界条件
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# 将项目根目录加入搜索路径，确保能导入 homework_organizer 包。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from homework_organizer.core import archive as archive_core
from homework_organizer.core import rename as rename_core
from homework_organizer.core import roster as roster_core
from homework_organizer.core import scan as scan_core
from homework_organizer.core import wordcount as wordcount_core


class TempDirTestCase(unittest.TestCase):
    """需要在真实文件系统上运行的测试的基类。

    每个测试方法在独立的临时目录中执行，结束后自动清理，测试之间互不干扰，
    且不会影响用户的真实文件。
    """

    def setUp(self) -> None:
        """创建临时目录。"""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        """清理临时目录及其全部内容。"""
        self._tmp.cleanup()

    def make_file(self, name: str, content: str = "test") -> Path:
        """在临时目录中创建文件。

        Args:
            name: 文件名，可包含子目录路径。
            content: 文件内容。

        Returns:
            创建的文件路径。
        """
        path = self.tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class TestScan(TempDirTestCase):
    """扫描功能的测试。"""

    def test_scan_lists_all_files(self) -> None:
        """扫描应列出目录下的全部文件。"""
        self.make_file("a.txt")
        self.make_file("b.docx")
        self.make_file("c.pdf")

        records = scan_core.scan_directory(self.tmp_path)

        self.assertEqual(len(records), 3)

    def test_scan_only_files_not_directories(self) -> None:
        """扫描应仅返回文件，不包含子目录。"""
        self.make_file("a.txt")
        (self.tmp_path / "subdir").mkdir()

        records = scan_core.scan_directory(self.tmp_path)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "a.txt")

    def test_extension_filter(self) -> None:
        """扩展名过滤应仅保留匹配的文件。"""
        self.make_file("a.txt")
        self.make_file("b.docx")
        self.make_file("c.pdf")

        records = scan_core.scan_directory(self.tmp_path, extensions=[".pdf"])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "c.pdf")

    def test_extension_filter_is_case_insensitive(self) -> None:
        """扩展名过滤应大小写不敏感。

        用户可能传入 ``".PDF"`` 而磁盘上的文件为 ``".pdf"``，若未做小写
        转换则过滤将失效。
        """
        self.make_file("a.txt")
        self.make_file("b.PDF")

        records = scan_core.scan_directory(self.tmp_path, extensions=[".pdf"])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "b.PDF")

    def test_extension_filter_accepts_without_dot(self) -> None:
        """扩展名不带前导点也应被接受。"""
        self.make_file("a.txt")
        self.make_file("b.pdf")

        records = scan_core.scan_directory(self.tmp_path, extensions=["pdf"])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].name, "b.pdf")

    def test_recursive_scan(self) -> None:
        """递归扫描应包含子目录中的文件。"""
        self.make_file("top.txt")
        self.make_file("sub/nested.txt")

        flat = scan_core.scan_directory(self.tmp_path, recursive=False)
        self.assertEqual(len(flat), 1)

        deep = scan_core.scan_directory(self.tmp_path, recursive=True)
        self.assertEqual(len(deep), 2)

    def test_scan_nonexistent_dir_raises(self) -> None:
        """扫描不存在的目录应抛出明确异常。

        选择抛出异常而非返回空列表，以避免用户将路径错误误认为目录为空。
        """
        with self.assertRaises(NotADirectoryError):
            scan_core.scan_directory(self.tmp_path / "不存在的目录")

    def test_size_and_mtime_are_captured(self) -> None:
        """扫描记录应正确捕获文件大小与修改时间。"""
        content = "hello world"
        self.make_file("a.txt", content)

        records = scan_core.scan_directory(self.tmp_path)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.size_bytes, len(content.encode("utf-8")))
        self.assertGreater(record.modified_at.year, 2000)

    def test_size_human_formatting(self) -> None:
        """大小格式化应符合预期。"""
        record = scan_core.ScanRecord(
            path=Path("x"),
            name="x",
            suffix="",
            size_bytes=1536,
            modified_at=datetime.now(),
        )
        self.assertEqual(record.size_human, "1.5 KB")


class TestRenameParsing(unittest.TestCase):
    """文件名解析的测试，无需真实文件。"""

    def test_parse_standard_name(self) -> None:
        """标准格式应被正确拆解。"""
        result = rename_core.parse_structured_name("20230101_张三_第一次作业.pdf")

        self.assertIsNotNone(result)
        student_id, name, assignment, suffix, _, problem = result
        self.assertEqual(student_id, "20230101")
        self.assertEqual(name, "张三")
        self.assertEqual(assignment, "第一次作业")
        self.assertEqual(suffix, ".pdf")
        # 各段语义正常时不应报出问题。
        self.assertEqual(problem, "")

    def test_parse_name_with_underscore_in_assignment(self) -> None:
        """作业名中的下划线应完整保留，不得截断。

        若以 ``[^_]+`` 匹配作业名，``"第一次_实验报告"`` 将被截断为
        ``"第一次"``，造成信息丢失。
        """
        result = rename_core.parse_structured_name("20230101_张三_第一次_实验报告.docx")

        self.assertIsNotNone(result)
        assignment = result[2]
        self.assertEqual(assignment, "第一次_实验报告")

    def test_parse_rejects_irregular_name(self) -> None:
        """不符合规则的文件名应返回 ``None``。"""
        self.assertIsNone(rename_core.parse_structured_name("随便起的名字.txt"))
        self.assertIsNone(rename_core.parse_structured_name("20230401_蒋十六.txt"))

    def test_parse_reports_semantic_problem(self) -> None:
        """声明为学号却写着中文时，应返回语义问题而非 ``None``。

        返回问题（而不是 ``None``）是为了让上层能把这些文件标红提示用户；
        若直接判为"结构不符"，用户就看不到这条警告了。
        """
        result = rename_core.parse_structured_name("学号_张三_作业.txt")

        self.assertIsNotNone(result)
        self.assertIn("不像学号", result[5])

    def test_parse_accepts_alphanumeric_id(self) -> None:
        """含字母的学号应被接受。"""
        result = rename_core.parse_structured_name("2023CS001_张三_作业.pdf")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "2023CS001")

    def test_parse_alternative_order(self) -> None:
        """原格式为「姓名_学号_作业名」时应正确拆解。

        这是需求中明确要求支持的场景：文件名不一定非得是
        「学号_姓名_作业名」这一种排列。
        """
        result = rename_core.parse_structured_name(
            "张三_20230101_第一次作业.pdf",
            source_format=("name", "id", "title"),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "20230101")
        self.assertEqual(result[1], "张三")
        self.assertEqual(result[2], "第一次作业")

    def test_parse_two_segment_format(self) -> None:
        """原格式为两段时应正确拆解，缺失的一段留空。"""
        result = rename_core.parse_structured_name(
            "20230101_第一次作业.docx",
            source_format=("id", "title"),
        )

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "20230101")
        self.assertEqual(result[1], "")
        self.assertEqual(result[2], "第一次作业")

    def test_looks_like_id(self) -> None:
        """学号识别应接受数字与字母数字混排，拒绝中文。"""
        self.assertTrue(rename_core.looks_like_id("20230101"))
        self.assertTrue(rename_core.looks_like_id("2023CS001"))
        self.assertFalse(rename_core.looks_like_id("张三"))
        self.assertFalse(rename_core.looks_like_id("第一次作业"))
        self.assertFalse(rename_core.looks_like_id(""))

    def test_looks_like_name(self) -> None:
        """姓名识别应拒绝含数字或作业名特征词的文本。

        不按长度设限，以免复姓、少数民族姓名等被误判为作业名。
        """
        self.assertTrue(rename_core.looks_like_name("张三"))
        self.assertTrue(rename_core.looks_like_name("王小明同学"))
        self.assertFalse(rename_core.looks_like_name("第一次作业"))
        self.assertFalse(rename_core.looks_like_name("实验报告"))
        self.assertFalse(rename_core.looks_like_name("20230101"))
        self.assertFalse(rename_core.looks_like_name(""))


class TestRenameNewName(unittest.TestCase):
    """新名称生成的测试。"""

    def test_default_template(self) -> None:
        """默认模板应生成「作业名_学号.扩展名」。"""
        built = rename_core.build_new_name(
            "20230101_张三_第一次作业.pdf",
            "{title}_{id}",
        )
        self.assertIsNotNone(built)
        self.assertEqual(built[0], "第一次作业_20230101.pdf")

    def test_extension_is_preserved(self) -> None:
        """扩展名必须保留，否则文件将无法打开。"""
        built = rename_core.build_new_name(
            "20230101_张三_作业.docx",
            "{title}_{id}",
        )
        self.assertIsNotNone(built)
        self.assertTrue(built[0].endswith(".docx"))

    def test_roster_fills_missing_field(self) -> None:
        """信息表应能补全文件名中缺失的字段。

        需求要求「只有学号或只有姓名时可扩写另一项」，此处验证两个方向。
        """
        roster = roster_core.parse_roster_text("20230101,张三")

        # 只有学号 → 补出姓名。
        by_id = rename_core.build_new_name(
            "20230101_作业.docx",
            "{title}_{id}_{name}",
            source_format=("id", "title"),
            roster=roster,
        )
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id[0], "作业_20230101_张三.docx")
        self.assertIn("张三", by_id[4])

        # 只有姓名 → 补出学号。
        by_name = rename_core.build_new_name(
            "张三_作业.docx",
            "{title}_{name}_{id}",
            source_format=("name", "title"),
            roster=roster,
        )
        self.assertIsNotNone(by_name)
        self.assertEqual(by_name[0], "作业_张三_20230101.docx")

    def test_empty_placeholder_does_not_leave_dangling_separator(self) -> None:
        """占位符为空时不应留下悬挂的下划线。

        模板 ``{title}_{id}`` 在学号补全失败时渲染出 ``作业_``，若不过滤
        会得到 ``作业_.docx`` 这样难看的名字。
        """
        built = rename_core.build_new_name(
            "张三_作业.docx",
            "{title}_{id}",
            source_format=("name", "title"),
        )
        self.assertIsNotNone(built)
        self.assertEqual(built[0], "作业.docx")

    def test_validate_template(self) -> None:
        """模板校验应识别空模板、无效占位符与无占位符三种问题。"""
        self.assertIsNone(rename_core.validate_template("{title}_{id}"))
        self.assertIsNotNone(rename_core.validate_template(""))
        self.assertIsNotNone(rename_core.validate_template("{bad}"))
        self.assertIsNotNone(rename_core.validate_template("固定名称"))

    def test_illegal_chars_are_sanitized(self) -> None:
        """文件名中的非法字符应被替换。

        ``"第1/2章"`` 中的斜杠会被系统视为目录分隔符，不处理将导致文件被
        创建至非预期位置。
        """
        cleaned = rename_core._sanitize_stem("第1/2章 报告")
        self.assertNotIn("/", cleaned)
        self.assertIn("_", cleaned)

    def test_invalid_template_returns_none(self) -> None:
        """模板含无效占位符时应返回 ``None``，而非抛出异常。"""
        new_name = rename_core.build_new_name(
            "20230101_张三_作业.pdf",
            "{title}_{unknown_placeholder}",
        )
        self.assertIsNone(new_name)

    def test_irregular_name_returns_none(self) -> None:
        """不符合规则的文件名应返回 ``None``。"""
        new_name = rename_core.build_new_name("随便起的名字.txt", "{title}_{id}")
        self.assertIsNone(new_name)


class TestRenamePlan(TempDirTestCase):
    """改名计划生成与冲突处理的测试。"""

    def _scan(self) -> list:
        """扫描临时目录。"""
        return scan_core.scan_directory(self.tmp_path)

    def test_plan_generates_correct_pairs(self) -> None:
        """计划应正确计算改名前后的对应关系。"""
        self.make_file("20230101_张三_第一次作业.pdf")

        plan = rename_core.calculate_rename_plan(self._scan(), "{title}_{id}")

        self.assertEqual(len(plan.pending), 1)
        action = plan.pending[0]
        self.assertEqual(action.old_name, "20230101_张三_第一次作业.pdf")
        self.assertEqual(action.new_name, "第一次作业_20230101.pdf")

    def test_plan_does_not_touch_disk(self) -> None:
        """生成计划不得修改任何文件。

        该测试对应"必须先预览、确认后才修改"这一约束在代码层面的保证。
        """
        original = self.make_file("20230101_张三_第一次作业.pdf")

        rename_core.calculate_rename_plan(self._scan(), "{title}_{id}")

        self.assertTrue(original.exists())
        self.assertFalse((self.tmp_path / "第一次作业_20230101.pdf").exists())

    def test_conflict_with_existing_file_is_skipped(self) -> None:
        """目标名已被占用时应跳过，不得覆盖。

        场景：磁盘上已存在 ``读书笔记_20230402.txt``，而另一文件
        ``20230402_沈十七_读书笔记.txt`` 的新名与之一致。若不处理，原有
        文件将被覆盖。
        """
        existing = self.make_file("读书笔记_20230402.txt", "原有内容，不能被覆盖")
        self.make_file("20230402_沈十七_读书笔记.txt", "待改名")

        plan = rename_core.calculate_rename_plan(self._scan(), "{title}_{id}")

        skipped_names = [Path(p).name for p, _ in plan.skipped]
        self.assertIn("20230402_沈十七_读书笔记.txt", skipped_names)

        pending_names = [action.old_name for action in plan.pending]
        self.assertNotIn("20230402_沈十七_读书笔记.txt", pending_names)

        self.assertEqual(existing.read_text(encoding="utf-8"), "原有内容，不能被覆盖")

    def test_intra_batch_collision_is_prevented(self) -> None:
        """同批次内两个文件计算出相同新名时，仅批准其中一个。

        使用不含学号的模板 ``{title}`` 使两个文件的新名相同。若无防护，
        后处理者将覆盖先处理者。
        """
        self.make_file("20230101_张三_作业.pdf")
        self.make_file("20230102_李四_作业.pdf")

        plan = rename_core.calculate_rename_plan(self._scan(), "{title}")

        self.assertLessEqual(len(plan.pending), 1)
        self.assertGreaterEqual(len(plan.skipped), 1)

    def test_irregular_files_go_to_skipped(self) -> None:
        """不符合命名规则的文件应进入跳过清单。"""
        self.make_file("随便起的名字.txt")
        self.make_file("20230101_张三_作业.pdf")

        plan = rename_core.calculate_rename_plan(self._scan(), "{title}_{id}")

        skipped_names = [Path(p).name for p, _ in plan.skipped]
        self.assertIn("随便起的名字.txt", skipped_names)

    def test_unchanged_name_marked_as_not_changed(self) -> None:
        """新旧同名时应标记为无需改动，而非待执行。"""
        self.make_file("20230101_张三_作业.pdf")

        plan = rename_core.calculate_rename_plan(self._scan(), "{id}_{name}_{title}")

        self.assertEqual(len(plan.pending), 0)
        self.assertEqual(plan.unchanged_count, 1)


class TestRenameApply(TempDirTestCase):
    """改名执行的测试。"""

    def test_apply_actually_renames(self) -> None:
        """执行后磁盘上的文件名应确实发生变化。"""
        self.make_file("20230101_张三_第一次作业.pdf")

        plan = rename_core.calculate_rename_plan(
            scan_core.scan_directory(self.tmp_path), "{title}_{id}"
        )
        succeeded, failed = rename_core.apply_rename_plan(plan)

        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(failed), 0)

        self.assertTrue((self.tmp_path / "第一次作业_20230101.pdf").exists())
        self.assertFalse((self.tmp_path / "20230101_张三_第一次作业.pdf").exists())

    def test_apply_does_not_overwrite_created_after_plan(self) -> None:
        """计划生成后目标名被占用时，执行阶段仍应拦截。

        模拟用户预览后手动创建同名文件再执行的场景。此时计划已过期，执行
        阶段的二次校验是防止覆盖的最后一道检查。
        """
        self.make_file("20230101_张三_第一次作业.pdf")

        plan = rename_core.calculate_rename_plan(
            scan_core.scan_directory(self.tmp_path), "{title}_{id}"
        )

        intruder = self.make_file("第一次作业_20230101.pdf", "我是后来者，不能被覆盖")

        succeeded, failed = rename_core.apply_rename_plan(plan)

        self.assertEqual(len(succeeded), 0)
        self.assertEqual(len(failed), 1)
        self.assertEqual(intruder.read_text(encoding="utf-8"), "我是后来者，不能被覆盖")


class TestArchive(TempDirTestCase):
    """归档功能的测试。"""

    def test_archive_by_extension(self) -> None:
        """按扩展名归档应将文件放入对应子文件夹。"""
        self.make_file("a.docx")
        self.make_file("b.pdf")
        self.make_file("c.txt")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="extension")

        self.assertEqual(len(result.moved), 3)
        self.assertTrue((self.tmp_path / "Word文档" / "a.docx").exists())
        self.assertTrue((self.tmp_path / "PDF文档" / "b.pdf").exists())
        self.assertTrue((self.tmp_path / "文本文件" / "c.txt").exists())

    def test_archive_generates_report_text(self) -> None:
        """报告应包含需求要求的三项信息。"""
        self.make_file("a.docx")
        self.make_file("b.pdf")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="extension")
        report = archive_core.build_report(result, self.tmp_path)

        self.assertIn("成功移动：2 个", report)
        self.assertIn("跳过未动：0 个", report)
        self.assertIn("Word文档", report)
        self.assertIn("PDF文档", report)

    def test_archive_does_not_overwrite(self) -> None:
        """归档时目标位置已有同名文件应跳过，不得覆盖。"""
        self.make_file("Word文档/a.docx", "原有内容")
        self.make_file("a.docx", "新内容")

        records = scan_core.scan_directory(self.tmp_path, recursive=False)
        result = archive_core.archive(records, self.tmp_path, mode="extension")

        self.assertEqual(len(result.moved), 0)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(
            (self.tmp_path / "Word文档" / "a.docx").read_text(encoding="utf-8"),
            "原有内容",
        )

    def test_report_saved_to_disk(self) -> None:
        """报告应能保存为文件。"""
        self.make_file("a.docx")
        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="extension")
        report = archive_core.build_report(result, self.tmp_path)

        saved = archive_core.save_report(report, self.tmp_path)

        self.assertTrue(saved.exists())
        self.assertIn("作业文件整理报告", saved.read_text(encoding="utf-8"))


class TestSemesterRanges(unittest.TestCase):
    """学期区间解析与判定的测试。"""

    def test_default_semesters_cover_two_years(self) -> None:
        """默认区间表应覆盖 2025-2026 与 2026-2027 两个学年。"""
        names = [name for name, _, _ in archive_core.DEFAULT_SEMESTERS]
        self.assertIn("2025-2026-1", names)
        self.assertIn("2025-2026-2", names)
        self.assertIn("2026-2027-1", names)
        self.assertIn("2026-2027-2", names)
        self.assertEqual(len(archive_core.DEFAULT_SEMESTERS), 4)

    def test_parse_sample_text(self) -> None:
        """内置示例文本应能完整解析为区间列表。"""
        ranges = archive_core.parse_semester_ranges_text(
            archive_core.SAMPLE_SEMESTER_TEXT
        )
        self.assertEqual(len(ranges), 4)
        self.assertEqual(ranges[0], ("2025-2026-1", "2025-09-01", "2026-01-15"))

    def test_parse_skips_comments_and_blank_lines(self) -> None:
        """注释行与空行应被跳过，不计入区间。"""
        text = (
            "# 这是注释\n"
            "\n"
            "2026-2027-1, 2026-09-01, 2027-01-15\n"
            "   \n"
            "# 又一条注释\n"
        )
        ranges = archive_core.parse_semester_ranges_text(text)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][0], "2026-2027-1")

    def test_parse_accepts_chinese_comma_and_whitespace(self) -> None:
        """中文逗号与空格分隔都应被接受。"""
        by_chinese_comma = archive_core.parse_semester_ranges_text(
            "2026-2027-1，2026-09-01，2027-01-15"
        )
        by_space = archive_core.parse_semester_ranges_text(
            "2026-2027-1 2026-09-01 2027-01-15"
        )
        self.assertEqual(by_chinese_comma, by_space)
        self.assertEqual(by_chinese_comma[0][0], "2026-2027-1")

    def test_parse_normalizes_short_dates(self) -> None:
        """省略前导零的日期应被规范化为 YYYY-MM-DD。"""
        ranges = archive_core.parse_semester_ranges_text("A, 2026-9-1, 2027-1-15")
        self.assertEqual(ranges[0], ("A", "2026-09-01", "2027-01-15"))

    def test_parse_empty_text_returns_empty_list(self) -> None:
        """空文本与仅含注释的文本应返回空列表，而不是报错。"""
        self.assertEqual(archive_core.parse_semester_ranges_text(""), [])
        self.assertEqual(archive_core.parse_semester_ranges_text("# 只有注释"), [])

    def test_parse_rejects_wrong_field_count(self) -> None:
        """字段数不为 3 时应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            archive_core.parse_semester_ranges_text("2026-2027-1, 2026-09-01")

    def test_parse_rejects_invalid_date(self) -> None:
        """非法日期（含不存在的日期）应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            archive_core.parse_semester_ranges_text("A, 2026-13-01, 2027-01-15")
        with self.assertRaises(ValueError):
            archive_core.parse_semester_ranges_text("A, 2026-02-30, 2027-01-15")

    def test_parse_rejects_reversed_range(self) -> None:
        """开始日期晚于结束日期应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            archive_core.parse_semester_ranges_text("A, 2027-05-01, 2026-01-01")


class TestClassifyBySemester(TempDirTestCase):
    """按修改时间判定学期的测试。"""

    def _touch_with_mtime(self, name: str, iso_date: str) -> Path:
        """创建一个文件并把它的修改时间改为指定日期。

        Args:
            name: 文件名。
            iso_date: ``YYYY-MM-DD`` 形式的日期。

        Returns:
            创建出的文件路径。
        """
        import os

        path = self.make_file(name)
        target = datetime.fromisoformat(iso_date)
        stamp = target.timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_file_falls_into_autumn_semester(self) -> None:
        """2026 年 10 月的文件应归入 2026-2027-1。"""
        path = self._touch_with_mtime("a.docx", "2026-10-06")
        self.assertEqual(
            archive_core.classify_by_semester(path), "2026-2027-1"
        )

    def test_file_falls_into_spring_semester(self) -> None:
        """2027 年 4 月的文件应归入 2026-2027-2。"""
        path = self._touch_with_mtime("b.docx", "2027-04-01")
        self.assertEqual(
            archive_core.classify_by_semester(path), "2026-2027-2"
        )

    def test_boundary_end_date_is_inclusive(self) -> None:
        """结束日期当天应算作落在区间内。"""
        path = self._touch_with_mtime("c.docx", "2027-01-15")
        self.assertEqual(
            archive_core.classify_by_semester(path), "2026-2027-1"
        )

    def test_file_outside_all_ranges_is_unclassified(self) -> None:
        """不落在任何区间的文件应归入「未归类」。"""
        path = self._touch_with_mtime("d.docx", "2028-03-01")
        self.assertEqual(
            archive_core.classify_by_semester(path),
            archive_core.UNCLASSIFIED_NAME,
        )

    def test_custom_ranges_are_honoured(self) -> None:
        """自定义区间应覆盖默认区间表。"""
        path = self._touch_with_mtime("e.docx", "2028-03-01")
        custom = [("自定义学期", "2028-01-01", "2028-12-31")]
        self.assertEqual(
            archive_core.classify_by_semester(path, custom), "自定义学期"
        )

    def test_empty_ranges_fall_back_to_default(self) -> None:
        """传入空区间序列时应回退到默认区间表。"""
        path = self._touch_with_mtime("f.docx", "2026-10-06")
        self.assertEqual(
            archive_core.classify_by_semester(path, []), "2026-2027-1"
        )


class TestSemesterArchive(TempDirTestCase):
    """按学期归档与未归类明细报告的测试。"""

    def _touch_with_mtime(self, name: str, iso_date: str) -> Path:
        """创建文件并设置其修改时间。"""
        import os

        path = self.make_file(name)
        stamp = datetime.fromisoformat(iso_date).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    def test_archive_by_semester_moves_files_into_semester_folders(self) -> None:
        """按学期归档应把文件移入对应学期子文件夹。"""
        self._touch_with_mtime("a.docx", "2026-10-06")
        self._touch_with_mtime("b.docx", "2027-04-01")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="semester")

        self.assertEqual(len(result.moved), 2)
        self.assertTrue(
            (self.tmp_path / "2026-2027-1" / "a.docx").exists()
        )
        self.assertTrue(
            (self.tmp_path / "2026-2027-2" / "b.docx").exists()
        )

    def test_unclassified_files_get_their_own_folder(self) -> None:
        """区间外的文件应进入「未归类」文件夹。"""
        self._touch_with_mtime("out.docx", "2028-03-01")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="semester")

        self.assertEqual(len(result.moved), 1)
        self.assertTrue(
            (self.tmp_path / archive_core.UNCLASSIFIED_NAME / "out.docx").exists()
        )

    def test_report_lists_unclassified_with_mtime(self) -> None:
        """报告应列出未归类文件及其修改时间。"""
        self._touch_with_mtime("out.docx", "2028-03-01")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="semester")
        report = archive_core.build_report(result, self.tmp_path)

        self.assertIn("【未归类明细】", report)
        self.assertIn("out.docx", report)
        self.assertIn("2028-03-01", report)

    def test_report_omits_unclassified_section_when_none(self) -> None:
        """没有未归类文件时，报告不应出现该区块。"""
        self._touch_with_mtime("in.docx", "2026-10-06")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(records, self.tmp_path, mode="semester")
        report = archive_core.build_report(result, self.tmp_path)

        self.assertNotIn("【未归类明细】", report)

    def test_archive_by_semester_uses_custom_ranges(self) -> None:
        """按学期归档应尊重传入的自定义区间。"""
        self._touch_with_mtime("x.docx", "2028-05-05")

        records = scan_core.scan_directory(self.tmp_path)
        result = archive_core.archive(
            records,
            self.tmp_path,
            mode="semester",
            semester_ranges=[("我的学期", "2028-01-01", "2028-12-31")],
        )

        self.assertEqual(len(result.moved), 1)
        self.assertTrue((self.tmp_path / "我的学期" / "x.docx").exists())

    def test_extension_mode_ignores_semester_ranges(self) -> None:
        """按扩展名归档时，学期区间不应产生影响。"""
        self.make_file("a.docx")

        records = scan_core.scan_directory(self.tmp_path)
        archive_core.archive(
            records,
            self.tmp_path,
            mode="extension",
            semester_ranges=[("不存在的学期", "1900-01-01", "1900-12-31")],
        )

        self.assertTrue((self.tmp_path / "Word文档" / "a.docx").exists())


class TestTitleWhitelist(unittest.TestCase):
    """作业名表（白名单）文本解析的测试。"""

    def test_basic_lines(self) -> None:
        """每行一个作业名，按原顺序返回。"""
        titles = archive_core.parse_title_whitelist_text(
            "第一次作业\n第二次作业\n数据结构实验"
        )

        self.assertEqual(titles, ["第一次作业", "第二次作业", "数据结构实验"])

    def test_skips_blank_lines_and_comments(self) -> None:
        """空行与 # 开头的注释行应被跳过。"""
        titles = archive_core.parse_title_whitelist_text(
            "# 这是注释\n第一次作业\n\n   \n# 又一条注释\n第二次作业\n"
        )

        self.assertEqual(titles, ["第一次作业", "第二次作业"])

    def test_strips_surrounding_whitespace(self) -> None:
        """每行的首尾空白应被去除。"""
        titles = archive_core.parse_title_whitelist_text("  第一次作业  \n\t第二次作业\t\n")

        self.assertEqual(titles, ["第一次作业", "第二次作业"])

    def test_deduplicates_keeping_first_order(self) -> None:
        """重复项只保留首次出现的那一个，顺序不变。"""
        titles = archive_core.parse_title_whitelist_text(
            "第一次作业\n第二次作业\n第一次作业\n"
        )

        self.assertEqual(titles, ["第一次作业", "第二次作业"])

    def test_empty_text_returns_empty_list(self) -> None:
        """空文本、纯空白文本、纯注释文本都返回空列表。"""
        for text in ("", "   \n\n  ", "# 只有注释\n# 还是注释\n"):
            self.assertEqual(archive_core.parse_title_whitelist_text(text), [])

    def test_sample_text_is_parseable(self) -> None:
        """内置示例文本应能被自身解析，且不含注释与空行。"""
        titles = archive_core.parse_title_whitelist_text(
            archive_core.SAMPLE_TITLE_WHITELIST_TEXT
        )

        self.assertTrue(titles, "示例文本不应解析出空列表")
        self.assertTrue(all(not item.startswith("#") for item in titles))


class TestExtractTitle(TempDirTestCase):
    """从文件名识别作业名的测试。"""

    def _title(self, filename: str, whitelist=None) -> tuple[str, str]:
        """对临时目录下的虚拟文件名识别作业名。"""
        return archive_core.extract_title(self.tmp_path / filename, whitelist)

    def test_two_segments_id_and_text(self) -> None:
        """两段「数字 + 文本」：文本段直接作作业名。"""
        title, _ = self._title("20230001_第一次作业.docx")
        self.assertEqual(title, "第一次作业")

    def test_two_segments_two_texts_keyword_wins(self) -> None:
        """两段文本只有一段含特征词时，该段作作业名。"""
        title, _ = self._title("第一次作业_张三.docx")
        self.assertEqual(title, "第一次作业")

    def test_three_segments_title_name_id(self) -> None:
        """三段「作业名_姓名_学号」应能识别出作业名。"""
        title, _ = self._title("数据结构实验_王五_20230103.docx")
        self.assertEqual(title, "数据结构实验")

    def test_three_segments_two_keywords_takes_trailing(self) -> None:
        """三段中两段文本都含特征词时，取后置段作作业名。"""
        title, _ = self._title("20230001_作业_报告.docx")
        self.assertEqual(title, "报告")

    def test_single_segment_unrecognized(self) -> None:
        """单段文件名识别不出作业名。"""
        title, reason = self._title("孤零零.docx")
        self.assertEqual(title, "")
        self.assertIn("仅一段", reason)

    def test_whitelist_overrides_heuristics(self) -> None:
        """作业名表中的条目应优先被认定为作业名。

        「读书笔记」在去掉白名单时可能被判为姓名，加入白名单后必须纠正。
        """
        title, _ = self._title(
            "读书笔记_冯十二_20230202.txt", ["读书笔记", "第一次作业"]
        )
        self.assertEqual(title, "读书笔记")

    def test_four_segments_unrecognized(self) -> None:
        """四段文件名仍不予识别。"""
        title, _ = self._title("a_b_c_d.docx")
        self.assertEqual(title, "")


class TestClassifyByTitle(TempDirTestCase):
    """按作业名分类的测试。"""

    def _classify(self, filename: str, whitelist=None) -> tuple[str, str]:
        """对临时目录下的虚拟文件名做分类。"""
        return archive_core.classify_by_title(self.tmp_path / filename, whitelist)

    def test_hit_whitelist_returns_title_as_category(self) -> None:
        """命中白名单时，分类名就是该作业名。"""
        category, reason = self._classify(
            "第一次作业_张三_20230101.docx", ["第一次作业", "第二次作业"]
        )

        self.assertEqual(category, "第一次作业")
        self.assertIn("归入同名文件夹", reason)

    def test_miss_whitelist_returns_unlisted(self) -> None:
        """未命中白名单时返回 UNLISTED_TITLE_NAME 标记。"""
        category, reason = self._classify(
            "毕业论文_张三_20230101.docx", ["第一次作业"]
        )

        self.assertEqual(category, archive_core.UNLISTED_TITLE_NAME)
        self.assertIn("不在作业表", reason)
        self.assertIn("不移动", reason)

    def test_no_whitelist_accepts_any_title(self) -> None:
        """不提供白名单时，识别出什么作业名就按什么归类。"""
        category, _ = self._classify("毕业论文_张三_20230101.docx", None)

        self.assertEqual(category, "毕业论文")

    def test_empty_whitelist_means_no_restriction(self) -> None:
        """空列表等同于不设白名单。"""
        category, _ = self._classify("毕业论文_张三_20230101.docx", [])

        self.assertEqual(category, "毕业论文")

    def test_unrecognizable_goes_to_unclassified(self) -> None:
        """识别不出作业名时归入 UNCLASSIFIED_NAME。"""
        category, _ = self._classify("随便起的名字.docx", None)

        self.assertEqual(category, archive_core.UNCLASSIFIED_NAME)

    def test_whitelist_corrects_heuristic_mistake(self) -> None:
        """作业名表能纠正启发式的误判。

        「宏观经济学_王小明_20230202」的两段文本都不含内置特征词、长度也相近，
        启发式只能按「取后置段」把姓名「王小明」当作作业名；用户把「宏观经济学」
        写进作业名表后，识别结果被纠正回正确值。这正是白名单存在的意义。
        """
        filename = "宏观经济学_王小明_20230202.txt"

        without_whitelist, _ = self._classify(filename, None)
        with_whitelist, _ = self._classify(filename, ["宏观经济学"])

        self.assertEqual(without_whitelist, "王小明", "无白名单时应复现启发式误判")
        self.assertEqual(with_whitelist, "宏观经济学", "有白名单时应纠正为正确作业名")


class TestTitleArchive(TempDirTestCase):
    """按作业名归档（mode="title"）的端到端测试。"""

    def _archive(self, whitelist=None):
        """扫描临时目录并按作业名归档。"""
        records = scan_core.scan_directory(self.tmp_path)
        return archive_core.archive(
            records=records,
            root=self.tmp_path,
            mode="title",
            title_whitelist=whitelist,
        )

    def test_files_moved_into_title_folders(self) -> None:
        """每个作业名建一个文件夹，文件被移入其中。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("第一次作业_李四_20230102.docx")
        self.make_file("数据结构实验_王五_20230103.docx")

        result = self._archive()

        self.assertEqual(len(result.moved), 3)
        self.assertTrue(
            (self.tmp_path / "第一次作业" / "第一次作业_张三_20230101.docx").exists()
        )
        self.assertTrue(
            (self.tmp_path / "第一次作业" / "第一次作业_李四_20230102.docx").exists()
        )
        self.assertTrue(
            (self.tmp_path / "数据结构实验" / "数据结构实验_王五_20230103.docx").exists()
        )

    def test_file_names_are_not_modified(self) -> None:
        """归档只移动文件，不改名。"""
        self.make_file("第一次作业_张三_20230101.docx")

        self._archive()

        self.assertTrue(
            (self.tmp_path / "第一次作业" / "第一次作业_张三_20230101.docx").exists()
        )

    def test_unlisted_files_stay_in_place(self) -> None:
        """不在作业名表中的文件保持原地不动（用户明确要求的策略）。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("期末考试_李四_20230102.docx")

        result = self._archive(["第一次作业"])

        self.assertEqual(len(result.moved), 1)
        # 未列入的文件仍在根目录，没有被移动。
        self.assertTrue((self.tmp_path / "期末考试_李四_20230102.docx").exists())
        # 也不应生成「未列入作业表」文件夹。
        self.assertFalse((self.tmp_path / archive_core.UNLISTED_TITLE_NAME).exists())

    def test_unlisted_files_recorded_in_skipped(self) -> None:
        """未列入的文件要进 skipped 明细并写明原因。"""
        self.make_file("期末考试_李四_20230102.docx")

        result = self._archive(["第一次作业"])

        self.assertEqual(len(result.moved), 0)
        self.assertTrue(
            any("不在作业表" in reason for _, reason in result.skipped),
            f"跳过原因中应含「不在作业表」，实际为 {result.skipped}",
        )

    def test_unrecognizable_goes_to_unclassified_folder(self) -> None:
        """识别不出作业名的文件归入「未归类」文件夹。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("孤零零.docx")

        # 归档结果本身无需断言，这里只关心文件最终落点。
        self._archive()

        unclassified_dir = self.tmp_path / archive_core.UNCLASSIFIED_NAME
        self.assertTrue(unclassified_dir.exists())
        self.assertTrue((unclassified_dir / "孤零零.docx").exists())

    def test_report_has_unlisted_section(self) -> None:
        """报告应含「不在作业表 · 未移动」区块，并说明文件未被移动。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("期末考试_李四_20230102.docx")

        result = self._archive(["第一次作业"])
        report = archive_core.build_report(result, self.tmp_path, mode="title")

        self.assertIn("不在作业表 · 未移动", report)
        self.assertIn("期末考试_李四_20230102.docx", report)

    def test_report_unclassified_wording_matches_mode(self) -> None:
        """作业名模式下「未归类」的说明不应再提学期区间。"""
        self.make_file("孤零零.docx")

        result = self._archive()
        report = archive_core.build_report(result, self.tmp_path, mode="title")

        section = report[report.find("【未归类明细】"):]
        self.assertIn("无法从下列文件名中识别出作业名", section)
        self.assertNotIn("学期区间", section)


class TestMatchSegments(unittest.TestCase):
    """``match_segments`` 逐段匹配关键词表的测试。"""

    def test_matches_title_name_and_id(self) -> None:
        """作业名、姓名、学号混在表中时，各段都能各自命中。"""
        matched = rename_core.match_segments(
            "第一次作业_张三_20230101",
            ["第一次作业", "张三", "20230101"],
        )

        self.assertEqual(matched, ["第一次作业", "张三", "20230101"])

    def test_unlimited_segments(self) -> None:
        """段数不受限制，五段也能全部命中。"""
        matched = rename_core.match_segments(
            "A_B_C_D_E",
            ["A", "B", "C", "D", "E"],
        )

        self.assertEqual(matched, ["A", "B", "C", "D", "E"])

    def test_partial_match_returns_only_hits(self) -> None:
        """只有写进表中的段才返回，其余段忽略。"""
        matched = rename_core.match_segments(
            "第一次作业_张三_20230101",
            ["张三"],
        )

        self.assertEqual(matched, ["张三"])

    def test_no_match_returns_empty(self) -> None:
        """一段都没命中时返回空列表。"""
        matched = rename_core.match_segments("随便起的名字", ["第一次作业", "张三"])

        self.assertEqual(matched, [])

    def test_empty_keywords_returns_empty(self) -> None:
        """关键词表为空时不命中任何段。"""
        self.assertEqual(rename_core.match_segments("第一次作业_张三", None), [])
        self.assertEqual(rename_core.match_segments("第一次作业_张三", []), [])
        # 全是空白的关键词表同样视为空。
        self.assertEqual(rename_core.match_segments("第一次作业_张三", ["  ", ""]), [])

    def test_duplicate_segments_reported_once(self) -> None:
        """同名段重复出现时只报告一次，避免重复建同一个文件夹。"""
        matched = rename_core.match_segments("张三_张三_20230101", ["张三"])

        self.assertEqual(matched, ["张三"])

    def test_space_and_plus_separators_supported(self) -> None:
        """空格与加号同样作为分段符。"""
        self.assertEqual(
            rename_core.match_segments("第一次作业 张三 20230101", ["张三"]), ["张三"]
        )
        self.assertEqual(
            rename_core.match_segments("第一次作业+张三+20230101", ["张三"]), ["张三"]
        )

    def test_whitespace_in_keywords_is_stripped(self) -> None:
        """关键词两侧的空白会被清理，用户从表格里粘贴带空格也能命中。"""
        matched = rename_core.match_segments("第一次作业_张三", ["  张三  "])

        self.assertEqual(matched, ["张三"])


class TestParseKeywordListText(unittest.TestCase):
    """``parse_keyword_list_text`` 的测试。"""

    def test_skips_comments_and_blanks(self) -> None:
        """注释行与空行被忽略。"""
        text = "# 这是注释\n第一次作业\n\n张三\n   \n20230101\n"
        self.assertEqual(
            archive_core.parse_keyword_list_text(text),
            ["第一次作业", "张三", "20230101"],
        )

    def test_deduplicates_keep_order(self) -> None:
        """重复项去重且保持首次出现的顺序。"""
        text = "第一次作业\n张三\n第一次作业\n"
        self.assertEqual(archive_core.parse_keyword_list_text(text), ["第一次作业", "张三"])

    def test_empty_text_returns_empty(self) -> None:
        """只有注释或空行时返回空列表。"""
        self.assertEqual(archive_core.parse_keyword_list_text("# 注释\n\n  \n"), [])


class TestSegmentArchive(TempDirTestCase):
    """按段归类（mode="segment"）的端到端测试。"""

    def _archive(self, keywords):
        """扫描临时目录并按段归类。"""
        records = scan_core.scan_directory(self.tmp_path)
        return archive_core.archive(
            records=records,
            root=self.tmp_path,
            mode="segment",
            keywords=keywords,
        )

    def test_single_hit_creates_one_folder(self) -> None:
        """只命中一段时，只建一个文件夹，文件被移动过去。"""
        self.make_file("第一次作业_张三_20230101.docx")

        result = self._archive(["第一次作业"])

        self.assertEqual(len(result.moved), 1)
        self.assertTrue(
            (self.tmp_path / "第一次作业" / "第一次作业_张三_20230101.docx").exists()
        )
        self.assertEqual(result.moved[0].category, "第一次作业")

    def test_multi_hit_creates_copies(self) -> None:
        """命中多段时，本体放第一个文件夹，其余文件夹各放一份副本。"""
        self.make_file("第一次作业_张三_20230101.docx")

        result = self._archive(["第一次作业", "张三", "20230101"])

        self.assertEqual(len(result.moved), 1)
        action = result.moved[0]
        # 本体落在第一个命中的文件夹。
        self.assertEqual(action.category, "第一次作业")
        self.assertTrue(
            (self.tmp_path / "第一次作业" / "第一次作业_张三_20230101.docx").exists()
        )
        # 另外两段各有一个副本。
        self.assertEqual(len(action.copies), 2)
        self.assertTrue(
            (self.tmp_path / "张三" / "第一次作业_张三_20230101.docx").exists()
        )
        self.assertTrue(
            (self.tmp_path / "20230101" / "第一次作业_张三_20230101.docx").exists()
        )

    def test_copies_preserve_mtime(self) -> None:
        """副本的修改时间与本体一致，避免报告里时间对不上。"""
        # 先记下源文件的修改时间：归档后源路径不复存在，只能提前取。
        src = self.tmp_path / "第一次作业_张三_20230101.docx"
        self.make_file(src.name)
        src_mtime = src.stat().st_mtime

        self._archive(["第一次作业", "张三"])

        copy_path = self.tmp_path / "张三" / "第一次作业_张三_20230101.docx"
        self.assertTrue(copy_path.exists(), "应生成副本")
        self.assertAlmostEqual(src_mtime, copy_path.stat().st_mtime, places=1)

    def test_unmatched_files_stay_in_place(self) -> None:
        """一段都没命中的文件保持原地不动，且不建任何文件夹。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("随便起的名字.txt")

        result = self._archive(["第一次作业"])

        self.assertEqual(len(result.moved), 1)
        self.assertTrue((self.tmp_path / "随便起的名字.txt").exists())
        # 未命中的文件不应产生任何新文件夹。
        self.assertFalse((self.tmp_path / "随便起的名字").exists())
        self.assertTrue(
            any("没有段命中关键词表" in reason for _, reason in result.skipped),
            f"跳过原因应说明未命中，实际为 {result.skipped}",
        )

    def test_empty_keywords_raises(self) -> None:
        """关键词表为空时直接抛错，而不是静默地什么都不做。"""
        self.make_file("第一次作业_张三_20230101.docx")
        records = scan_core.scan_directory(self.tmp_path)

        with self.assertRaises(ValueError):
            archive_core.archive(
                records=records,
                root=self.tmp_path,
                mode="segment",
                keywords=[],
            )

    def test_undo_restores_files_and_removes_copies(self) -> None:
        """撤销时要还原本体、删掉副本，且根目录文件数完全恢复。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("第一次作业_李四_20230102.docx")
        self.make_file("随便起的名字.txt")
        before = sorted(p.name for p in self.tmp_path.iterdir() if p.is_file())

        self._archive(["第一次作业", "张三"])
        undo = archive_core.undo_last(self.tmp_path)

        # 两个本体被还原，一个副本被清理。
        self.assertEqual(len(undo.restored), 2)
        self.assertEqual(len(undo.removed_copies), 1)
        self.assertEqual(undo.failed, [])
        self.assertEqual(
            sorted(p.name for p in self.tmp_path.iterdir() if p.is_file()), before
        )
        # 副本所在的文件夹里不应再有残留。
        copy_dir = self.tmp_path / "张三"
        self.assertTrue(not copy_dir.exists() or not list(copy_dir.iterdir()))

    def test_report_lists_copies_and_unmatched(self) -> None:
        """报告要同时含副本指向与「未命中关键词 · 未移动」区块。"""
        self.make_file("第一次作业_张三_20230101.docx")
        self.make_file("随便起的名字.txt")

        result = self._archive(["第一次作业", "张三"])
        report = archive_core.build_report(result, self.tmp_path, mode="segment")

        self.assertIn("副本", report)
        self.assertIn("未命中关键词 · 未移动", report)
        self.assertIn("随便起的名字.txt", report)

    def test_sample_keyword_list_is_parseable(self) -> None:
        """内置示例表能被解析出非空关键词，保证「填入示例」按钮可用。"""
        keywords = archive_core.parse_keyword_list_text(
            archive_core.SAMPLE_KEYWORD_LIST_TEXT
        )

        self.assertTrue(keywords)
        self.assertIn("第一次作业", keywords)


class TestUndo(TempDirTestCase):
    """撤销功能的测试。"""

    def test_undo_restores_files(self) -> None:
        """撤销应将全部文件移回原位。"""
        self.make_file("a.docx")
        self.make_file("b.pdf")
        self.make_file("c.txt")

        records = scan_core.scan_directory(self.tmp_path)
        archive_core.archive(records, self.tmp_path, mode="extension")

        self.assertTrue((self.tmp_path / "Word文档" / "a.docx").exists())
        self.assertFalse((self.tmp_path / "a.docx").exists())

        result = archive_core.undo_last(self.tmp_path)

        self.assertEqual(len(result.restored), 3)
        self.assertTrue((self.tmp_path / "a.docx").exists())
        self.assertTrue((self.tmp_path / "b.pdf").exists())
        self.assertTrue((self.tmp_path / "c.txt").exists())
        self.assertFalse((self.tmp_path / "Word文档" / "a.docx").exists())

    def test_undo_without_history(self) -> None:
        """无历史记录时撤销应明确报告原因。"""
        result = archive_core.undo_last(self.tmp_path)

        self.assertEqual(len(result.restored), 0)
        self.assertEqual(len(result.failed), 1)
        self.assertIn("没有找到", result.failed[0][1])

    def test_undo_twice_second_time_fails(self) -> None:
        """连续撤销两次时，第二次应报告无记录。

        该测试验证撤销后会移除对应日志记录，避免重复处理同一条记录。
        """
        self.make_file("a.docx")

        records = scan_core.scan_directory(self.tmp_path)
        archive_core.archive(records, self.tmp_path, mode="extension")

        first = archive_core.undo_last(self.tmp_path)
        self.assertEqual(len(first.restored), 1)

        second = archive_core.undo_last(self.tmp_path)
        self.assertEqual(len(second.restored), 0)
        self.assertIn("没有找到", second.failed[0][1])

    def test_undo_does_not_overwrite(self) -> None:
        """撤销时原位置已被占用应跳过，不得覆盖。"""
        self.make_file("a.docx")

        records = scan_core.scan_directory(self.tmp_path)
        archive_core.archive(records, self.tmp_path, mode="extension")

        intruder = self.make_file("a.docx", "新文件，不能被覆盖")

        result = archive_core.undo_last(self.tmp_path)

        self.assertEqual(len(result.restored), 0)
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(intruder.read_text(encoding="utf-8"), "新文件，不能被覆盖")

    def test_history_is_recorded(self) -> None:
        """归档后应能在历史记录中查询到。"""
        self.make_file("a.docx")

        records = scan_core.scan_directory(self.tmp_path)
        archive_core.archive(records, self.tmp_path, mode="extension")

        history = archive_core.list_history(self.tmp_path)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].operation, "archive")
        self.assertEqual(len(history[0].actions), 1)

    def test_journal_survives_multiple_archives(self) -> None:
        """多次归档后历史记录应包含两条，且按从新到旧排列。"""
        self.make_file("a.docx")
        records = scan_core.scan_directory(self.tmp_path, recursive=False)
        archive_core.archive(records, self.tmp_path, mode="extension")

        self.make_file("b.pdf")
        records = scan_core.scan_directory(self.tmp_path, recursive=False)
        archive_core.archive(records, self.tmp_path, mode="extension")

        history = archive_core.list_history(self.tmp_path)

        self.assertEqual(len(history), 2)
        self.assertEqual(len(history[0].actions), 1)


class TestWordCount(unittest.TestCase):
    """字数统计算法的测试。"""

    def test_count_chinese_characters(self) -> None:
        """每个中文字符计 1 字。"""
        self.assertEqual(wordcount_core.count_words("今天天气很好"), 6)

    def test_count_english_words(self) -> None:
        """每个英文单词计 1 字，不以字母计数。"""
        self.assertEqual(wordcount_core.count_words("hello world"), 2)

    def test_punctuation_not_counted(self) -> None:
        """标点符号与空白不计入。"""
        self.assertEqual(wordcount_core.count_words("你好！"), 2)

    def test_mixed_chinese_english(self) -> None:
        """中英混排时分别统计后相加。"""
        self.assertEqual(wordcount_core.count_words("你好 world"), 3)

    def test_empty_text(self) -> None:
        """空文本应返回 0。"""
        self.assertEqual(wordcount_core.count_words(""), 0)
        self.assertEqual(wordcount_core.count_words("   \n\t  "), 0)

    def test_below_threshold_is_strictly_less(self) -> None:
        """低于阈值采用严格小于判定，恰好等于阈值视为合格。"""
        results = [
            wordcount_core.WordCountResult(
                path=Path("a.txt"), count=499, below_threshold=False
            ),
            wordcount_core.WordCountResult(
                path=Path("b.txt"), count=500, below_threshold=False
            ),
        ]

        wordcount_core.apply_threshold(results, threshold=500)

        self.assertTrue(results[0].below_threshold)
        self.assertFalse(results[1].below_threshold)

    def test_failed_count_not_marked_below(self) -> None:
        """统计失败的文件不应被标记为低于阈值。

        无法读取与内容不足是两种不同状态，将二者混同会误判学生作业。
        """
        results = [
            wordcount_core.WordCountResult(
                path=Path("bad.pdf"),
                count=None,
                below_threshold=False,
                error="读取失败：文件损坏",
            ),
        ]

        wordcount_core.apply_threshold(results, threshold=500)

        self.assertFalse(results[0].below_threshold)
        self.assertFalse(results[0].is_ok)


class TestWordCountOnFiles(TempDirTestCase):
    """真实文件上的字数统计测试。"""

    def test_count_txt_file(self) -> None:
        """应正确统计 txt 文件的字数。"""
        self.make_file("a.txt", "一二三四五六七八九十")

        result = wordcount_core.count_words_in_file(self.tmp_path / "a.txt")

        self.assertTrue(result.is_ok)
        self.assertEqual(result.count, 10)

    def test_unsupported_format_records_error(self) -> None:
        """不支持的格式应返回带原因的失败结果，而非抛出异常。"""
        path = self.make_file("a.xyz", "some content")

        result = wordcount_core.count_words_in_file(path)

        self.assertFalse(result.is_ok)
        self.assertIsNone(result.count)
        self.assertIn("不支持", result.error)

    def test_scan_documents_wordcount_with_threshold(self) -> None:
        """批量统计应正确标出低于阈值的文件。"""
        self.make_file("20230101_长文件.txt", "好" * 100)
        self.make_file("20230102_短文件.txt", "好" * 5)

        records = scan_core.scan_directory(self.tmp_path)
        results, _ = wordcount_core.scan_documents_wordcount(records, threshold=50)

        by_name = {result.path.name: result for result in results}

        self.assertFalse(by_name["20230101_长文件.txt"].below_threshold)
        self.assertTrue(by_name["20230102_短文件.txt"].below_threshold)

    def test_wordcount_report_content(self) -> None:
        """字数报告应包含不合格清单与达标清单。"""
        self.make_file("20230101_长文件.txt", "好" * 100)
        self.make_file("20230102_短文件.txt", "好" * 5)

        records = scan_core.scan_directory(self.tmp_path)
        results, _ = wordcount_core.scan_documents_wordcount(records, threshold=50)
        report = wordcount_core.build_wordcount_report(results, threshold=50)

        self.assertIn("低于字数要求", report)
        self.assertIn("20230102_短文件.txt", report)
        self.assertIn("低于要求 1 个", report)

    def test_docx_counting(self) -> None:
        """应能统计 docx 文件的字数，含表格内容。"""
        try:
            from docx import Document
        except ImportError:
            self.skipTest("未安装 python-docx，跳过 docx 测试")

        document = Document()
        document.add_paragraph("这是中文内容")
        document.add_paragraph("hello world")
        path = self.tmp_path / "20230101_张三_作业.docx"
        document.save(str(path))

        result = wordcount_core.count_words_in_file(path)

        self.assertTrue(result.is_ok)
        self.assertEqual(result.count, 8)


class TestRoster(unittest.TestCase):
    """学生信息表解析的测试。"""

    def test_parse_comma_separated(self) -> None:
        """以逗号分隔的信息表应被正确解析。"""
        roster = roster_core.parse_roster_text("20230101,张三\n20230102,李四")

        self.assertEqual(len(roster), 2)
        self.assertEqual(roster.name_of("20230101"), "张三")
        self.assertEqual(roster.id_of("李四"), "20230102")

    def test_parse_space_and_colon_separated(self) -> None:
        """空格、冒号、全角逗号等分隔符都应支持。"""
        text = "20230101 张三\n20230102:李四\n20230103，王五"
        roster = roster_core.parse_roster_text(text)

        self.assertEqual(len(roster), 3)
        self.assertEqual(roster.name_of("20230102"), "李四")
        self.assertEqual(roster.name_of("20230103"), "王五")

    def test_parse_skips_header_and_comments(self) -> None:
        """表头行与注释行应被忽略，不当作数据。"""
        text = "# 这是注释\n// 这也是注释\n学号,姓名\nid,name\n20230101,张三\n"
        roster = roster_core.parse_roster_text(text)

        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.name_of("20230101"), "张三")
        # 表头不能被当成一条「学号=姓名，姓名=姓名」的荒谬记录。
        self.assertIsNone(roster.name_of("学号"))
        self.assertIsNone(roster.name_of("id"))

    def test_parse_keyed_format(self) -> None:
        """``学号=... 姓名=...`` 形式的写法应被支持。"""
        roster = roster_core.parse_roster_text("学号=20230101 姓名=张三")

        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.name_of("20230101"), "张三")

    def test_duplicate_id_is_ignored(self) -> None:
        """重复学号应只保留第一条，并留下冲突记录。"""
        roster = roster_core.parse_roster_text("20230101,张三\n20230101,李四")

        self.assertEqual(len(roster), 1)
        self.assertEqual(roster.name_of("20230101"), "张三")
        self.assertEqual(len(roster.conflicts), 1)

    def test_empty_text_gives_empty_roster(self) -> None:
        """空文本应得到空表，且可安全判假。"""
        roster = roster_core.parse_roster_text("")

        self.assertEqual(len(roster), 0)
        self.assertFalse(roster)
        self.assertIsNone(roster.name_of("20230101"))

    def test_sample_text_is_parseable(self) -> None:
        """内置示例必须能被自己的解析器解析。

        示例文本是给用户照着填的模板，若它本身都解析不出记录，说明示例与
        解析规则已经脱节。
        """
        roster = roster_core.parse_roster_text(roster_core.SAMPLE_ROSTER_TEXT)

        self.assertGreaterEqual(len(roster), 4)
        self.assertEqual(roster.name_of("20230001"), "张三")

    def test_fill_both_ways(self) -> None:
        """补全函数应支持「有学号补姓名」与「有姓名补学号」两个方向。"""
        roster = roster_core.parse_roster_text("20230101,张三")

        self.assertEqual(roster.fill("20230101", "")[:2], ("20230101", "张三"))
        self.assertEqual(roster.fill("", "张三")[:2], ("20230101", "张三"))
        # 两者都有时不改动。
        self.assertEqual(roster.fill("20230101", "张三")[:2], ("20230101", "张三"))

    def test_fill_unknown_returns_original(self) -> None:
        """查不到时应保留原值，不得把已有信息抹掉。"""
        roster = roster_core.parse_roster_text("20230101,张三")

        student_id, student_name, note = roster.fill("99999999", "")
        self.assertEqual(student_id, "99999999")
        self.assertEqual(student_name, "")
        self.assertIn("不在信息表", note)

    def test_fill_with_nothing_gives_empty(self) -> None:
        """两项都空时应返回空值，供上层判为不合格。"""
        roster = roster_core.parse_roster_text("20230101,张三")

        student_id, student_name, note = roster.fill("", "")
        self.assertEqual((student_id, student_name), ("", ""))
        self.assertIn("既无学号也无姓名", note)


class TestRenameWithRoster(TempDirTestCase):
    """信息表与格式切换在改名计划中的综合测试。"""

    def _scan(self) -> list:
        """扫描临时目录。"""
        return scan_core.scan_directory(self.tmp_path)

    def test_missing_id_filled_from_roster(self) -> None:
        """文件名只有姓名时，学号应由信息表补出。"""
        self.make_file("张三_第一次作业.docx")
        roster = roster_core.parse_roster_text("20230101,张三")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{name}_{id}",
            source_format=("name", "title"),
            roster=roster,
        )

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "第一次作业_张三_20230101.docx")

    def test_missing_name_filled_from_roster(self) -> None:
        """文件名只有学号时，姓名应由信息表补出。"""
        self.make_file("20230101_第一次作业.docx")
        roster = roster_core.parse_roster_text("20230101,张三")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}_{name}",
            source_format=("id", "title"),
            roster=roster,
        )

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "第一次作业_20230101_张三.docx")

    def test_alternative_source_order(self) -> None:
        """原格式选「姓名_学号_作业名」时应能正确改名。

        这是需求明确要求的能力：文件名结构不必固定为
        「学号_姓名_作业名」。
        """
        self.make_file("张三_20230101_第一次作业.pdf")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("name", "id", "title"),
        )

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "第一次作业_20230101.pdf")

    def test_no_id_no_name_is_marked_invalid(self) -> None:
        """名称段既不是学号也不是姓名时应判为不合格并标红。

        场景：原格式声明第一段是学号，文件名却写着「第一次作业」。若照常
        改名会生成以作业名冒充学号的文件名，因此必须拦下并提示用户。
        """
        self.make_file("第一次作业_张三_报告.docx")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("id", "name", "title"),
        )

        invalid_names = [Path(p).name for p, _ in plan.invalid]
        self.assertIn("第一次作业_张三_报告.docx", invalid_names)
        self.assertEqual(len(plan.pending), 0)
        # 标红项不得同时出现在待改名清单里，否则会照常被改掉。
        pending_names = [action.old_name for action in plan.pending]
        self.assertNotIn("第一次作业_张三_报告.docx", pending_names)

    def test_invalid_paths_property(self) -> None:
        """``invalid_paths`` 应返回不合格文件的路径集合，供界面标红。"""
        self.make_file("第一次作业_张三_报告.docx")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("id", "name", "title"),
        )

        expected = self.tmp_path / "第一次作业_张三_报告.docx"
        self.assertIn(expected, plan.invalid_paths)

    def test_valid_files_not_marked_invalid(self) -> None:
        """信息完整的文件不应被误判为不合格。"""
        self.make_file("20230101_张三_第一次作业.pdf")
        self.make_file("20230102_李四_第二次作业.pdf")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("id", "name", "title"),
        )

        self.assertEqual(len(plan.invalid), 0)
        self.assertEqual(len(plan.pending), 2)

    def test_format_mismatch_goes_to_skipped_not_invalid(self) -> None:
        """结构不匹配应进入跳过清单，而非标红清单。

        两者的区别在于：结构不匹配通常说明文件与作业无关，标红则意味着
        「本该有学号姓名但实际没有」，需要教师留意。分区展示才能让用户
        分辨轻重。
        """
        self.make_file("随便起的名字.txt")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("id", "name", "title"),
        )

        self.assertEqual(len(plan.invalid), 0)
        skipped_names = [Path(p).name for p, _ in plan.skipped]
        self.assertIn("随便起的名字.txt", skipped_names)

    def test_format_plan_mentions_invalid_section(self) -> None:
        """预览文本应包含不合格区块，让命令行用户也能看到。"""
        self.make_file("第一次作业_张三_报告.docx")

        plan = rename_core.calculate_rename_plan(
            self._scan(),
            "{title}_{id}",
            source_format=("id", "name", "title"),
        )
        text = rename_core.format_plan(plan)

        self.assertIn("不合格", text)
        self.assertIn("既无学号也无姓名", text)


class TestSegmentSplit(unittest.TestCase):
    """段切分的测试：分隔符只支持 ``_``、``+`` 与空格。"""

    def test_split_underscore(self) -> None:
        """下划线应能切分。"""
        self.assertEqual(
            rename_core.split_segments("20230001_张三_第一次作业"),
            ["20230001", "张三", "第一次作业"],
        )

    def test_split_plus(self) -> None:
        """加号应能切分。"""
        self.assertEqual(
            rename_core.split_segments("张三+第一次作业"),
            ["张三", "第一次作业"],
        )

    def test_split_space(self) -> None:
        """半角空格应能切分。"""
        self.assertEqual(
            rename_core.split_segments("张三 第一次作业"),
            ["张三", "第一次作业"],
        )

    def test_split_fullwidth_space(self) -> None:
        """全角空格也应能切分。

        中文输入法下敲出的空格常是全角，若不支持会导致整串被判为一段。
        """
        self.assertEqual(
            rename_core.split_segments("张三\u3000第一次作业"),
            ["张三", "第一次作业"],
        )

    def test_split_mixed_and_repeated(self) -> None:
        """混合分隔符与连续分隔符应按一个处理，且丢弃空段。"""
        self.assertEqual(
            rename_core.split_segments("20230001__张三 + 第一次作业"),
            ["20230001", "张三", "第一次作业"],
        )


class TestInferFields(unittest.TestCase):
    """段推断规则的测试，对应需求新增的识别定义。"""

    def _infer(self, stem: str, preferred_titles=None):
        """调用推断函数。

        Args:
            stem: 文件名主体，不含扩展名。
            preferred_titles: 可选的作业名表，用于验证白名单优先匹配。
        """
        return rename_core.infer_fields_from_segments(stem, preferred_titles)

    def test_single_segment_is_rejected(self) -> None:
        """只有一段时应不予通过（标红）。

        无论是「张三」这样的姓名还是「简单作业」这样的作业名，单段都缺了
        另一项信息，无法拼出完整的新文件名。
        """
        for stem in ("张三", "简单作业", "20230001"):
            fields, rule, _ = self._infer(stem)
            self.assertIsNone(fields, f"{stem} 不应推断出字段")
            self.assertEqual(rule, rename_core.RULE_SINGLE)

    def test_id_plus_text_gives_id_and_title(self) -> None:
        """数字段 + 文本段：数字作学号，文本直接作作业名。

        注意即便文本段写的是姓名（如「20230001_张三」），按需求也应直接
        识别为作业名，不做二次校验。
        """
        fields, rule, _ = self._infer("20230001_张三")

        self.assertEqual(fields, ("20230001", "", "张三"))
        self.assertEqual(rule, rename_core.RULE_ID_TEXT)

    def test_text_plus_id_order_independent(self) -> None:
        """文本段在前、数字段在后时结论应相同。"""
        fields, rule, _ = self._infer("张三+20230001")

        self.assertEqual(fields, ("20230001", "", "张三"))
        self.assertEqual(rule, rename_core.RULE_ID_TEXT)

    def test_id_plus_keyword_text(self) -> None:
        """数字段 + 含特征词的文本段同样走「数字+文本」规则。"""
        fields, rule, _ = self._infer("20230001_第一次作业")

        self.assertEqual(fields, ("20230001", "", "第一次作业"))
        self.assertEqual(rule, rename_core.RULE_ID_TEXT)

    def test_two_texts_second_has_keyword(self) -> None:
        """两段文本，后段含特征词 → 后段作作业名，前段作姓名。"""
        fields, rule, _ = self._infer("张三_第一次作业")

        self.assertEqual(fields, ("", "张三", "第一次作业"))
        self.assertEqual(rule, rename_core.RULE_TITLE_KEYWORD)

    def test_two_texts_first_has_keyword(self) -> None:
        """两段文本，前段含特征词 → 前段作作业名，后段作姓名。"""
        fields, rule, _ = self._infer("第一次作业_张三")

        self.assertEqual(fields, ("", "张三", "第一次作业"))
        self.assertEqual(rule, rename_core.RULE_TITLE_KEYWORD)

    def test_two_texts_both_have_keyword_takes_trailing(self) -> None:
        """两段文本都含特征词时，后置段作作业名。

        这条规则是需求补充说明明确要求的，用于区分「作业_报告」这类两段
        都命中特征词的写法。
        """
        fields, rule, _ = self._infer("作业_报告")

        self.assertEqual(fields, ("", "作业", "报告"))
        self.assertEqual(rule, rename_core.RULE_TITLE_TRAILING)

    def test_two_texts_both_have_keyword_longer(self) -> None:
        """两段都含特征词的长例子同样取后置段。"""
        fields, rule, _ = self._infer("第一次作业_实验报告")

        self.assertEqual(fields, ("", "第一次作业", "实验报告"))
        self.assertEqual(rule, rename_core.RULE_TITLE_TRAILING)

    def test_two_texts_no_keyword_takes_trailing(self) -> None:
        """两段文本都不含特征词时，后置段作作业名，前段一律作姓名。"""
        fields, rule, _ = self._infer("张三_李四")

        self.assertEqual(fields, ("", "张三", "李四"))
        self.assertEqual(rule, rename_core.RULE_TITLE_TRAILING)

    def test_two_numbers_rejected(self) -> None:
        """两段都是数字时不予识别。

        既无法判断哪个是学号，也确定不了作业名。
        """
        fields, rule, _ = self._infer("20230001_20230002")

        self.assertIsNone(fields)
        self.assertEqual(rule, rename_core.RULE_AMBIGUOUS)

    def test_three_segments_id_plus_two_texts(self) -> None:
        """三段「数字 + 文本 + 文本」应被接受：数字作学号，两段文本按同一规则定作业名。"""
        fields, rule, _ = self._infer("20230001_张三_第一次作业")

        self.assertEqual(fields, ("20230001", "张三", "第一次作业"))
        self.assertEqual(rule, rename_core.RULE_TRIPLE)

    def test_three_segments_text_order_irrelevant(self) -> None:
        """三段里数字段的位置不影响结果，两段文本的先后顺序决定作业名。"""
        for stem, expected_title in (
            ("20230001_第一次作业_张三", "第一次作业"),  # 前段含特征词
            ("第一次作业_20230001_张三", "第一次作业"),  # 数字居中
            ("张三_20230001_数据结构实验", "数据结构实验"),  # 后段含特征词
        ):
            fields, rule, _ = self._infer(stem)
            self.assertIsNotNone(fields, f"{stem} 应推断出字段")
            self.assertEqual(rule, rename_core.RULE_TRIPLE)
            self.assertEqual(fields[2], expected_title, f"{stem} 的作业名应为 {expected_title}")

    def test_three_segments_no_keyword_takes_trailing(self) -> None:
        """三段中两段文本都不含特征词时，取后置段作作业名（与两段规则一致）。"""
        fields, rule, _ = self._infer("20230001_张三_李四")

        self.assertEqual(fields, ("20230001", "张三", "李四"))
        self.assertEqual(rule, rename_core.RULE_TRIPLE)

    def test_three_segments_two_ids_rejected(self) -> None:
        """三段里有数字段但不止一个数字段时不予识别。"""
        for stem in ("20230001_20230002_张三", "20230001_张三_李四_王五"):
            fields, rule, _ = self._infer(stem)
            self.assertIsNone(fields, f"{stem} 不应推断出字段")
            self.assertEqual(rule, rename_core.RULE_AMBIGUOUS)

    def test_four_segments_rejected(self) -> None:
        """四段及以上仍不予识别。"""
        fields, rule, _ = self._infer("a_b_c_d")
        self.assertIsNone(fields)
        self.assertEqual(rule, rename_core.RULE_AMBIGUOUS)

    def test_preferred_titles_take_priority(self) -> None:
        """提供作业名表时，与表中某项完全相同的段直接判为作业名。

        「读书笔记」不含内置特征词，靠启发式会被误判成姓名；用户手写的
        作业名表比特征词表权威，应当能纠正这一点。
        """
        preferred = ["读书笔记", "第一次作业"]

        fields, rule, _ = self._infer("读书笔记_冯十二_20230202", preferred)

        self.assertEqual(fields, ("20230202", "冯十二", "读书笔记"))
        self.assertEqual(rule, rename_core.RULE_WHITELIST)

    def test_preferred_titles_absent_keeps_heuristics(self) -> None:
        """不提供作业名表时，判定完全走启发式，行为不受影响。"""
        fields, rule, _ = self._infer("20230001_张三_第一次作业")

        self.assertEqual(fields, ("20230001", "张三", "第一次作业"))
        self.assertEqual(rule, rename_core.RULE_TRIPLE)

    def test_keyword_word_list_is_used(self) -> None:
        """作业名特征词表应覆盖常见作业类型。"""
        for text in ("第一次作业", "实验报告", "课程设计", "期末试卷", "预习总结"):
            fields, _, _ = self._infer(f"张三_{text}")
            self.assertEqual(fields[2], text, f"{text} 应被判为作业名")


class TestAutoRenamePlan(TempDirTestCase):
    """自动识别模式在改名计划中的测试。"""

    def _scan(self) -> list:
        """扫描临时目录。"""
        return scan_core.scan_directory(self.tmp_path)

    def _plan(self, roster=None, template: str = "{title}_{id}"):
        """以自动识别模式计算计划。"""
        return rename_core.calculate_rename_plan(
            self._scan(),
            template,
            source_format=rename_core.AUTO_SOURCE_FORMAT,
            roster=roster,
        )

    def test_auto_is_default_preset(self) -> None:
        """自动识别应是原格式下拉的首项。"""
        self.assertEqual(
            rename_core.SOURCE_FORMAT_PRESETS[0][1],
            rename_core.AUTO_SOURCE_FORMAT,
        )

    def test_id_and_text_rename(self) -> None:
        """「20230001_张三」应改名为「张三_20230001」。"""
        self.make_file("20230001_张三.docx")

        plan = self._plan()

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "张三_20230001.docx")

    def test_two_texts_rename(self) -> None:
        """「张三_第一次作业」应改名为「第一次作业_张三」。"""
        self.make_file("张三_第一次作业.docx")
        roster = roster_core.parse_roster_text("20230101,张三")

        plan = self._plan(roster)

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "第一次作业_20230101.docx")

    def test_both_keyword_takes_trailing(self) -> None:
        """两段都含特征词时，新名应基于后置段。"""
        self.make_file("作业_报告.docx")

        plan = self._plan()

        self.assertEqual(len(plan.pending), 1)
        # 后置段「报告」为作业名，前段「作业」为姓名，模板取 title。
        self.assertEqual(plan.pending[0].new_name, "报告.docx")

    def test_single_segment_marked_invalid(self) -> None:
        """单段文件名应标红而非跳过。"""
        self.make_file("孤零零.docx")

        plan = self._plan()

        invalid_names = [Path(p).name for p, _ in plan.invalid]
        self.assertIn("孤零零.docx", invalid_names)
        self.assertEqual(len(plan.pending), 0)
        self.assertEqual(len(plan.skipped), 0)

    def test_two_numbers_marked_invalid(self) -> None:
        """两段都是数字时应标红并说明理由。"""
        self.make_file("20230001_20230002.docx")

        plan = self._plan()

        invalid_names = [Path(p).name for p, _ in plan.invalid]
        self.assertIn("20230001_20230002.docx", invalid_names)
        reasons = " ".join(reason for _, reason in plan.invalid)
        self.assertIn("不予识别", reasons)

    def test_three_segments_now_recognized(self) -> None:
        """三段「数字 + 两段文本」现在能被识别，不再标红。

        这是本轮规则扩展的结果：「作业名_姓名_学号」这类三段命名在真实场景中
        很常见，若一律拒绝，会导致绝大多数文件无法改名。
        """
        self.make_file("20230001_张三_第一次作业.docx")

        plan = self._plan()

        invalid_names = [Path(p).name for p, _ in plan.invalid]
        self.assertNotIn("20230001_张三_第一次作业.docx", invalid_names)

        pending = {action.old_name: action.new_name for action in plan.pending}
        self.assertIn("20230001_张三_第一次作业.docx", pending)
        # 默认模板为「作业名_学号」，三段应能拼出这个结果。
        self.assertEqual(pending["20230001_张三_第一次作业.docx"], "第一次作业_20230001.docx")

    def test_invalid_rows_are_not_renamed(self) -> None:
        """标红文件绝不能出现在待改名清单里。"""
        self.make_file("孤零零.docx")
        self.make_file("20230001_张三.docx")

        plan = self._plan()

        pending_names = [action.old_name for action in plan.pending]
        self.assertNotIn("孤零零.docx", pending_names)
        self.assertIn("20230001_张三.docx", pending_names)

    def test_roster_fills_name_in_auto_mode(self) -> None:
        """自动识别模式下信息表仍应补全缺失的姓名。"""
        self.make_file("20230001_第一次作业.docx")
        roster = roster_core.parse_roster_text("20230001,张三")

        plan = self._plan(roster, template="{title}_{id}_{name}")

        self.assertEqual(len(plan.pending), 1)
        self.assertEqual(plan.pending[0].new_name, "第一次作业_20230001_张三.docx")
        self.assertEqual(plan.pending[0].parsed_name, "张三")

    def test_rule_name_is_exposed(self) -> None:
        """识别结果应带上规则名，供界面「判定依据」列展示。"""
        self.make_file("20230001_张三.docx")

        plan = self._plan()

        self.assertIn("数字+文本", plan.pending[0].fill_note)

    def test_format_plan_shows_reasons(self) -> None:
        """预览文本应写明标红原因。"""
        self.make_file("孤零零.docx")

        text = rename_core.format_plan(self._plan())

        self.assertIn("不合格", text)
        self.assertIn("仅一段", text)


class TestRenameTitleWhitelist(TempDirTestCase):
    """需求 2 作业白名单接入改名流程的测试。

    白名单的存在意义是「允许用户直接指定哪一段是作业名」，从而救回那些
    不含内置特征词、启发式必然误判的作业名（如「宏观经济学」）。

    注意：白名单只在**自动识别**模式下生效，因此这里的所有计划都显式传
    ``source_format=AUTO_SOURCE_FORMAT``——这也是界面上的默认选项。
    """

    def _plan(self, preferred_titles=None) -> rename_core.RenamePlan:
        """扫描临时目录并按自动识别模式计算改名计划。"""
        records = scan_core.scan_directory(self.tmp_path)
        return rename_core.calculate_rename_plan(
            records=records,
            source_format=rename_core.AUTO_SOURCE_FORMAT,
            preferred_titles=preferred_titles,
        )

    def test_whitelist_corrects_misjudged_title(self) -> None:
        """白名单能把被误判成姓名的作业名纠正回来。

        「宏观经济学_王小明_20230202」两段文本都不含特征词、长度也相近，
        启发式只能按「取后置段」把「王小明」当作业名；把「宏观经济学」写进
        白名单后，识别结果被纠正。
        """
        self.make_file("宏观经济学_王小明_20230202.txt")

        without = self._plan(None)
        with_list = self._plan(["宏观经济学"])

        self.assertEqual(without.pending[0].new_name, "王小明_20230202.txt")
        self.assertEqual(with_list.pending[0].new_name, "宏观经济学_20230202.txt")

    def test_whitelist_hit_note_mentions_whitelist(self) -> None:
        """命中白名单时，判定依据要写明是「命中作业表」，方便用户核对。"""
        self.make_file("宏观经济学_王小明_20230202.txt")

        plan = self._plan(["宏观经济学"])

        self.assertIn("命中作业表", plan.pending[0].fill_note)

    def test_empty_whitelist_keeps_heuristics(self) -> None:
        """白名单为空时不改变行为，仍走启发式。"""
        self.make_file("第一次作业_张三_20230101.docx")

        empty = self._plan([])
        none_list = self._plan(None)

        self.assertEqual(
            empty.pending[0].new_name, none_list.pending[0].new_name
        )

    def test_whitelist_does_not_replace_missing_id_and_name(self) -> None:
        """白名单只定作业名，不能替代学号与姓名。

        「随便起的名字」只有一段，既无学号也无姓名。即使把它写进白名单、
        它确实被认成了作业名，该文件仍应判为不合格并标红——因为学号与姓名
        一个都没有。这正是「白名单不能当学号姓名用」的体现。
        """
        self.make_file("随便起的名字.docx")

        plan = self._plan(["随便起的名字"])

        self.assertEqual(plan.pending, [])
        self.assertEqual(len(plan.invalid), 1)
        reason = plan.invalid[0][1]
        self.assertIn("既无学号也无姓名", reason)
        # 白名单确实生效了（被认成作业名），只是救不了「没有学号姓名」这一项。
        self.assertIn("判为作业名", reason)

    def test_whitelist_ignored_for_fixed_format(self) -> None:
        """固定排列下作业名由字段位置决定，白名单不参与。

        这里传入的白名单与文件名内容毫不相干，若被误用会导致作业名错乱；
        实际结果应与不传白名单完全一致。
        """
        self.make_file("20230001_张三_第一次作业.docx")
        records = scan_core.scan_directory(self.tmp_path)
        source_format = ("id", "name", "title")

        with_list = rename_core.calculate_rename_plan(
            records=records,
            source_format=source_format,
            preferred_titles=["毫不相干的名词"],
        )

        self.assertEqual(with_list.pending[0].new_name, "第一次作业_20230001.docx")

    def test_build_new_name_accepts_whitelist(self) -> None:
        """``build_new_name`` 也应能直接接收白名单参数（自动识别模式）。"""
        built = rename_core.build_new_name(
            "宏观经济学_王小明_20230202.txt",
            source_format=rename_core.AUTO_SOURCE_FORMAT,
            preferred_titles=["宏观经济学"],
        )

        self.assertIsNotNone(built)
        self.assertEqual(built[0], "宏观经济学_20230202.txt")
        self.assertEqual(built[3], "宏观经济学")


class TestHomeworkCheck(TempDirTestCase):
    """作业检查（按检索信息汇总）的测试。

    汇总的判定规则只有一条：**达标份数 ≥ 1 就标绿，否则标红**。读取失败的
    份数单独成列，既不并入未达标，也不影响绿标——这与逐文件明细里
    「读不出来不等于没写够」的口径保持一致。
    """

    def _check(
        self,
        keywords: list[str],
        threshold: int = 500,
    ) -> wordcount_core.HomeworkCheckSummary:
        """扫描临时目录、统计字数并构建汇总结果。"""
        records = scan_core.scan_directory(self.tmp_path)
        results, _ = wordcount_core.scan_documents_wordcount(
            records, threshold=threshold
        )
        return wordcount_core.build_homework_check(
            results, keywords, threshold=threshold
        )

    def _row(
        self,
        summary: wordcount_core.HomeworkCheckSummary,
        keyword: str,
    ) -> wordcount_core.HomeworkCheckRow:
        """按关键词取出汇总行，取不到时让测试直接失败。"""
        for row in summary.rows:
            if row.keyword == keyword:
                return row
        self.fail(f"汇总结果里没有 {keyword!r} 这一行")

    def test_matching_is_exact_segment(self) -> None:
        """匹配口径是「整段相等」，不做子串包含。

        「张三丰」里的「张三」是子串，不该命中——否则统计会虚高。
        """
        long_file = self.make_file("张三丰_第一次作业.txt", "字" * 600)
        result = wordcount_core.count_words_in_file(long_file)

        self.assertEqual(
            wordcount_core.classify_file_by_info(result, ["张三"]), []
        )
        self.assertEqual(
            wordcount_core.classify_file_by_info(result, ["张三丰"]),
            ["张三丰"],
        )

    def test_matches_across_all_separators(self) -> None:
        """`_`、`+`、空格三种分隔符切出的段都能被检索信息命中。"""
        for name in ("张三_第一次作业.txt", "张三+第一次作业.txt", "张三 第一次作业.txt"):
            path = self.make_file(name, "字" * 600)
            result = wordcount_core.count_words_in_file(path)
            self.assertEqual(
                wordcount_core.classify_file_by_info(result, ["张三", "第一次作业"]),
                ["张三", "第一次作业"],
                msg=f"{name} 应同时命中两条",
            )

    def test_counts_pass_below_and_failed(self) -> None:
        """识别份数 / 达标份数 / 未达标份数 / 读取失败四类计数各自正确。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        self.make_file("张三_第二次作业.txt", "字" * 10)
        self.make_file("张三_第三次作业.pdf", "not a real pdf")

        row = self._row(self._check(["张三"]), "张三")

        self.assertEqual(row.total, 3)
        self.assertEqual(row.passed, 1)
        self.assertEqual(row.below, 1)
        self.assertEqual(row.failed, 1)

    def test_failed_does_not_count_as_below(self) -> None:
        """读取失败的份数不计入未达标。

        与逐文件明细一致：读不出来不等于没写够。
        """
        self.make_file("张三_第一次作业.pdf", "not a real pdf")

        row = self._row(self._check(["张三"]), "张三")

        self.assertEqual(row.failed, 1)
        self.assertEqual(row.below, 0)

    def test_green_when_at_least_one_passed(self) -> None:
        """达标份数 ≥ 1 即标绿，哪怕同时夹着未达标。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        self.make_file("张三_第二次作业.txt", "字" * 10)

        row = self._row(self._check(["张三"]), "张三")

        self.assertEqual(row.below, 1)
        self.assertTrue(row.is_green)

    def test_red_when_nobody_passed(self) -> None:
        """达标份数为 0 时标红。"""
        self.make_file("李四_第一次作业.txt", "字" * 10)

        row = self._row(self._check(["李四"]), "李四")

        self.assertEqual(row.passed, 0)
        self.assertFalse(row.is_green)

    def test_red_when_nobody_matched(self) -> None:
        """一份都没识别到时也标红，且各项计数为 0。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)

        row = self._row(self._check(["王五"]), "王五")

        self.assertEqual(row.total, 0)
        self.assertFalse(row.is_green)

    def test_file_matching_multiple_keywords_counts_in_each(self) -> None:
        """一个文件命中多条检索信息时，同时计入每一行。"""
        self.make_file("张三_20230101_第一次作业.txt", "字" * 600)

        summary = self._check(["张三", "20230101", "第一次作业"])

        for row in summary.rows:
            self.assertEqual(row.total, 1, msg=f"{row.keyword} 应各计 1 份")

    def test_rows_follow_keyword_order(self) -> None:
        """汇总行顺序与检索信息顺序严格一致。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        keywords = ["第一次作业", "张三", "不存在的人"]

        summary = self._check(keywords)

        self.assertEqual([row.keyword for row in summary.rows], keywords)

    def test_all_green_requires_non_empty_rows(self) -> None:
        """空表不算「全部通过」，避免给出误导性的绿勾。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)

        summary = self._check([])

        self.assertEqual(summary.rows, [])
        self.assertFalse(summary.all_green)

    def test_all_green_true_when_every_row_green(self) -> None:
        """每一行都达标时整体判定为全绿。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        self.make_file("李四_第一次作业.txt", "字" * 600)

        summary = self._check(["张三", "李四"])

        self.assertTrue(summary.all_green)
        self.assertEqual(summary.red_rows, [])

    def test_red_rows_identifies_offenders(self) -> None:
        """标红行能被点名，供弹窗提示使用。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        self.make_file("李四_第一次作业.txt", "字" * 10)

        summary = self._check(["张三", "李四"])

        self.assertEqual([row.keyword for row in summary.red_rows], ["李四"])

    def test_unmatched_collects_files_without_any_hit(self) -> None:
        """没命中任何检索信息的文件单独收集，不拉低任何一行。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        self.make_file("随便起的名.txt", "字" * 600)

        summary = self._check(["张三"])

        self.assertEqual(summary.unmatched, ["随便起的名.txt"])
        self.assertEqual(self._row(summary, "张三").total, 1)

    def test_keywords_are_whitespace_and_duplicate_safe(self) -> None:
        """关键词表经过解析后应已去空白、去重且保序。"""
        keywords = archive_core.parse_keyword_list_text("张三\n\n  张三  \n# 注释\n李四\n")

        self.assertEqual(keywords, ["张三", "李四"])

    def test_summary_text_shows_all_columns(self) -> None:
        """汇总文本要展示全部五列（含读取失败）。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)

        text = wordcount_core.format_homework_check_summary(self._check(["张三"]))

        for header in ("输入信息", "识别份数", "达标份数", "未达标份数", "读取失败"):
            self.assertIn(header, text)

    def test_summary_text_warns_about_red_rows(self) -> None:
        """存在标红行时，汇总文本要明确点名警示。"""
        self.make_file("李四_第一次作业.txt", "字" * 10)

        text = wordcount_core.format_homework_check_summary(self._check(["李四"]))

        self.assertIn("未通过", text)
        self.assertIn("李四", text)

    def test_summary_text_handles_empty_rows(self) -> None:
        """空表渲染成一句说明，而不是空白或异常。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)

        text = wordcount_core.format_homework_check_summary(self._check([]))

        self.assertIn("未填写检索信息", text)

    def test_report_includes_summary_section(self) -> None:
        """提供 summary 时报告要含「按检索信息汇总」区块。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        records = scan_core.scan_directory(self.tmp_path)
        results, _ = wordcount_core.scan_documents_wordcount(records, threshold=500)
        summary = wordcount_core.build_homework_check(
            results, ["张三"], threshold=500
        )

        report = wordcount_core.build_wordcount_report(
            results, threshold=500, summary=summary
        )

        self.assertIn("按检索信息汇总", report)
        self.assertIn("作业检查报告", report)

    def test_report_without_summary_has_no_section(self) -> None:
        """不提供 summary 时报告不含汇总区块（保持向后兼容）。"""
        self.make_file("张三_第一次作业.txt", "字" * 600)
        records = scan_core.scan_directory(self.tmp_path)
        results, _ = wordcount_core.scan_documents_wordcount(records, threshold=500)

        report = wordcount_core.build_wordcount_report(results, threshold=500)

        self.assertNotIn("按检索信息汇总", report)

    def test_sample_check_info_is_parseable(self) -> None:
        """内置示例能被自身解析器解析，保证「填入示例」按钮可用。"""
        keywords = archive_core.parse_keyword_list_text(
            wordcount_core.SAMPLE_CHECK_INFO_TEXT
        )

        self.assertEqual(keywords, ["张三", "李四", "王五", "20230101", "第一次作业"])

    def test_end_to_end_on_real_files(self) -> None:
        """在真实文件上跑通「扫描 → 统计 → 汇总」整条链路。"""
        self.make_file("张三_20230101_第一次作业.txt", "字" * 600)
        self.make_file("李四_20230202_第一次作业.txt", "字" * 10)
        self.make_file("随便起的名.txt", "字" * 600)

        summary = self._check(["张三", "李四", "20230101"])

        self.assertEqual(len(summary.rows), 3)
        self.assertTrue(self._row(summary, "张三").is_green)
        self.assertFalse(self._row(summary, "李四").is_green)
        # 学号与张三是同一个文件的两个段，应各计一份。
        self.assertEqual(self._row(summary, "20230101").total, 1)
        self.assertFalse(summary.all_green)
        self.assertEqual(summary.unmatched, ["随便起的名.txt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
