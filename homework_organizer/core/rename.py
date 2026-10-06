"""需求 2：批量改名。

本模块按命名模板统一文件名称，例如将 ``学号_姓名_作业名.pdf`` 改为
``作业名_学号.pdf``。本模块为项目中会修改磁盘文件的模块之一，因此以安全性
为首要设计目标。

设计约束：
    1. 执行前必须输出改名预览，经用户确认后方可修改文件。
    2. 目标名已被占用时跳过该文件并说明原因，不覆盖任何已有文件。
    3. 同一批次内两个文件计算出相同新名时，仅批准其中一个，避免互相覆盖。
    4. 文件名中「学号」与「姓名」至少出现一项，两项皆缺的文件判为不合格。

实现上将流程拆分为三个阶段，以 :class:`RenamePlan` 衔接：

    计算阶段  :func:`calculate_rename_plan`  产出计划，不修改磁盘
    确认阶段  图形界面弹窗或命令行确认
    执行阶段  :func:`apply_rename_plan`      实际调用 ``Path.rename``

如此，预览并非仅作为提示文本，而是架构上必需的一步；未经计划阶段无法进入
执行阶段，从代码结构上排除"直接改名"的可能。

关于「原格式可配置」：

需求方希望支持 ``姓名_学号_作业名`` 之类的其它排列，而非只认
``学号_姓名_作业名``。为此原格式由字段顺序描述——:data:`SourceFormat`
是一个由 ``"id"``/``"name"``/``"title"`` 组成的有序元组，表示文件名各段
的排列。例如 ``("name", "id", "title")`` 表示「姓名_学号_作业名」。
各段如何切分见 :func:`parse_structured_name` 的说明。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

from .roster import Roster

__all__ = [
    "FieldKind",
    "SourceFormat",
    "AUTO_SOURCE_FORMAT",
    "DEFAULT_SOURCE_FORMAT",
    "SOURCE_FORMAT_PRESETS",
    "TARGET_TEMPLATE_PRESETS",
    "DEFAULT_TEMPLATE",
    "RULE_SINGLE",
    "RULE_ID_TEXT",
    "RULE_TITLE_KEYWORD",
    "RULE_TITLE_TRAILING",
    "RULE_TRIPLE",
    "RULE_WHITELIST",
    "RULE_AMBIGUOUS",
    "RULE_DESCRIPTIONS",
    "RenameAction",
    "RenamePlan",
    "split_segments",
    "match_segments",
    "parse_structured_name",
    "build_new_name",
    "validate_template",
    "looks_like_id",
    "looks_like_name",
    "infer_fields_from_segments",
    "calculate_rename_plan",
    "apply_rename_plan",
    "format_plan",
]


class FieldKind(str, Enum):
    """文件名中一段的语义。"""

    ID = "id"
    """学号。"""

    NAME = "name"
    """姓名。"""

    TITLE = "title"
    """作业名。"""


#: 原文件名的字段排列。元素取自 :class:`FieldKind` 的值。
SourceFormat = tuple[str, ...]

#: 原格式取此值时启用**自动识别**：不看固定字段顺序，改为按段数与各段内容
#: 类型套用规则表推断（见 :func:`infer_fields_from_segments`）。
AUTO_SOURCE_FORMAT: SourceFormat = ("auto",)

#: 需求原文示例所用的原格式。
DEFAULT_SOURCE_FORMAT: SourceFormat = ("id", "name", "title")

#: 原格式下拉框的候选项，元素为 ``(显示文本, 格式元组)``。
#: 首项为自动识别，是推荐用法；其余为固定排列，用于文件名结构已知的场合。
SOURCE_FORMAT_PRESETS: list[tuple[str, SourceFormat]] = [
    ("自动识别（推荐）", AUTO_SOURCE_FORMAT),
    ("学号_姓名_作业名", ("id", "name", "title")),
    ("姓名_学号_作业名", ("name", "id", "title")),
    ("学号_作业名", ("id", "title")),
    ("姓名_作业名", ("name", "title")),
    ("作业名_学号", ("title", "id")),
    ("作业名_姓名", ("title", "name")),
    ("学号_姓名_作业名_序号", ("id", "name", "title", "extra")),
]

#: 目标命名模板下拉框的候选项，元素为 ``(显示文本, 模板)``。
TARGET_TEMPLATE_PRESETS: list[tuple[str, str]] = [
    ("作业名_学号", "{title}_{id}"),
    ("作业名_姓名", "{title}_{name}"),
    ("作业名_学号_姓名", "{title}_{id}_{name}"),
    ("作业名_姓名_学号", "{title}_{name}_{id}"),
    ("学号_作业名", "{id}_{title}"),
    ("姓名_作业名", "{name}_{title}"),
    ("作业名", "{title}"),
]

#: 默认命名模板，对应需求示例「作业名_学号」。
DEFAULT_TEMPLATE = "{title}_{id}"

#: 允许出现在模板中的占位符字样。
_VALID_PLACEHOLDERS = frozenset({"id", "name", "title"})

# Windows 文件名中不允许出现的字符。
_ILLEGAL_CHARS = '<>:"/\\|?*'

# 文件名中段与段之间的分隔符。
#
# 规则明确要求支持 ``_``、``+`` 与空格三种。空格在 Windows 文件名中合法但
# 罕见，多见于从聊天记录或邮件标题里粘出来的名字，因此一并支持。
# 半角空格与全角空格都要覆盖——中文输入法下敲出的空格常是全角。
_SEGMENT_SEPARATORS = ("_", "+", " ", "\t", "\u3000")

# 用于构造切分正则的字符类。正则元字符需转义。
_SEGMENT_SPLIT_PATTERN = re.compile(
    "[" + "".join(re.escape(sep) for sep in _SEGMENT_SEPARATORS) + "]+"
)

# 判断某段文本「像不像学号」：纯数字、或数字字母混排且含至少一位数字。
# 用于识别「原格式声明这里是学号，但实际内容其实是作业名」的情况。
_ID_LIKE_PATTERN = re.compile(r"^[A-Za-z]*\d[A-Za-z0-9\-]*$")

# 判断某段文本「像不像作业名」：含中文且不像人名。作业名通常比姓名长，
# 且常常带有「作业」「报告」「实验」「第N次」等字样。
#
# 词表覆盖高校常见作业类型。此表仅影响"两段或三段文本里优先选哪段当作业名"，
# 判不准时仍会退回"取后置段"，因此宁可多列一些词，也不要让明显是作业名的
# 词汇（如「读书笔记」）落在表外而被误判成姓名。
_TITLE_LIKE_WORDS = (
    "作业", "报告", "实验", "论文", "练习", "习题", "总结", "心得",
    "第", "次", "课设", "设计", "试卷", "答案", "方案", "分析", "预习",
    "笔记", "读书", "摘要", "综述", "翻译", "演讲", "展示", "调研",
    "编程", "上机", "代码", "项目", "案例", "实习", "周记", "日记",
    "期末", "期中", "考核", "答辩", "开题", "文献", "期刊", "调查",
)

#: 推断单段语义时使用的规则名，供界面展示"为什么这样判定"。
#:
#: 同一段代码可能按不同规则得出相同结论，规则名用于区分是通过哪条路径判定的。
#: 各常量取值即界面「判定依据」列显示的中文短语，说明文本见 :data:`RULE_DESCRIPTIONS`。
RULE_SINGLE = "单段"
#: 一段数字 + 一段文本，文本段直接当作业名。
RULE_ID_TEXT = "数字+文本"
#: 两段文本中命中作业名特征词者判为作业名。
RULE_TITLE_KEYWORD = "含作业字样"
#: 两段文本均无特征词，后置段判为作业名。
RULE_TITLE_TRAILING = "后段作作业名"
#: 三段（数字 + 两段文本），数字作学号，两段文本沿用上面的规则。
RULE_TRIPLE = "数字+两段文本"
#: 某一段命中用户提供的作业名表，优先级最高。
RULE_WHITELIST = "命中作业表"
#: 段数或内容不符合识别规则，不予识别。
RULE_AMBIGUOUS = "无法识别"

#: 各规则的说明文本，界面表格的"判定依据"列直接取用。
RULE_DESCRIPTIONS: dict[str, str] = {
    RULE_SINGLE: "只有一段信息，缺少学号或姓名，不予通过",
    RULE_ID_TEXT: "数字段视为学号，文本段直接视为作业名",
    RULE_TITLE_KEYWORD: "两段文本中命中作业名特征词者判为作业名",
    RULE_TITLE_TRAILING: "两段文本均无作业名特征词，后置段判为作业名",
    RULE_TRIPLE: "数字段作学号，两段文本按「含特征词优先、否则取后置段」定作业名",
    RULE_WHITELIST: "某一段与用户提供的作业名表完全一致，直接判为作业名",
    RULE_AMBIGUOUS: "段数或内容不符合识别规则，不予识别",
}


@dataclass
class RenameAction:
    """单条改名动作，描述某文件由旧名变更为新名的映射。

    该对象同时用于预览展示与实际执行，使"用户所见"与"实际执行"始终一致。
    """

    source: Path
    """源文件完整路径。"""

    old_name: str
    """原文件名，不含目录部分。"""

    new_name: str
    """新文件名，不含目录部分。仅在原目录内改名，不涉及移动。"""

    changed: bool
    """是否需要执行改名。``False`` 表示新旧同名，执行也为空操作。"""

    parsed_id: str = ""
    """从原文件名中解析出的学号，未补全前的原始值。"""

    parsed_name: str = ""
    """从原文件名中解析出的姓名，未补全前的原始值。"""

    parsed_title: str = ""
    """从原文件名中解析出的作业名。"""

    fill_note: str = ""
    """字段补全说明。空字符串表示无需补全或文件名信息已完整。"""

    @property
    def new_path(self) -> Path:
        """新文件的完整路径，等于原目录拼接新文件名。

        Returns:
            新文件路径。目录取自 :attr:`source` 的父目录，保证改名后文件
            仍位于原位置。
        """
        return self.source.parent / self.new_name


@dataclass
class RenamePlan:
    """一批改名操作的完整计划，包含待执行动作与被跳过项。

    跳过项记录为 ``(文件路径, 跳过原因)`` 二元组。选择"跳过并说明原因"而非
    "自动追加序号"，是因为后者会静默产生 ``a.pdf``、``a_1.pdf``、``a_2.pdf``
    等文件，用户更难察觉异常。
    """

    actions: list[RenameAction] = field(default_factory=list)
    """全部改名动作，含无需改动的项。"""

    skipped: list[tuple[Path, str]] = field(default_factory=list)
    """被跳过的文件及其原因。"""

    invalid: list[tuple[Path, str]] = field(default_factory=list)
    """因缺少学号与姓名而被判为不合格的文件及原因。

    单独成列是为了让界面把这些行标红——它们与"文件名结构不符"属于不同性质：
    结构不符通常说明文件本身无关紧要，而缺少学号姓名往往意味着学生提交不
    规范，需要教师留意。
    """

    @property
    def pending(self) -> list[RenameAction]:
        """需要实际执行的动作列表。

        Returns:
            :attr:`actions` 中 ``changed`` 为 ``True`` 的子集。
        """
        return [action for action in self.actions if action.changed]

    @property
    def unchanged_count(self) -> int:
        """文件名已符合规则、无需改动的数量。

        Returns:
            无需改动的动作数量。
        """
        return sum(1 for action in self.actions if not action.changed)

    @property
    def invalid_paths(self) -> set[Path]:
        """不合格文件的路径集合，供界面快速判定某行是否标红。

        Returns:
            路径集合。
        """
        return {path for path, _ in self.invalid}


def looks_like_id(text: str) -> bool:
    """判断一段文本是否像学号。

    学号通常为纯数字或字母数字混排（如 ``20230001``、``2023CS001``）。纯中文
    文本一定不是学号。

    Args:
        text: 待判断的文本片段。

    Returns:
        像学号时为 ``True``。
    """
    text = text.strip()
    if not text:
        return False
    # 出现中文即不可能是学号。
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return False
    return _ID_LIKE_PATTERN.match(text) is not None


def looks_like_name(text: str) -> bool:
    """判断一段文本是否像姓名。

    只做保守排除，不做精确识别：排除含数字（学号特征）、含作业名特征词
    （「作业」「报告」「第N次」等）的文本。长度不作为判据——复姓加名、
    少数民族姓名、带学位后缀的写法都可能超过三四个汉字，若以长度设限会
    频繁误伤真实姓名，把本该正常改名的文件标红。

    Args:
        text: 待判断的文本片段。

    Returns:
        像姓名时为 ``True``。
    """
    text = text.strip()
    if not text:
        return False
    if any(char.isdigit() for char in text):
        return False
    if any(word in text for word in _TITLE_LIKE_WORDS):
        return False
    # 至少含一个中文字符，否则更可能是英文作业名。
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _field_is_plausible(kind: str, text: str) -> bool:
    """判断某段文本是否配得上它声明的语义。

    Args:
        kind: 段的语义，``"id"`` 或 ``"name"``。
        text: 该段的文本内容。

    Returns:
        语义相符时为 ``True``；``title`` 等其它类型一律返回 ``True``。
    """
    if kind == FieldKind.ID.value:
        return looks_like_id(text)
    if kind == FieldKind.NAME.value:
        return looks_like_name(text)
    return True


def split_segments(stem: str) -> list[str]:
    """按 ``_``、``+``、空格把文件名主体切成若干段。

    连续的分隔符视为一个，切分后丢弃空白段。例如 ``"20230001__张三"`` 得到
    ``["20230001", "张三"]``。

    Args:
        stem: 文件名主体，不含扩展名。

    Returns:
        非空段组成的列表，按原顺序排列。
    """
    return [seg.strip() for seg in _SEGMENT_SPLIT_PATTERN.split(stem) if seg.strip()]


def match_segments(stem: str, keywords: Sequence[str] | None = None) -> list[str]:
    """把文件名切成任意多段，返回其中命中关键词表的段。

    与 :func:`infer_fields_from_segments` 的"推断"思路不同，本函数不做任何
    语义猜测——它只做一件事：**逐段与用户给出的表比对**。因此：

    * 不限制段数，两段、三段、十段都可以；
    * 表里写什么就匹配什么，作业名、姓名、学号一视同仁；
    * 命中的段原样返回，直接用作文件夹名。

    Args:
        stem: 文件名主体，不含扩展名。
        keywords: 关键词表。传 ``None`` 或空序列时返回空列表（无表即无匹配）。

    Returns:
        命中关键词的段列表，按段在文件名中出现的顺序排列，重复项只保留一次。
    """
    if not keywords:
        return []

    # 去空白后建集合：表里可能有手误留的空行或前后空格。
    wanted = {item.strip() for item in keywords if item.strip()}
    if not wanted:
        return []

    matched: list[str] = []
    seen: set[str] = set()
    for segment in split_segments(stem):
        if segment not in wanted or segment in seen:
            continue
        seen.add(segment)
        matched.append(segment)

    return matched


def _contains_title_word(text: str) -> bool:
    """判断一段文本是否含有作业名特征词。

    Args:
        text: 待判断的文本片段。

    Returns:
        命中任一特征词时为 ``True``。
    """
    return any(word in text for word in _TITLE_LIKE_WORDS)


def _segment_kind(text: str) -> str:
    """判定一段文本的内容类型。

    判定顺序为「数字优先于文本」：先看是否像学号，再看是否含作业名特征词，
    都不满足则视为普通文本（通常是姓名）。含特征词者单独标为 ``title``
    只是为了在界面上说明"它为什么被判为作业名"，分类时仍与 ``text`` 同属
    文本类，见 :func:`_is_text_like`。

    Args:
        text: 待判定的文本片段。

    Returns:
        ``"id"``（像学号）、``"title"``（含作业名特征词）、``"text"``
        （不含数字、不含作业名特征词的纯文本）。
    """
    if looks_like_id(text):
        return FieldKind.ID.value
    if _contains_title_word(text):
        return FieldKind.TITLE.value
    return "text"


def _is_text_like(kind: str) -> bool:
    """判断某段是否属于「文本类」。

    含作业名特征词（``title``）与普通文本（``text``）在规则表里同属"那一段
    文本"，区别只在于前者能作为判定作业名的依据。用本函数统一收敛，可避免
    在规则分支里反复罗列两种代号。

    Args:
        kind: 由 :func:`_segment_kind` 得出的内容类型。

    Returns:
        属于文本类时为 ``True``。
    """
    return kind in ("text", FieldKind.TITLE.value)


def infer_fields_from_segments(
    stem: str,
    preferred_titles: Sequence[str] | None = None,
) -> tuple[tuple[str, str, str] | None, str, str]:
    """按段数与各段内容类型推断学号、姓名与作业名。

    这是需求中新增的识别定义。核心思路是不再要求文件名符合某个固定的字段
    排列，而是先切段、再看每段「像什么」，最后套用规则表。

    规则表（``T`` = 文本段，``N`` = 数字段；文本段包含含特征词者）：

    ==================  ====================================================
    输入                 判定
    ==================  ====================================================
    仅 1 段              不予通过（缺少学号或姓名），标红
    2 段：N + T          数字段作学号，文本段**直接**作作业名
    2 段：T + T          含特征词者作作业名；两段都含时**取后置段**；
                        两段都不含时也**取后置段**；另一段一律作姓名
    3 段：N + T + T      数字段作学号；余下两段文本套用上面的 T + T 规则
    其余情况             不予识别（如 N + N、T + T + T）
    ==================  ====================================================

    若提供了 ``preferred_titles``（作业归类场景传入用户的作业名表），则在
    上述规则**之前**先做一次精确匹配：某一文本段整体等于表内某项时，直接
    认定它就是作业名，另一段作姓名。用户手写的作业名表比程序的特征词表更
    权威——「读书笔记」这类不含内置特征词的作业名，只有靠这张表才能与
    姓名区分开。该参数默认为 ``None``，因此不影响需求二的改名行为。

    Args:
        stem: 文件名主体，不含扩展名。
        preferred_titles: 用户提供的作业名清单，用于优先匹配。可传 ``None``。

    Returns:
        三元组 ``(字段, 规则名, 说明)``：

        * 字段 —— ``(学号, 姓名, 作业名)``，位置为空字符串表示该字段未识别出；
          整体为 ``None`` 表示不予通过或不予识别。
        * 规则名 —— 命中的 :data:`RULE_SINGLE` 等常量，供界面标示判定依据。
        * 说明 —— 一句话解释为什么这样判，直接显示在预览表里。
    """
    segments = split_segments(stem)

    # 规则零（仅在提供了作业名表时启用）：表中某项与某一段完全相等，直接认定
    # 该段为作业名。这条规则放在最前面，因为它最有说服力——用户亲手写下的
    # 作业名比任何启发式判断都权威。命中后其余文本段作姓名、数字段作学号。
    if preferred_titles:
        whitelist = {item.strip() for item in preferred_titles if item.strip()}
        for index, segment in enumerate(segments):
            if segment.strip() not in whitelist:
                continue
            rest = [seg for pos, seg in enumerate(segments) if pos != index]
            student_id = ""
            name = ""
            for seg in rest:
                # 数字段优先当学号；其余文本段按出现顺序取第一个作姓名。
                if _segment_kind(seg) == FieldKind.ID.value and not student_id:
                    student_id = seg
                elif not name:
                    name = seg
            return (
                (student_id, name, segment),
                RULE_WHITELIST,
                f"「{segment}」命中作业名表中的条目，判为作业名",
            )

    # 规则一：只有一段，既无学号也无姓名。
    if len(segments) == 1:
        return None, RULE_SINGLE, f"仅一段「{segments[0]}」，缺少学号或姓名"

    # 规则五：三段，且恰好一段是数字、另外两段是文本。这是「作业名_姓名_学号」
    # 这类常见命名的形态——三段里只有一段能当学号，剩下两段文本按「两段文本」
    # 的同一条规则定作业名，因此复用 _resolve_two_texts。
    if len(segments) == 3:
        kinds = [_segment_kind(seg) for seg in segments]
        id_positions = [
            idx for idx, kind in enumerate(kinds)
            if kind == FieldKind.ID.value and not _is_text_like(kind)
        ]
        text_positions = [
            idx for idx, kind in enumerate(kinds) if _is_text_like(kind)
        ]

        if len(id_positions) == 1 and len(text_positions) == 2:
            id_index = id_positions[0]
            text_indexes = [idx for idx in text_positions if idx != id_index]
            # 保持原有先后顺序：前一段文本作姓名候选，后一段作作业名候选。
            text_indexes.sort()
            first_text = segments[text_indexes[0]]
            second_text = segments[text_indexes[1]]

            fields, rule, why = _resolve_two_texts(first_text, second_text)
            student_name, title = fields[1], fields[2]
            student_id = segments[id_index]

            return (
                (student_id, student_name, title),
                RULE_TRIPLE,
                f"数字段「{student_id}」判为学号；{why}（规则同「两段文本」）",
            )

    # 规则四：段数既不是两段也不是可识别的三段，无法在"一段学号 + 一段或两段
    # 文本"的模型下拆解。
    if len(segments) != 2:
        joined = "、".join(f"「{seg}」" for seg in segments)
        return None, RULE_AMBIGUOUS, f"共 {len(segments)} 段（{joined}），不符合识别规则"

    first, second = segments
    first_kind = _segment_kind(first)
    second_kind = _segment_kind(second)

    # 规则二：一段数字 + 一段文本。数字段作学号，文本段直接作作业名。
    # 需求明确要求"含一份数字信息与一份文本信息时直接将这一份文本信息识别为
    # 作业名"，因此这里不校验文本段是否真的像作业名——即便它写的是姓名
    # （如「20230001_张三」）也照此办理。
    if first_kind == FieldKind.ID.value and _is_text_like(second_kind):
        return (
            (first, "", second),
            RULE_ID_TEXT,
            f"数字段「{first}」判为学号，文本段「{second}」判为作业名",
        )

    if second_kind == FieldKind.ID.value and _is_text_like(first_kind):
        return (
            (second, "", first),
            RULE_ID_TEXT,
            f"数字段「{second}」判为学号，文本段「{first}」判为作业名",
        )

    # 规则三：两段都是文本。
    if _is_text_like(first_kind) and _is_text_like(second_kind):
        return _resolve_two_texts(first, second)

    # 其余组合：两段都是数字，无法判断哪个是学号、也确定不了作业名。
    joined = f"「{first}」（{_kind_label(first_kind)}）+「{second}」（{_kind_label(second_kind)}）"
    return None, RULE_AMBIGUOUS, f"两段 {joined} 无法拆分为学号与作业名，不予识别"


def _resolve_two_texts(
    first: str,
    second: str,
) -> tuple[tuple[str, str, str], str, str]:
    """在两段文本中判定哪一段是作业名，另一段作姓名。

    该逻辑被「两段文本」与「三段：数字 + 两段文本」两处复用，因此单独抽出，
    确保两种输入下的作业名判定口径完全一致——用户不会遇到「同样两段文本，
    在两种文件名结构里判出不同作业名」的困惑。

    判定顺序（与需求描述逐条对应）：

    1. 两段都含作业特征词 → 取**后置段**为作业名；
    2. 只有一段含特征词 → 该段为作业名；
    3. 两段都不含特征词 → 同样取**后置段**为作业名。

    三种情况中未当选作业名的那一段一律作姓名。

    Args:
        first: 前置文本段。
        second: 后置文本段。

    Returns:
        三元组 ``(字段, 规则名, 说明)``，字段为 ``(学号, 姓名, 作业名)``，
        学号位置固定为空字符串（调用方负责补学号）。
    """
    first_has_word = _contains_title_word(first)
    second_has_word = _contains_title_word(second)

    # 两段都含特征词：取后置段作作业名。
    if first_has_word and second_has_word:
        return (
            ("", first, second),
            RULE_TITLE_TRAILING,
            f"两段均含作业名特征词，取后置段「{second}」为作业名，"
            f"前段「{first}」作姓名",
        )

    # 只有一段含特征词：该段作作业名，另一段作姓名。
    if first_has_word != second_has_word:
        title, name = (first, second) if first_has_word else (second, first)
        return (
            ("", name, title),
            RULE_TITLE_KEYWORD,
            f"「{title}」命中作业名特征词，判为作业名；「{name}」作姓名",
        )

    # 两段都不含特征词：同样取后置段作作业名，前段作姓名。
    return (
        ("", first, second),
        RULE_TITLE_TRAILING,
        f"两段均无作业名特征词，后置段「{second}」判为作业名，前段「{first}」作姓名",
    )


def _kind_label(kind: str) -> str:
    """把内部的内容类型代号翻译成中文，用于界面说明。

    Args:
        kind: 内容类型代号。

    Returns:
        中文标签。
    """
    labels = {
        FieldKind.ID.value: "数字",
        FieldKind.TITLE.value: "含作业字样",
        "text": "文本",
    }
    return labels.get(kind, kind)


def _split_suffix(name: str) -> tuple[str, str]:
    """拆出文件名主体与扩展名。

    ``Path.stem`` 对 ``a.tar.gz`` 只会剥掉 ``.gz``，与多数用户的直觉一致，
    因此这里沿用相同规则。

    Args:
        name: 文件名，不含目录部分。

    Returns:
        二元组 ``(主体, 扩展名)``，扩展名含前导点号；无扩展名时为空字符串。
    """
    suffix = Path(name).suffix
    stem = name[: len(name) - len(suffix)] if suffix else name
    return stem, suffix


def _build_parse_pattern(source_format: Sequence[str]) -> re.Pattern[str]:
    """依据原格式构造解析用正则。

    各段统一使用"非下划线片段"作最小切分单位，随后由 :func:`_field_is_plausible`
    判断该段是否配得上声明的语义。之所以不在正则里直接限制 ``id`` 必须是数字，
    是为了让「声明是学号、实际却写着作业名」的文件能够被解析出来、进而标红
    提示用户；若直接在正则阶段排除，这类文件会退化为普通的"跳过"，用户就
    看不到标红警告了。

    各段的贪婪策略：

    * ``title`` 是最后一段时贪婪匹配到行尾，允许包含下划线（如「第一次_实验报告」）；
    * ``title`` 之后还有别的段时改为非贪婪，把尾部让给后续段；
    * 其余段一律非贪婪，取到下一个下划线为止。

    Args:
        source_format: 字段顺序，元素为 :class:`FieldKind` 的值。

    Returns:
        用于 ``fullmatch`` 的正则对象。
    """
    kinds = list(source_format)

    # title 之后是否还有其它段，决定它的贪婪性。
    title_index = (
        kinds.index(FieldKind.TITLE.value)
        if FieldKind.TITLE.value in kinds
        else -1
    )
    title_is_last = title_index == len(kinds) - 1

    pieces: list[str] = []
    for position, kind in enumerate(kinds):
        if kind == FieldKind.TITLE.value:
            token = ".+" if title_is_last else ".+?"
        else:
            token = "[^_]+?"
        pieces.append(f"(?P<{kind}_{position}>{token})")

    return re.compile("_".join(pieces))


def _format_to_tokens(source_format: Sequence[str]) -> str:
    """把字段顺序翻译成人类可读的描述，用于提示文案。

    Args:
        source_format: 字段顺序，或 :data:`AUTO_SOURCE_FORMAT`。

    Returns:
        形如 ``学号_姓名_作业名`` 的描述文本；自动识别时返回 ``自动识别``。
    """
    if tuple(source_format) == AUTO_SOURCE_FORMAT:
        return "自动识别"

    labels = {FieldKind.ID.value: "学号", FieldKind.NAME.value: "姓名",
              FieldKind.TITLE.value: "作业名", "extra": "附加段"}
    return "_".join(labels.get(kind, kind) for kind in source_format)


def parse_structured_name(
    name: str,
    source_format: Sequence[str] = DEFAULT_SOURCE_FORMAT,
    roster: Roster | None = None,
    preferred_titles: Sequence[str] | None = None,
) -> tuple[str, str, str, str, str, str] | None:
    """按指定原格式解析文件名。

    有两条路径：

    **自动识别**（``source_format`` 为 :data:`AUTO_SOURCE_FORMAT`）
        不看固定字段顺序，而是按段数与各段内容类型套用规则表，见
        :func:`infer_fields_from_segments`。推荐用法，能同时应付
        「20230001_张三_第一次作业」「张三_第一次作业」「20230001_张三」
        等多种写法。

    **固定排列**（其余取值）
        用正则按 ``source_format`` 逐段切分，再校验 ``id`` / ``name`` 段的
        内容是否配得上其声明的语义。之所以不在正则阶段就限制 ``id`` 必须是
        数字，是为了让「声明是学号、实际却写着作业名」的文件能够被解析出来、
        进而标红提示用户；若在正则阶段直接排除，这类文件会退化为普通的
        "跳过"，用户就看不到标红警告了。

    Args:
        name: 文件名，不含目录部分。
        source_format: 原文件名的字段排列，或 :data:`AUTO_SOURCE_FORMAT`。
        roster: 学生信息表。提供时用于补全缺失的学号或姓名；为 ``None``
            则不补全。
        preferred_titles: 作业白名单。**仅在自动识别路径下生效**：与表中某条
            完全一致的段会被优先认定为作业名。固定排列下作业名由声明的字段
            位置决定，无需此参数。

    Returns:
        六元组 ``(学号, 姓名, 作业名, 扩展名, 说明, 问题)``；无法解析时返回
        ``None``。

        * 说明 —— 字段补全记录，或自动识别命中的规则解释（如
          ``数字段「20230001」判为学号，文本段「张三」判为作业名``）。
        * 问题 —— 空字符串表示正常；非空表示该文件**判为不合格应标红**，
          内容为原因说明（如 ``仅一段「张三」，缺少学号或姓名``）。
    """
    stem, suffix = _split_suffix(name)
    if not stem:
        return None

    if tuple(source_format) == AUTO_SOURCE_FORMAT:
        return _parse_by_inference(stem, suffix, roster, preferred_titles)

    pattern = _build_parse_pattern(source_format)
    match = pattern.fullmatch(stem)
    if match is None:
        return None

    groups = match.groupdict()
    parsed_id = ""
    parsed_name = ""
    title = ""
    raw_id = ""
    raw_name = ""
    semantic_issues: list[str] = []

    for position, kind in enumerate(source_format):
        value = groups.get(f"{kind}_{position}")
        if value is None:
            continue

        if kind == FieldKind.ID.value:
            raw_id = value
            parsed_id = value
            if not looks_like_id(value) and value.strip():
                semantic_issues.append(f"「{value}」不像学号")
        elif kind == FieldKind.NAME.value:
            raw_name = value
            parsed_name = value
            if not looks_like_name(value) and value.strip():
                semantic_issues.append(f"「{value}」不像姓名")
        elif kind == FieldKind.TITLE.value:
            title = value

    title = title.strip()
    if not title:
        return None

    note = ""
    if roster is not None:
        # 语义存疑的段不参与补全：把「第一次作业」当学号去查信息表毫无意义，
        # 只会污染查询结果。此时保留原文本，由上层判为不合格。
        lookup_id = raw_id if not any("不像学号" in item for item in semantic_issues) else ""
        lookup_name = raw_name if not any("不像姓名" in item for item in semantic_issues) else ""
        filled_id, filled_name, note = roster.fill(lookup_id, lookup_name)

        # 只有当补全确实改善了信息时才采纳结果。若查表失败返回空值，而原文
        # 本有内容，则保留原文，避免把「20230099」这种未收录学号抹掉。
        parsed_id = filled_id or parsed_id
        parsed_name = filled_name or parsed_name

    problem = "；".join(semantic_issues)
    return parsed_id.strip(), parsed_name.strip(), title, suffix, note, problem


def _parse_by_inference(
    stem: str,
    suffix: str,
    roster: Roster | None,
    preferred_titles: Sequence[str] | None = None,
) -> tuple[str, str, str, str, str, str]:
    """按自动识别规则解析文件名主体。

    与固定排列的解析不同，本函数**永不返回 None**。原因是"不予识别"在自动
    识别模式下本身就是一条需要告知用户的判定结果，应当进入标红清单并在说明
    栏写明理由，而不是静默跳过——静默跳过会让用户以为文件与程序无关。

    推断出字段后仍走一遍信息表补全：例如「20230001_张三」被识别为学号
    ``20230001`` + 作业名 ``张三``、姓名为空，此时可用信息表把姓名补上。

    Args:
        stem: 文件名主体，不含扩展名。
        suffix: 扩展名，含前导点号。
        roster: 学生信息表，可为 ``None``。
        preferred_titles: 用户提供的作业白名单。非空时，与其中某条完全一致的
            段会被**优先认定为作业名**，不再走启发式判断。可为 ``None``。

    Returns:
        六元组，结构同 :func:`parse_structured_name`。
    """
    fields, rule, why = infer_fields_from_segments(stem, preferred_titles)

    if fields is None:
        # 不予通过 / 不予识别：问题栏写明理由，由上层标红。
        return "", "", "", suffix, "", why

    student_id, student_name, title = fields
    # 说明栏以规则名为前缀，界面上能直接看出"这条是按哪条规则判的"。
    note = f"[{rule}] {why}"

    if roster is not None and (not student_id or not student_name):
        filled_id, filled_name, fill_note = roster.fill(student_id, student_name)
        student_id = filled_id or student_id
        student_name = filled_name or student_name
        if fill_note:
            note = f"{note}；{fill_note}"

    return student_id, student_name, title, suffix, note, ""


def build_new_name(
    old_name: str,
    template: str = DEFAULT_TEMPLATE,
    source_format: Sequence[str] = DEFAULT_SOURCE_FORMAT,
    roster: Roster | None = None,
    preferred_titles: Sequence[str] | None = None,
) -> tuple[str, str, str, str, str, str] | None:
    """依据模板为指定文件名生成新名称。

    Args:
        old_name: 原文件名。
        template: 命名模板，支持 ``{id}``、``{name}``、``{title}`` 三个
            占位符。扩展名由本函数自动拼接，不需写入模板。
        source_format: 原文件名的字段排列。
        roster: 学生信息表，用于补全缺失字段。
        preferred_titles: 作业白名单，仅自动识别路径下生效，见
            :func:`parse_structured_name`。

    Returns:
        六元组 ``(新文件名, 学号, 姓名, 作业名, 说明, 问题)``；原文件名
        不符合指定排列、或模板占位符无效时返回 ``None``。

        注意：当 ``problem`` 非空（即该文件已判为不合格）时，新文件名位置为
        空字符串——因为字段没识别出来，模板渲染不出有意义的名字。上层应据
        ``problem`` 把它归入标红清单，而不是当成"模板无效"跳过。
    """
    parsed = parse_structured_name(old_name, source_format, roster, preferred_titles)
    if parsed is None:
        return None

    student_id, student_name, title, suffix, note, problem = parsed

    # 已判为不合格的文件：字段未识别出，无法渲染新名。此处原样返回，让上层
    # 依据 problem 标红，而不是在这里返回 None 被误当成"结构不符"跳过。
    if problem:
        return "", student_id, student_name, title, note, problem

    try:
        new_stem = template.format(id=student_id, name=student_name, title=title)
    except (KeyError, IndexError, ValueError):
        return None

    new_stem = _sanitize_stem(new_stem)
    if not new_stem:
        return None

    return new_stem + suffix, student_id, student_name, title, note, problem


def _sanitize_stem(stem: str) -> str:
    """清理文件名主体中的非法字符与冗余分隔符。

    作业名可能包含 ``/`` 等字符（如「第1/2章」），此类字符在 Windows 上作为
    目录分隔符使用，直接用作文件名会导致文件被写入非预期目录或操作失败。

    还需处理"占位符为空"留下的悬挂分隔符。例如模板 ``{title}_{id}`` 在学号
    补全失败时渲染出 ``第三次作业_``，若不去除尾随下划线，会得到
    ``第三次作业_.docx`` 这样难看的名字。连续下划线一并压成一条，避免
    ``作业名__学号`` 这类结果。

    Args:
        stem: 待清理的文件名主体。

    Returns:
        将非法字符替换为下划线、合并连续下划线、去除首尾空白与结尾点号
        后的结果。
    """
    cleaned = "".join("_" if char in _ILLEGAL_CHARS else char for char in stem)
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    cleaned = cleaned.strip()
    # 首尾的悬挂下划线来自空占位符，去掉；结尾点号在 Windows 上非法，一并去掉。
    cleaned = cleaned.strip("_")
    return cleaned.rstrip(".")


def validate_template(template: str) -> str | None:
    """校验命名模板是否可用。

    Args:
        template: 待校验的模板文本。

    Returns:
        校验通过时返回 ``None``，否则返回错误说明。
    """
    if not template.strip():
        return "模板为空"

    try:
        rendered = template.format(id="A", name="B", title="C")
    except (KeyError, IndexError, ValueError) as exc:
        return f"模板格式有误：{exc}"

    if not rendered.strip():
        return "模板渲染结果为空"

    # 模板必须至少含一个有效占位符，否则改名等于给所有文件起同一个名字。
    used = {key for key in _VALID_PLACEHOLDERS if f"{{{key}}}" in template}
    if not used:
        return "模板中至少需要包含一个占位符：{id}、{name} 或 {title}"
    if "{title}" not in template:
        # 不强制禁止，但作业名是区分文件的主要依据，缺了容易产生重名。
        return "提示：模板未包含 {title}，可能导致多个文件重名"

    return None


def calculate_rename_plan(
    records: Sequence,
    template: str = DEFAULT_TEMPLATE,
    source_format: Sequence[str] = DEFAULT_SOURCE_FORMAT,
    roster: Roster | None = None,
    preferred_titles: Sequence[str] | None = None,
) -> RenamePlan:
    """计算改名计划。

    本函数不修改任何文件，仅进行字符串运算，可安全重复调用以生成预览。

    冲突判定逻辑：以 ``occupied`` 集合记录改名后会被占用的文件名，初始包含
    磁盘上现存的全部文件名，每批准一条改名则将新名加入、旧名移出。初始集合
    必须包含"本批次中不参与改名的文件"——否则当 A 改名为 B、而 B 本身不参与
    改名时，程序会误判 B 为可用名并覆盖原文件。

    三类文件的去向：

    * **不合格** → :attr:`RenamePlan.invalid`，界面标红，不参与改名。包含：
      缺少学号与姓名；或自动识别模式下的"不予识别"（段数/内容不符规则）；
      或固定排列下声明为学号/姓名的段内容语义不符；
    * 不符合指定原格式（正则未匹配）或模板无效 → :attr:`RenamePlan.skipped`；
    * 其余 → 进入 :attr:`RenamePlan.actions`，逐条判定冲突。

    Args:
        records: 扫描记录序列，需具有 ``path`` 与 ``name`` 属性。
        template: 命名模板。
        source_format: 原文件名的字段排列，或 :data:`AUTO_SOURCE_FORMAT`。
        roster: 学生信息表，用于补全缺失字段。
        preferred_titles: 作业白名单。提供时，与表中某条完全一致的段会被
            优先认定为作业名（仅自动识别路径生效）。可为 ``None``。

    Returns:
        包含待执行动作、被跳过项与不合格项的 :class:`RenamePlan`。
    """
    existing: dict[str, Path] = {
        record.name.lower(): record.path for record in records
    }

    plan = RenamePlan()
    candidates: list[tuple[Path, str, str, str, str, str]] = []

    format_label = _format_to_tokens(source_format)

    for record in records:
        built = build_new_name(
            record.name, template, source_format, roster, preferred_titles
        )
        if built is None:
            plan.skipped.append(
                (record.path, f"文件名不符合「{format_label}」结构，或模板占位符无效")
            )
            continue

        new_name, student_id, student_name, _title, note, problem = built

        # 强制要求：学号与姓名至少有一个，且判定的内容必须站得住脚。
        # 判定分两种情况：
        #   1. 语义/结构问题 —— 自动识别判定"不予通过/不予识别"，或固定排列下
        #      声明为学号姓名的段内容不符。此时只报问题，不再叠加"两项皆空"
        #      的通用原因，否则同一件事会被说两遍。
        #   2. 两项补全后仍为空 —— 文件名与信息表都提供不了学号姓名。
        reasons: list[str] = []
        if problem:
            reasons.append(problem)
        elif not student_id and not student_name:
            # 字段都空但没问题说明：只有信息表补全失败一种可能。
            # note 在自动识别模式下带 "[规则名]" 前缀，读起来不像原因，
            # 因此这里给出固定的通用说明，并把 note 作为附注。
            base = "文件名中既无学号也无姓名（信息表亦无法补全）"
            reasons.append(f"{base}｜{note}" if note else base)

        if reasons:
            plan.invalid.append((record.path, "；".join(reasons)))
            continue

        candidates.append(
            (record.path, record.name, new_name, student_id, student_name, note)
        )

    # 初始占用集合包含全部现存文件名。此策略偏保守：不排除参与改名的源名，
    # 代价是 A→B 且 B→A 的互换场景会被判为冲突而跳过，收益是判定结果与处理
    # 顺序无关，且任何情况下都不会发生覆盖。
    occupied: set[str] = set(existing.keys())

    for path, old_name, new_name, student_id, student_name, note in candidates:
        new_key = new_name.lower()
        old_key = old_name.lower()

        if new_key == old_key:
            plan.actions.append(
                RenameAction(
                    source=path,
                    old_name=old_name,
                    new_name=new_name,
                    changed=False,
                    parsed_id=student_id,
                    parsed_name=student_name,
                    fill_note=note,
                )
            )
            continue

        if new_key in occupied:
            occupier = existing.get(new_key)
            if occupier is not None:
                reason = (
                    f"目标名「{new_name}」已被占用（{occupier.name}），"
                    "为避免覆盖已跳过"
                )
            else:
                reason = f"目标名「{new_name}」与本批次中另一个文件的目标名重复，已跳过"
            plan.skipped.append((path, reason))
            continue

        plan.actions.append(
            RenameAction(
                source=path,
                old_name=old_name,
                new_name=new_name,
                changed=True,
                parsed_id=student_id,
                parsed_name=student_name,
                fill_note=note,
            )
        )

        # 将新名标记为已占用，防止同批次内其他文件计算出相同新名。
        occupied.add(new_key)
        # 旧名随本文件改名而释放。
        occupied.discard(old_key)

    accounted = len(plan.actions) + len(plan.skipped) + len(plan.invalid)
    assert accounted >= len(candidates), (
        f"内部错误：候选 {len(candidates)} 条，但仅登记 {accounted} 条"
    )

    return plan


def apply_rename_plan(
    plan: RenamePlan,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[RenameAction], list[tuple[Path, str]]]:
    """执行改名计划，会修改磁盘上的文件名。

    仅处理 ``plan.pending`` 中的动作；每个文件独立捕获异常，避免单个失败
    中断整批处理。执行前会再次检查目标名是否已被占用，以覆盖计划生成后
    磁盘状态发生变化的情况。

    Args:
        plan: 由 :func:`calculate_rename_plan` 生成的计划。
        progress: 进度回调，签名为 ``(当前序号, 总数, 说明文本)``。

    Returns:
        二元组 ``(成功列表, 失败列表)``，失败项为 ``(路径, 失败原因)``。
    """
    succeeded: list[RenameAction] = []
    failed: list[tuple[Path, str]] = []

    actions = plan.pending
    total = len(actions)

    for index, action in enumerate(actions, start=1):
        if progress is not None:
            progress(index, total, f"{action.old_name} → {action.new_name}")

        # 运行时二次校验，是保证"绝不覆盖"的最后一道检查。
        target = action.new_path
        if target.exists():
            failed.append((action.source, f"目标已存在，已跳过：{action.new_name}"))
            continue

        try:
            # 同目录内改名属于原子操作，不会产生中间状态。
            action.source.rename(target)
            succeeded.append(action)
        except OSError as exc:
            failed.append((action.source, f"{type(exc).__name__}: {exc}"))

    return succeeded, failed


def format_plan(plan: RenamePlan) -> str:
    """将改名计划渲染为可读的预览文本。

    输出分为待改名、不合格、被跳过、汇总四个区块，作为用户确认操作的依据。

    Args:
        plan: 待渲染的改名计划。

    Returns:
        多行预览文本。
    """
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("【将要改名的文件】")
    lines.append("=" * 70)
    if plan.pending:
        for i, action in enumerate(plan.pending, start=1):
            lines.append(f"{i:>3}. {action.old_name}")
            lines.append(f"     → {action.new_name}")
    else:
        lines.append("（无）")

    lines.append("")
    lines.append("=" * 70)
    lines.append("【不合格：文件名中既无学号也无姓名，无法改名】")
    lines.append("=" * 70)
    if plan.invalid:
        for i, (path, reason) in enumerate(plan.invalid, start=1):
            lines.append(f"{i:>3}. {path.name}")
            lines.append(f"     原因：{reason}")
    else:
        lines.append("（无）")

    lines.append("")
    lines.append("=" * 70)
    lines.append("【将被跳过（不会被修改）】")
    lines.append("=" * 70)
    if plan.skipped:
        for i, (path, reason) in enumerate(plan.skipped, start=1):
            lines.append(f"{i:>3}. {path.name}")
            lines.append(f"     原因：{reason}")
    else:
        lines.append("（无）")

    lines.append("")
    lines.append("=" * 70)
    lines.append(
        f"汇总：将改名 {len(plan.pending)} 个 | "
        f"不合格 {len(plan.invalid)} 个 | "
        f"跳过 {len(plan.skipped)} 个 | "
        f"无需改动 {plan.unchanged_count} 个"
    )
    lines.append("=" * 70)

    return "\n".join(lines)
