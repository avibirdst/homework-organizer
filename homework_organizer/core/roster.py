"""学生信息表：学号与姓名的对照关系。

教师手上的作业文件常常只带学号或只带姓名。若有一份「学号 ↔ 姓名」对照表，
就能把缺失的一项补全，从而让 :mod:`homework_organizer.core.rename` 生成更
完整的文件名。

信息表来源有两种，二者可以混用：

    1. 用户在界面文本框中直接粘贴、逐行输入；
    2. 用户选择一个 ``.txt`` 文件，程序读取其内容。

两种来源最终都被规整为同一份文本再解析，因此解析规则完全一致。

**格式约定**（足够宽松，避免"格式不对就罢工"）：

* 每行一条记录，学号与姓名之间用逗号、制表符、空格或冒号分隔；
* 空行、以 ``#`` 或 ``//`` 开头的注释行会被忽略；
* 表头行会被自动识别并跳过，例如 ``学号,姓名``、``id,name``；
* 写成 ``学号=20230001 姓名=张三`` 这类带键名的形式也能解析。

解析结果存于 :class:`Roster`，它对"查不到"一律返回 ``None`` 而不抛异常——
文件名里出现信息表未收录的学号属于正常情况，不应中断整批处理。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "RosterEntry",
    "Roster",
    "RosterError",
    "SAMPLE_ROSTER_TEXT",
    "parse_roster_text",
    "load_roster_file",
]


class RosterError(Exception):
    """读取信息表文件失败。

    仅在文件层面出错时抛出（路径不存在、编码无法识别等）。解析层面不做
    严格校验，跳过分隔符不合规的行即可。
    """


@dataclass
class RosterEntry:
    """信息表中的一条记录。"""

    student_id: str
    """学号。"""

    student_name: str
    """姓名。"""


#: 举例用的信息表内容。界面上「示例」按钮与「导出示例」功能均使用此常量，
#: 保证用户看到的示例与实际支持的格式始终一致。
SAMPLE_ROSTER_TEXT = """\
# 学生信息表 —— 学号与姓名对照
# 每行一条记录，学号在前、姓名在后，中间用逗号或空格分隔。
# 以 # 开头的行是注释，程序会自动忽略；下面这两行表头也会被自动跳过。
学号,姓名
20230001,张三
20230002 李四
20230003，王五
20230004:赵六
"""

# 每条记录允许的分隔符：英文/中文逗号、制表符、冒号、全角冒号、空白。
_SEPARATORS = ",，\t:："

# 表头行的关键词。只要某一侧命中其中之一即认为该行是表头。
_ID_HEADER_WORDS = frozenset({"学号", "学籍号", "id", "sid", "no", "number", "studentid"})
_NAME_HEADER_WORDS = frozenset({"姓名", "名字", "名字", "name", "studentname", "学生姓名"})

# 带键名的写法，如「学号=20230001 姓名=张三」。
_KEY_PATTERN_ID = ("学号", "id", "sid", "no")
_KEY_PATTERN_NAME = ("姓名", "名字", "name")


@dataclass
class Roster:
    """学号与姓名的双向对照表。

    内部维护两张字典以实现 O(1) 双向查询。构建时会忽略重复项：先出现的
    记录生效，后出现的同键记录被丢弃并在 :attr:`conflicts` 中留痕。
    """

    entries: list[RosterEntry] = field(default_factory=list)
    """按录入顺序保存的全部有效记录。"""

    _by_id: dict[str, str] = field(default_factory=dict, repr=False)
    """学号 → 姓名。"""

    _by_name: dict[str, str] = field(default_factory=dict, repr=False)
    """姓名 → 学号。"""

    conflicts: list[str] = field(default_factory=list)
    """重复或自相矛盾的行说明，供界面提示使用。"""

    def __len__(self) -> int:
        """返回信息表中的记录条数。

        Returns:
            有效记录数量。
        """
        return len(self.entries)

    def __bool__(self) -> bool:
        """判断信息表是否含有效记录。

        Returns:
            至少有一条记录时为 ``True``。
        """
        return bool(self.entries)

    def name_of(self, student_id: str) -> str | None:
        """按学号查询姓名。

        Args:
            student_id: 学号，忽略首尾空白与大小写差异。

        Returns:
            对应姓名；未收录时返回 ``None``。
        """
        return self._by_id.get(student_id.strip().upper())

    def id_of(self, student_name: str) -> str | None:
        """按姓名查询学号。

        Args:
            student_name: 姓名，忽略首尾空白。

        Returns:
            对应学号；未收录时返回 ``None``。
        """
        return self._by_name.get(student_name.strip())

    def complete_id(self, student_id: str) -> tuple[str, str, str]:
        """以学号为已知项，尝试补全姓名。

        Args:
            student_id: 已知学号。

        Returns:
            三元组 ``(学号, 姓名, 说明)``。学号为空白时原样返回；查不到姓名
            时姓名位置为空白字符串，说明文本形如 ``无``。
        """
        student_id = student_id.strip()
        if not student_id:
            return "", "", "无学号"
        student_name = self.name_of(student_id)
        if student_name is None:
            return student_id, "", f"{student_id} 不在信息表中"
        return student_id, student_name, f"{student_id} → {student_name}"

    def complete_name(self, student_name: str) -> tuple[str, str, str]:
        """以姓名为已知项，尝试补全学号。

        Args:
            student_name: 已知姓名。

        Returns:
            三元组 ``(学号, 姓名, 说明)``。姓名为空白时原样返回；查不到学号
            时学号位置为空白字符串。
        """
        student_name = student_name.strip()
        if not student_name:
            return "", "", "无姓名"
        student_id = self.id_of(student_name)
        if student_id is None:
            return "", student_name, f"{student_name} 不在信息表中"
        return student_id, student_name, f"{student_name} → {student_id}"

    def fill(self, student_id: str, student_name: str) -> tuple[str, str, str]:
        """对残缺的学号/姓名做双向补全。

        这是需求二扩写的统一入口：调用方不必关心文件名里原本给的是哪一项，
        交给本方法即可。补全优先级为

            1. 两项都有 → 原样返回；
            2. 只有学号 → 查姓名；
            3. 只有姓名 → 查学号；
            4. 两项都空 → 返回空值与提示，由调用方决定是否标红。

        Args:
            student_id: 从文件名解析出的学号，可能为空白。
            student_name: 从文件名解析出的姓名，可能为空白。

        Returns:
            三元组 ``(补全后的学号, 补全后的姓名, 说明)``。第 3 项是给界面
            显示的一句话解释，正常补全时为 ``学号 → 姓名`` 形式。
        """
        student_id = student_id.strip()
        student_name = student_name.strip()

        if student_id and student_name:
            return student_id, student_name, ""
        if student_id:
            return self.complete_id(student_id)
        if student_name:
            return self.complete_name(student_name)
        return "", "", "文件名中既无学号也无姓名"


def parse_roster_text(text: str) -> Roster:
    """把信息表文本解析为 :class:`Roster`。

    本函数对格式相当宽容：任何无法识别为"学号 + 姓名"的行都被静默跳过，
    而不是报错。理由是信息表常由用户手工从 Excel 或聊天记录里复制，残留
    的标题行、空行很常见，让用户去逐行清理并不友好。

    Args:
        text: 信息表原始文本，可含多行。

    Returns:
        解析出的 :class:`Roster`；输入为空或无有效行时返回空表。
    """
    roster = Roster()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        pair = _split_pair(line)
        if pair is None:
            continue

        student_id, student_name = pair
        if _is_header(student_id, student_name):
            continue

        id_key = student_id.upper()
        if id_key in roster._by_id:
            if roster._by_id[id_key] != student_name:
                roster.conflicts.append(
                    f"学号 {student_id} 重复：已记录为「{roster._by_id[id_key]}」，"
                    f"又出现「{student_name}」，以后者舍弃"
                )
            continue

        if student_name in roster._by_name:
            roster.conflicts.append(
                f"姓名 {student_name} 重复：已记录学号「{roster._by_name[student_name]}」，"
                f"又出现「{student_id}」，以后者舍弃"
            )
            continue

        roster.entries.append(
            RosterEntry(student_id=student_id, student_name=student_name)
        )
        roster._by_id[id_key] = student_name
        roster._by_name[student_name] = student_id

    return roster


def _split_pair(line: str) -> tuple[str, str] | None:
    """把一行文本拆成 ``(学号, 姓名)``。

    依次尝试三种写法：带键名、常规分隔符、纯空白。

    Args:
        line: 已去除首尾空白的一行文本。

    Returns:
        二元组；无法拆出两项时返回 ``None``。
    """
    keyed = _split_keyed(line)
    if keyed is not None:
        return keyed

    # 先按显式分隔符切分；切出的第一段若仍是「学号 姓名」这种空格分隔，
    # 再按空白切一次。
    normalized = line
    for sep in _SEPARATORS:
        normalized = normalized.replace(sep, ",")

    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]

    parts = line.split()
    if len(parts) >= 2:
        return parts[0], parts[1]

    return None


def _split_keyed(line: str) -> tuple[str, str] | None:
    """尝试解析 ``学号=20230001 姓名=张三`` 这类带键名的写法。

    Args:
        line: 已去除首尾空白的一行文本。

    Returns:
        二元组；该行不含键名时返回 ``None``。
    """
    tokenized = line.replace("=", " ").replace("：", " ").replace(":", " ")
    for sep in _SEPARATORS:
        tokenized = tokenized.replace(sep, " ")

    tokens = [token for token in tokenized.split() if token]
    if len(tokens) < 2:
        return None

    found_id: str | None = None
    found_name: str | None = None

    for index, token in enumerate(tokens):
        lowered = token.lower()
        has_next = index + 1 < len(tokens)
        if found_id is None and lowered in _KEY_PATTERN_ID and has_next:
            found_id = tokens[index + 1]
        elif found_name is None and lowered in _KEY_PATTERN_NAME and has_next:
            found_name = tokens[index + 1]

    if found_id is not None and found_name is not None:
        return found_id, found_name
    return None


def _is_header(first: str, second: str) -> bool:
    """判断一行是否为表头。

    Args:
        first: 拆分后的第一项。
        second: 拆分后的第二项。

    Returns:
        任一项命中表头关键词时为 ``True``。
    """
    first_key = first.strip().lower()
    second_key = second.strip().lower()
    return (
        first_key in _ID_HEADER_WORDS
        or first_key in _NAME_HEADER_WORDS
        or second_key in _ID_HEADER_WORDS
        or second_key in _NAME_HEADER_WORDS
    )


def load_roster_file(path: str | Path) -> Roster:
    """从文本文件读取并解析信息表。

    Args:
        path: ``.txt`` 或任意纯文本文件的路径。

    Returns:
        解析出的 :class:`Roster`。

    Raises:
        RosterError: 文件不存在、不是文件，或无法以常见编码读取。
    """
    file_path = Path(path)
    if not file_path.exists():
        raise RosterError(f"信息表文件不存在：{file_path}")
    if not file_path.is_file():
        raise RosterError(f"信息表路径不是文件：{file_path}")

    # utf-8-sig 可自动剥离 Windows 记事本保存 UTF-8 时写入的 BOM，
    # 避免首行「学号」被读成「\ufeff学号」而使表头识别失效。
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = file_path.read_text(encoding=encoding)
            return parse_roster_text(text)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            raise RosterError(f"读取信息表失败：{exc}") from exc

    raise RosterError(f"无法识别信息表文件编码（已尝试 utf-8 与 gbk）：{file_path}")
