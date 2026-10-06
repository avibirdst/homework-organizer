"""图形界面层，基于 PySide6 实现。

界面采用 ``QTabWidget`` 组织为四个标签页，一个功能模块对应一页：

    +-----------------------------------------------------+
    |  目录选择、扩展名过滤、字数阈值           [ 扫描 ]   |  顶部公共区
    +-----------------------------------------------------+
    |  需求1 扫描列出 | 需求2 批量改名 | 需求3 归档报告 | 作业检查  |
    +-----------------------------------------------------+
    |                                                     |
    |              各标签页内容                            |
    |                                                     |
    +-----------------------------------------------------+
    |  进度条                                      状态文字 |  底部状态区
    +-----------------------------------------------------+

界面搭建代码集中在 ``_build_*`` 系列方法中，事件处理集中在 ``_on_*`` 系列
方法中，两部分互不交叉，便于分别维护。本层仅负责将用户输入转换为对
``core`` 层的调用，并按结果更新界面，不包含业务逻辑。

"扫描"按钮置于顶部公共区而非各标签页内：四个功能均基于同一份扫描结果，
扫描一次供四个功能共用，可避免重复扫描与数据不一致。
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# 导入我们自己的核心逻辑层。
# 注意：core 里的模块完全不认识 Qt，它们只做纯逻辑。
# 界面的职责就是「把用户输入翻译成对 core 的调用，再把结果画出来」。
from homework_organizer.core import archive as archive_core
from homework_organizer.core import rename as rename_core
from homework_organizer.core import roster as roster_core
from homework_organizer.core import scan as scan_core
from homework_organizer.core import wordcount as wordcount_core


# 表格行配色。集中定义便于统一调整，避免颜色值散落在各处。
#
# 注意：背景色与文字色必须成对设置。仅设置背景色时，若系统切换为深色主题，
# 深色文字与深色背景将无法辨认。

#: 低于字数的行底色；也用于「缺学号姓名」的标红。
COLOR_BELOW_THRESHOLD_BG = QColor("#ffe0e0")
#: 低于字数的行文字色；与 :data:`COLOR_BELOW_THRESHOLD_BG` 成对使用。
COLOR_BELOW_THRESHOLD_FG = QColor("#c00000")
#: 达标的行底色。
COLOR_PASSED_BG = QColor("#e8f5e9")
#: 达标的行文字色；与 :data:`COLOR_PASSED_BG` 成对使用。
COLOR_PASSED_FG = QColor("#1b5e20")
#: 统计失败的行底色（读不出来 ≠ 没写够，故与标红区分开）。
COLOR_ERROR_BG = QColor("#fff8e1")
#: 统计失败的行文字色；与 :data:`COLOR_ERROR_BG` 成对使用。
COLOR_ERROR_FG = QColor("#8d6e00")


class CollapsibleHint(QWidget):
    """一段默认折叠的说明文字，点标题行才展开。

    需求二顶部的识别规则较长，常驻显示会挤占表格的可视高度。改为默认只露一行
    概要，想看细则时点一下展开——既保留「界面上有标示」这一要求，又不牺牲
    常用区域的空间。

    Attributes:
        toggle_btn: 标题行按钮，点击切换展开/收起。
        body: 承载完整说明文字的容器，收起时隐藏。
    """

    def __init__(self, summary: str, detail: str, parent=None) -> None:
        """构造可折叠说明块。

        Args:
            summary: 收起时显示在标题行上的概要文字。
            detail: 展开后显示的完整说明文字。
            parent: 父控件。
        """
        super().__init__(parent)

        layout = QVBoxLayout(self)
        # 该控件只是说明文字的容器，内边距压到最小以免白占空间。
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # 箭头在收起/展开之间切换，用最朴素的字符避免依赖图标资源。
        self.toggle_btn = QPushButton(f"▶ {summary}")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        # 左对齐、无边框，视觉上像一行可点的说明文字而非按钮。
        self.toggle_btn.setStyleSheet(
            "QPushButton { text-align: left; border: none; padding: 2px 0; "
            "color: #1565c0; font-weight: bold; background: transparent; }"
            "QPushButton:hover { color: #0d47a1; text-decoration: underline; }"
        )
        self.toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self.toggle_btn)

        self.body = QLabel(detail)
        self.body.setWordWrap(True)
        # 正文略微缩进，体现它从属于上面的标题行。
        self.body.setStyleSheet("color: #444; font-size: 12px; padding-left: 14px;")
        self.body.setVisible(False)
        layout.addWidget(self.body)

    def _on_toggle(self) -> None:
        """切换展开/收起状态，并同步箭头方向。"""
        expanded = self.toggle_btn.isChecked()
        self.body.setVisible(expanded)
        text = self.toggle_btn.text()
        # 只替换开头的箭头，保留后面的概要文字。
        self.toggle_btn.setText(("▼ " if expanded else "▶ ") + text[2:])


class TextZoomDialog(QDialog):
    """把一段文本放大到整屏显示的浮动窗口。

    「信息表」与「操作输出」两块文本区在常规布局中占据了大面积，挤压了表格
    的可视高度。解决方案不是删掉它们，而是给它们加一个「放大」按钮：需要
    阅读或编辑时弹出一个近乎全屏的窗口，看完关闭即可，主界面始终保持紧凑。

    Attributes:
        edit: 用于展示文本的编辑控件。传入的文本若为只读，这里同样只读，
            保证对话框不会意外修改源控件的内容。
    """

    def __init__(self, title: str, text: str, read_only: bool, parent=None) -> None:
        """构造放大窗口。

        Args:
            title: 窗口标题，用于说明放大的是哪一块内容。
            text: 待显示的初始文本。
            read_only: 是否只读。只读时用户仅能选择复制，不能编辑。
            parent: 父窗口，用于让对话框居中于主窗口之上。
        """
        super().__init__(parent)

        self.setWindowTitle(title)
        # 记住父窗口引用，关闭时把编辑结果回写给源控件。
        self._source = None

        layout = QVBoxLayout(self)

        self.edit = QPlainTextEdit()
        self.edit.setPlainText(text)
        self.edit.setReadOnly(read_only)
        # 放大窗口用等宽字体，长报告的对齐结构才不会被破坏。
        self.edit.setFont(QFont("Consolas", 11))
        layout.addWidget(self.edit)

        layout.addLayout(_build_close_row(self))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 固定命名)
        """关闭窗口前把编辑结果回写到源控件。

        这样可编辑的文本框（如信息表）在放大状态下做的修改不会丢失。

        Args:
            event: Qt 传入的关闭事件，调用 ``accept()`` 才会真正关闭。
        """
        if self._source is not None and not self.edit.isReadOnly():
            self._source.setPlainText(self.edit.toPlainText())
        super().closeEvent(event)


class TableZoomDialog(QDialog):
    """把一张表格放大到整屏显示的浮动窗口。

    与文本放大窗口同理：主界面里表格能露出的行数有限，想通览全部行时点
    「放大」即可整屏查看。表格内容以**只读副本**呈现，用户在放大窗口里
    不能改动单元格，避免与主界面的数据源脱节。

    Attributes:
        table: 放大后的表格控件，已按来源表格完整复制行、列与配色。
    """

    def __init__(self, title: str, source_table: QTableWidget, parent=None) -> None:
        """构造表格放大窗口。

        Args:
            title: 窗口标题。
            source_table: 需要被放大的来源表格，其内容会被逐格复制。
            parent: 父窗口，用于让对话框居中于主窗口之上。
        """
        super().__init__(parent)

        self.setWindowTitle(title)

        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self._copy_table(source_table)
        layout.addWidget(self.table)

        layout.addLayout(_build_close_row(self))

    def _copy_table(self, source: QTableWidget) -> None:
        """把来源表格的行列、表头、内容与配色复制到放大表格中。

        Args:
            source: 来源表格控件。
        """
        rows = source.rowCount()
        cols = source.columnCount()

        self.table.setColumnCount(cols)
        self.table.setRowCount(rows)

        # 复制表头文字，列宽策略与来源保持一致，视觉上才像是「同一个表变大了」。
        headers = [
            (source.horizontalHeaderItem(i).text()
             if source.horizontalHeaderItem(i) else "")
            for i in range(cols)
        ]
        self.table.setHorizontalHeaderLabels(headers)

        header = self.table.horizontalHeader()
        for i in range(cols):
            mode = source.horizontalHeader().sectionResizeMode(i)
            header.setSectionResizeMode(i, mode)

        self.table.verticalHeader().setVisible(source.verticalHeader().isVisible())
        self.table.setFont(source.font())

        for r in range(rows):
            self.table.setRowHeight(r, source.rowHeight(r))
            for c in range(cols):
                item = source.item(r, c)
                if item is None:
                    continue
                # 复制文本与配色。背景/文字颜色必须成对搬运，
                # 否则标红的行在放大窗口里会失去底色。
                new_item = QTableWidgetItem(item.text())
                new_item.setBackground(item.background())
                new_item.setForeground(item.foreground())
                if item.font() != source.font():
                    new_item.setFont(item.font())
                self.table.setItem(r, c, new_item)

        # 放大窗口里表格只作查看，禁掉编辑以免产生无效修改。
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)


def _build_close_row(parent: QWidget) -> QHBoxLayout:
    """构建放大窗口底部的「关闭」按钮行。

    两个放大对话框共用同一套按钮与样式，抽出来避免重复。

    Args:
        parent: 按钮所属的对话框。

    Returns:
        装配完成的按钮行布局。
    """
    row = QHBoxLayout()
    row.addStretch()

    close_btn = QPushButton("关闭")
    close_btn.setStyleSheet(
        "QPushButton { background-color: #1976d2; color: white; "
        "font-weight: bold; padding: 6px 24px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #1565c0; }"
    )
    close_btn.clicked.connect(parent.accept)
    row.addWidget(close_btn)

    return row


def attach_zoom_button(
    text_edit: QPlainTextEdit | QTableWidget,
    button_row: QHBoxLayout,
    title: str,
) -> QPushButton:
    """为文本区或表格配一个「放大」按钮，点击后整屏显示其内容。

    按钮插到传入的按钮行中。两类控件的放大行为不同：

    - ``QPlainTextEdit``：在放大窗口里可直接编辑；关闭时内容回写源控件。
    - ``QTableWidget``：在放大窗口里只读，仅用于通览全部行。

    Args:
        text_edit: 需要被放大的文本框或表格。
        button_row: 按钮所在的行布局，按钮会被追加到其中。
        title: 放大窗口的标题。

    Returns:
        创建出的放大按钮，便于调用方按需调整样式。
    """
    btn = QPushButton("放大 ⤢")

    def _open_zoom() -> None:
        """弹出放大窗口，并在关闭后按控件类型决定是否回写内容。"""
        if isinstance(text_edit, QTableWidget):
            dialog: QDialog = TableZoomDialog(title, text_edit, text_edit.window())
            _resize_zoom(dialog, text_edit)
            dialog.exec()
            return

        dialog = TextZoomDialog(
            title, text_edit.toPlainText(), text_edit.isReadOnly(), text_edit.window()
        )
        # 把源控件交给对话框，供 closeEvent 回写。
        dialog._source = text_edit
        _resize_zoom(dialog, text_edit)
        dialog.exec()
        # 只读控件在对话框里也保持只读，无需回写。
        if not text_edit.isReadOnly():
            text_edit.setPlainText(dialog.edit.toPlainText())

    btn.clicked.connect(_open_zoom)
    button_row.addWidget(btn)
    return btn


def _resize_zoom(dialog: QDialog, source: QWidget) -> None:
    """把放大对话框调整为主窗口尺寸的 92%。

    Args:
        dialog: 待调整的对话框。
        source: 用于反查主窗口尺寸的源控件。
    """
    window = source.window()
    if window is not None:
        geo = window.geometry()
        dialog.resize(int(geo.width() * 0.92), int(geo.height() * 0.92))



class MainWindow(QMainWindow):
    """应用主窗口。

    界面搭建由 ``_build_*`` 系列方法完成，事件处理由 ``_on_*`` 系列方法完成。

    Attributes:
        _records: 最近一次扫描的结果，四个标签页共用。
        _rename_plan: 最近一次计算出的改名计划，供确认后执行。
        _roster: 由界面文本框解析出的学生信息表，供需求二补全字段。
        _wordcount_results: 最近一次字数检查的结果。
        _wordcount_skipped: 字数检查中跳过处理的文件及原因。
        _wordcount_threshold: 最近一次字数检查使用的阈值。
    """

    def __init__(self) -> None:
        """构造主窗口，初始化状态并搭建界面。"""
        super().__init__()

        self.setWindowTitle("作业文件批量整理工具")
        self.resize(1100, 760)

        self._records: list[scan_core.ScanRecord] = []
        self._rename_plan: rename_core.RenamePlan | None = None
        self._roster: roster_core.Roster = roster_core.Roster()

        self._build_ui()

    # -----------------------------------------------------------------------
    # 界面搭建
    # -----------------------------------------------------------------------
    def _build_ui(self) -> None:
        """搭建整体界面结构。

        依次构建顶部公共区、中部标签页与日志区、底部状态区。中部使用
        ``QSplitter`` 分隔标签页与日志区，使用户可自行调整两者高度比例。
        """
        # QMainWindow 必须设置中央部件，否则布局无处附着。
        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.addWidget(self._build_top_area())

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_scan_tab(), "需求1 · 扫描列出")
        self.tabs.addTab(self._build_rename_tab(), "需求2 · 批量改名")
        self.tabs.addTab(self._build_archive_tab(), "需求3 · 归档报告")
        self.tabs.addTab(self._build_wordcount_tab(), "新增 · 作业检查")
        self.tabs.addTab(self._build_titlesort_tab(), "新增 · 作业归类")
        splitter.addWidget(self.tabs)
        splitter.addWidget(self._build_log_area())
        splitter.setSizes([560, 200])
        root_layout.addWidget(splitter)

        # 状态区独立成行而非置于 QStatusBar，因进度条需要较长的横向空间。
        status_row = QHBoxLayout()

        self.progress_bar = QProgressBar()
        # 取值范围 0-100，与 _set_progress 计算出的百分比对应。
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        status_row.addWidget(self.progress_bar, stretch=1)

        self.status_label = QLabel("就绪")
        # 固定最小宽度，避免文字长度变化导致进度条左右抖动。
        self.status_label.setMinimumWidth(320)
        status_row.addWidget(self.status_label)

        root_layout.addLayout(status_row)

    def _build_top_area(self) -> QWidget:
        """构建顶部公共区，包含目录选择、扩展名过滤、字数阈值与扫描按钮。

        Returns:
            装配完成的 ``QGroupBox``。
        """
        group = QGroupBox("① 选择目录与扫描条件")
        layout = QVBoxLayout(group)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("作业目录："))

        # 使用可编辑输入框而非只读标签，便于用户直接粘贴路径。
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText("请选择一个文件夹")
        dir_row.addWidget(self.dir_edit, stretch=1)

        browse_btn = QPushButton("浏览...")
        # connect 为 Qt 的信号槽连接，将用户操作绑定到处理函数。
        browse_btn.clicked.connect(self._on_browse_dir)
        dir_row.addWidget(browse_btn)

        layout.addLayout(dir_row)

        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("扩展名过滤："))

        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText("留空 = 不过滤；多个用空格或逗号分隔，例如：.docx .pdf")
        options_row.addWidget(self.ext_edit, stretch=1)

        self.recursive_check = QCheckBox("包含子文件夹")
        options_row.addWidget(self.recursive_check)

        layout.addLayout(options_row)

        action_row = QHBoxLayout()

        self.scan_btn = QPushButton("扫描")
        self.scan_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "font-weight: bold; padding: 6px 24px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self.scan_btn.clicked.connect(self._on_scan)
        action_row.addWidget(self.scan_btn)

        layout.addLayout(action_row)

        return group

    def _build_log_area(self) -> QWidget:
        """构建底部日志区，用于显示操作结果、报告与错误信息。

        整理报告可达数十行，超出一句话弹窗的承载能力，故使用可滚动的文本区
        呈现，并允许用户选中复制。

        Returns:
            装配完成的 ``QGroupBox``。
        """
        group = QGroupBox("操作输出 / 报告")
        layout = QVBoxLayout(group)

        # 使用 QPlainTextEdit 而非 QTextEdit，前者在大段纯文本下性能更好。
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        # 使用等宽字体，使扫描结果等对齐文本显示整齐。
        font = QFont("Consolas", 10)
        self.log_view.setFont(font)

        layout.addWidget(self.log_view)

        # 日志区固定在下方约 200 像素高，长报告需要点「放大」整屏查看。
        log_button_row = QHBoxLayout()
        log_button_row.addStretch()
        attach_zoom_button(self.log_view, log_button_row, "操作输出 / 报告（放大查看）")
        layout.addLayout(log_button_row)

        return group

    # -----------------------------------------------------------------------
    # 标签页一：扫描与列出（需求1）
    # -----------------------------------------------------------------------
    def _build_scan_tab(self) -> QWidget:
        """构建需求 1 的界面，以表格展示扫描结果。

        表格列为：序号、文件名、大小、修改时间、字数。

        Returns:
            装配完成的页面控件。
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel(
            "填写目录后点击上方「扫描」。表格会列出所有文件的大小与修改时间；"
            "填了扩展名则只显示匹配的文件。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.scan_table = QTableWidget()
        self.scan_table.setColumnCount(5)
        self.scan_table.setHorizontalHeaderLabels(
            ["序号", "文件名", "大小", "修改时间", "字数"]
        )

        # 文件名列拉伸填充剩余空间，其余列按内容自适应。
        header = self.scan_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self.scan_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.scan_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        # 已设有序号列，隐藏默认行号表头。
        self.scan_table.verticalHeader().setVisible(False)

        layout.addWidget(self.scan_table)

        self.scan_summary = QLabel("尚未扫描。")
        layout.addWidget(self.scan_summary)

        return page

    # -----------------------------------------------------------------------
    # 标签页二：批量改名（需求2）
    # -----------------------------------------------------------------------
    def _build_rename_tab(self) -> QWidget:
        """构建需求 2 的标签页：把内容区套进一个可滚动的容器。

        需求 2 纵向内容最多（折叠说明 + 信息表 + 作业白名单 + 格式设置 +
        按钮行 + 预览表），窗口不够高时底部的预览表会被挤扁甚至看不见。
        为此把整页塞进 :class:`QScrollArea`，鼠标滚轮在页面**任意位置**都能
        上下滚动，把每个区块都看全。

        Returns:
            装配完成的标签页控件（一个滚动区）。
        """
        scroll = QScrollArea()
        # setWidgetResizable(True) 让内容区随窗口宽度自适应，只让高度产生滚动，
        # 否则内容会保持自身最小尺寸、横向也冒出滚动条。
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._build_rename_page_content())
        return scroll

    def _build_rename_page_content(self) -> QWidget:
        """构建需求 2 的界面内容：信息表、格式选择、预览、确认、执行。

        页面自上而下共五个区块：

            1. 识别规则说明 —— 折叠块，展开后是完整判断规则
            2. 学生信息表   —— 学号与姓名的对照，用于补全缺失字段
            3. 作业白名单   —— 直接指定哪些是作业名，优先于启发式判断
            4. 格式设置     —— 原文件名格式与目标命名格式各一个下拉框
            5. 操作按钮     —— 预览与执行分离，不提供直接改名的入口
            6. 预览表格     —— 缺学号姓名的行标红

        预览与执行拆分为两个独立按钮，从交互层面保证用户先查看预览结果。

        Returns:
            装配完成的页面内容控件。
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        # 识别规则较长，默认折叠成一行概要，需要时点开查看全部细则。
        layout.addWidget(
            CollapsibleHint(
                summary="识别规则说明（点击展开全部细则）",
                detail=(
                    "程序按下方选定的「原文件名格式」识别每个文件，再按「新文件名格式」重命名。\n"
                    "\n"
                    "【第一步 · 作业白名单优先】\n"
                    "    若「作业白名单」里有内容，先把文件名切段，"
                    "与表中某条**完全一致**的那一段直接认定为作业名。\n"
                    "    这条规则最优先，因为白名单是你亲手写的，比程序猜的准。\n"
                    "    白名单留空则跳过本步，直接进入第二步。\n"
                    "\n"
                    "【第二步 · 按段数与内容判断】（默认「自动识别」时）\n"
                    "    切段方式：按 _ + 空格 三种分隔符切分。\n"
                    "    仅 1 段 → 缺少学号或姓名，标红不予通过；\n"
                    "    2 段（数字 + 文本）→ 数字作学号，文本直接作作业名；\n"
                    "    2 段（文本 + 文本）→ 含「作业/报告/实验」等字样者作作业名；"
                    "两段都含时取后置段，都不含时也取后置段；另一段作姓名；\n"
                    "    3 段（数字 + 两段文本）→ 数字作学号，两段文本套用上面的规则；\n"
                    "    其余情况（如两段都是数字）→ 不予识别，同样标红。\n"
                    "\n"
                    "【硬性要求】\n"
                    "    学号与姓名**至少要有一个**。若两者都没有，一律标红不予改名，"
                    "并给出上面两步的判定依据。\n"
                    "    作业白名单只决定「哪一段是作业名」，不能用来代替学号或姓名。\n"
                    "\n"
                    "【操作方式】\n"
                    "    必须先点「预览改名结果」，确认无误后再点「确认执行改名」。\n"
                    "    遇到重名冲突会自动跳过，绝不覆盖任何已有文件。"
                ),
            )
        )

        layout.addWidget(self._build_roster_group())

        layout.addWidget(self._build_rename_title_whitelist_group())

        # ---- 格式设置区 ----
        format_group = QGroupBox("格式设置")
        format_layout = QVBoxLayout(format_group)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("原文件名格式："))
        self.rename_source_combo = QComboBox()
        for label, fmt in rename_core.SOURCE_FORMAT_PRESETS:
            # 显示中文描述，程序内部用字段元组，两者以 userData 关联。
            self.rename_source_combo.addItem(label, fmt)
        # 默认选中首项「自动识别」——无需预先知道文件名结构，适用面最广。
        self.rename_source_combo.setCurrentIndex(0)
        source_row.addWidget(self.rename_source_combo, stretch=1)
        format_layout.addLayout(source_row)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("新文件名格式："))
        self.rename_target_combo = QComboBox()
        for label, template in rename_core.TARGET_TEMPLATE_PRESETS:
            self.rename_target_combo.addItem(label, template)
        # 默认选中需求原文示例「作业名_学号」。
        self.rename_target_combo.setCurrentIndex(0)
        self.rename_target_combo.currentIndexChanged.connect(
            self._on_rename_target_preset
        )
        target_row.addWidget(self.rename_target_combo, stretch=1)
        format_layout.addLayout(target_row)

        custom_row = QHBoxLayout()
        custom_row.addWidget(QLabel("自定义模板："))
        self.rename_template_edit = QLineEdit()
        self.rename_template_edit.setText(rename_core.DEFAULT_TEMPLATE)
        self.rename_template_edit.setPlaceholderText(rename_core.DEFAULT_TEMPLATE)
        # 用户手工编辑模板时同步回下拉框的选中状态。
        self.rename_template_edit.textChanged.connect(self._on_rename_template_edited)
        custom_row.addWidget(self.rename_template_edit, stretch=1)
        format_layout.addLayout(custom_row)

        placeholder_hint = QLabel(
            "可用占位符：{id} = 学号，{name} = 姓名，{title} = 作业名。"
            "选择预设会自动填入模板，也可直接手动编辑。"
        )
        placeholder_hint.setWordWrap(True)
        placeholder_hint.setStyleSheet("color: #666; font-size: 12px;")
        format_layout.addWidget(placeholder_hint)

        layout.addWidget(format_group)

        # ---- 操作按钮区 ----
        button_row = QHBoxLayout()

        self.preview_rename_btn = QPushButton("① 预览改名结果")
        self.preview_rename_btn.clicked.connect(self._on_preview_rename)
        button_row.addWidget(self.preview_rename_btn)

        self.apply_rename_btn = QPushButton("② 确认执行改名")
        # 初始禁用：未预览前不允许执行，避免直接改名。
        self.apply_rename_btn.setEnabled(False)
        # 橙色标识该操作会实际修改文件。
        self.apply_rename_btn.setStyleSheet(
            "QPushButton { background-color: #f57c00; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ef6c00; }"
            "QPushButton:disabled { background-color: #ccc; color: #888; }"
        )
        self.apply_rename_btn.clicked.connect(self._on_apply_rename)
        button_row.addWidget(self.apply_rename_btn)

        button_row.addStretch()
        layout.addLayout(button_row)

        # ---- 预览表格 ----
        self.rename_table = QTableWidget()
        self.rename_table.setColumnCount(5)
        self.rename_table.setHorizontalHeaderLabels(
            ["状态", "原文件名", "新文件名", "识别结果", "判定依据"]
        )
        header = self.rename_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.rename_table.verticalHeader().setVisible(False)

        # 表头行右侧放「放大」按钮，表格行多时点开整屏通览。
        table_header_row = QHBoxLayout()
        table_header_row.addWidget(QLabel("改名预览："))
        table_header_row.addStretch()
        attach_zoom_button(self.rename_table, table_header_row, "改名预览（放大查看）")
        layout.addLayout(table_header_row)

        # 表格在滚动区内不再靠 stretch 抢高度（滚动区里的内容高度由自身决定），
        # 改为给一个够用的最小高度：窗口够高时它会撑满剩余空间，窗口不够高
        # 时由外层滚动条兜住，表格本身仍完整可见。
        self.rename_table.setMinimumHeight(260)
        layout.addWidget(self.rename_table, stretch=1)

        return page

    def _build_roster_group(self) -> QGroupBox:
        """构建「学生信息表」输入区。

        信息表用于按学号补全姓名、或按姓名补全学号。提供文本框直接输入与
        导入 TXT 文件两条路径，二者最终流向同一个解析函数，行为一致。

        Returns:
            装配完成的分组框控件。
        """
        group = QGroupBox("学生信息表（用于补全缺失的学号或姓名，可留空）")
        layout = QVBoxLayout(group)

        tip = QLabel(
            "每行一条：学号在前、姓名在后，用逗号或空格分隔。"
            "以 # 开头的行是注释，表头行会自动跳过。\n"
            "例如：20230001,张三   或   20230001 张三"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        self.roster_edit = QPlainTextEdit()
        self.roster_edit.setPlaceholderText("在此粘贴信息表，或点右侧「导入 TXT 文件」选择文件…")
        self.roster_edit.setPlainText(roster_core.SAMPLE_ROSTER_TEXT)
        self.roster_edit.setFixedHeight(92)
        layout.addWidget(self.roster_edit)

        button_row = QHBoxLayout()

        import_btn = QPushButton("导入 TXT 文件")
        import_btn.clicked.connect(self._on_import_roster)
        button_row.addWidget(import_btn)

        sample_btn = QPushButton("填入示例")
        sample_btn.clicked.connect(self._on_fill_roster_sample)
        button_row.addWidget(sample_btn)

        export_btn = QPushButton("导出示例 TXT 文件")
        export_btn.clicked.connect(self._on_export_roster_sample)
        button_row.addWidget(export_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._on_clear_roster)
        button_row.addWidget(clear_btn)

        self.roster_status_label = QLabel("尚未解析信息表")
        self.roster_status_label.setStyleSheet("color: #666;")
        button_row.addWidget(self.roster_status_label)

        button_row.addStretch()

        # 信息表在紧凑布局里只留 92 像素高，要看全或大改时点「放大」整屏编辑。
        attach_zoom_button(self.roster_edit, button_row, "学生信息表（放大编辑）")

        layout.addLayout(button_row)

        return group

    def _build_rename_title_whitelist_group(self) -> QGroupBox:
        """构建需求 2 的「作业白名单」输入区。

        白名单是本页与「学生信息表」并列的第二张表，两者作用完全不同：

        * **学生信息表**管的是*学号与姓名*的互相补全；
        * **作业白名单**管的是*哪一段才是作业名*。

        由于需求 2 的识别靠特征词猜作业名，「宏观经济学」这类不含特征词的
        作业名会被误判成姓名。把作业名写进白名单后，与之完全一致的那一段会
        被**直接认定为作业名**，比任何启发式判断都权威。

        白名单只影响作业名的认定，**不放松「学号与姓名至少要有一个」的硬
        要求**——文件名里两样都没有时，照样标红不改。

        Returns:
            装配完成的分组框控件。
        """
        group = QGroupBox("作业白名单（可选：写明哪些是作业名，避免被误判成姓名）")
        layout = QVBoxLayout(group)

        tip = QLabel(
            "每行一个作业名。识别时先把文件名切段，再与下表比对：\n"
            "**完全一致的那一段直接认定为作业名**，不再走特征词猜测。\n"
            "留空表示不使用白名单，仍按内置特征词规则判断。\n"
            "本表**不能**替代学号或姓名——文件名里两者都没有时照样标红不改。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        self.rename_title_edit = QPlainTextEdit()
        self.rename_title_edit.setPlaceholderText(
            "每行一个作业名，例如：\n第一次作业\n宏观经济学\n读书笔记"
        )
        self.rename_title_edit.setPlainText(archive_core.SAMPLE_TITLE_WHITELIST_TEXT)
        self.rename_title_edit.setFixedHeight(84)
        layout.addWidget(self.rename_title_edit)

        button_row = QHBoxLayout()

        import_btn = QPushButton("导入 TXT 文件")
        import_btn.clicked.connect(self._on_import_rename_title_whitelist)
        button_row.addWidget(import_btn)

        sample_btn = QPushButton("填入示例")
        sample_btn.clicked.connect(self._on_fill_rename_title_sample)
        button_row.addWidget(sample_btn)

        export_btn = QPushButton("导出示例 TXT 文件")
        export_btn.clicked.connect(self._on_export_rename_title_sample)
        button_row.addWidget(export_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._on_clear_rename_title_whitelist)
        button_row.addWidget(clear_btn)

        self.rename_title_count_label = QLabel("")
        self.rename_title_count_label.setStyleSheet("color: #666;")
        button_row.addWidget(self.rename_title_count_label)

        button_row.addStretch()

        # 白名单在紧凑布局里只留 84 像素高，要写很多条时点「放大」整屏编辑。
        attach_zoom_button(
            self.rename_title_edit, button_row, "作业白名单（放大编辑）"
        )

        layout.addLayout(button_row)

        # 文本变化时刷新计数，让用户随时知道表里有几条。
        self.rename_title_edit.textChanged.connect(self._refresh_rename_title_count)
        self._refresh_rename_title_count()

        return group

    def _refresh_rename_title_count(self) -> None:
        """刷新需求 2 作业白名单右侧的条数提示。

        与作业归类页不同，白名单是**可选**的：留空不会禁用任何按钮，只是
        提示用户当前按内置规则判断。因此这里只改提示文字与颜色，不碰按钮。
        """
        titles = archive_core.parse_title_whitelist_text(
            self.rename_title_edit.toPlainText()
        )
        if titles:
            self.rename_title_count_label.setText(f"共 {len(titles)} 个作业名")
            self.rename_title_count_label.setStyleSheet("color: #2e7d32;")
        else:
            self.rename_title_count_label.setText("白名单为空（按内置规则判断）")
            self.rename_title_count_label.setStyleSheet("color: #666;")

    # -----------------------------------------------------------------------
    # 标签页三：归档与报告（需求3）
    # -----------------------------------------------------------------------
    def _build_archive_tab(self) -> QWidget:
        """构建需求 3 的界面：选择分类方式 → 归档 → 查看报告 → 撤销。

        排版要点：报告区是这一页真正要读的内容，因此让它占据页面的剩余空间
        （``stretch=1``），而不是给一个固定高度。分类方式与按钮行紧贴相邻，
        中间不留空白，避免页面上半部出现大片空洞。

        Returns:
            装配完成的页面控件。
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        # 布局内边距收紧，让内容区尽可能大。
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        hint = QLabel(
            "按类别把文件移动到子文件夹，并生成整理报告。"
            "如果整理结果不符合预期，可以点「撤销上次操作」把文件全部搬回原位。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # ---- 分类方式与按钮合并成一行，省去一整行的纵向空间 ----
        control_row = QHBoxLayout()
        control_row.setSpacing(6)
        control_row.addWidget(QLabel("分类方式："))

        self.archive_mode_combo = QComboBox()
        # userData 用来存「界面上显示的文字」和「程序里用的值」的对应关系。
        # 显示中文给用户看，程序内部用 "extension" / "semester"。
        self.archive_mode_combo.addItem("按文件类型（Word文档 / PDF文档 ...）", "extension")
        self.archive_mode_combo.addItem("按学期（根据文件修改时间判断）", "semester")
        control_row.addWidget(self.archive_mode_combo, stretch=1)

        self.archive_btn = QPushButton("① 执行归档")
        self.archive_btn.setStyleSheet(
            "QPushButton { background-color: #f57c00; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ef6c00; }"
        )
        self.archive_btn.clicked.connect(self._on_archive)
        control_row.addWidget(self.archive_btn)

        self.undo_btn = QPushButton("② 撤销上次操作")
        # 撤销按钮用红色系，因为它会「反向移动文件」，也需要用户有心理准备。
        self.undo_btn.setStyleSheet(
            "QPushButton { background-color: #d32f2f; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #c62828; }"
        )
        self.undo_btn.clicked.connect(self._on_undo)
        control_row.addWidget(self.undo_btn)

        self.history_btn = QPushButton("查看操作历史")
        self.history_btn.clicked.connect(self._on_show_history)
        control_row.addWidget(self.history_btn)

        layout.addLayout(control_row)

        # ---- 学期区间设置（仅在按学期归档时生效）----
        layout.addWidget(self._build_semester_group())

        # ---- 报告预览区：吃掉页面剩余高度 ----
        # 先创建报告控件，因为放大按钮需要引用它。
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setFont(QFont("Consolas", 10))

        report_header = QHBoxLayout()
        report_header.addWidget(QLabel("整理报告："))
        report_header.addStretch()
        # 放大按钮放到标题行右侧，省掉单独一行按钮的高度。
        attach_zoom_button(self.report_view, report_header, "整理报告（放大查看）")
        layout.addLayout(report_header)

        # stretch=1 让报告区填满剩余空间，页面不再出现大片空白。
        layout.addWidget(self.report_view, stretch=1)

        return page

    def _build_semester_group(self) -> QWidget:
        """构建「学期区间设置」折叠区，供用户按自己学校的校历填写。

        默认区间表是常见校历的**估计值**，各校开学/放假日期不同，直接用很可能
        让文件落进「未归类」。因此把区间做成可编辑的，并默认收起，不干扰
        按文件类型归档的用户。

        Returns:
            装配完成的 ``QGroupBox``。
        """
        self.semester_group = QGroupBox("学期区间设置（仅「按学期」归档时生效，点标题可展开）")
        self.semester_group.setCheckable(True)
        # 默认收起，避免占用按文件类型归档用户的屏幕空间。
        self.semester_group.setChecked(False)
        semester_layout = QVBoxLayout(self.semester_group)

        tip = QLabel(
            "每行一条：学期名, 开始日期, 结束日期（日期格式 YYYY-MM-DD）。"
            "以 # 开头的行是注释。\n"
            "文件的修改时间落在哪个区间，就归入哪个学期文件夹；"
            "落不进任何区间的文件归入「未归类」，并在报告中列出其修改时间。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        semester_layout.addWidget(tip)

        self.semester_edit = QPlainTextEdit()
        self.semester_edit.setPlaceholderText(
            "例如：2026-2027-1, 2026-09-01, 2027-01-15"
        )
        self.semester_edit.setPlainText(self._default_semester_text())
        self.semester_edit.setFixedHeight(96)
        semester_layout.addWidget(self.semester_edit)

        btn_row = QHBoxLayout()

        sample_btn = QPushButton("填入默认区间")
        sample_btn.clicked.connect(self._on_fill_semester_default)
        btn_row.addWidget(sample_btn)

        validate_btn = QPushButton("校验区间")
        validate_btn.clicked.connect(self._on_validate_semester)
        btn_row.addWidget(validate_btn)

        self.semester_status_label = QLabel("")
        self.semester_status_label.setStyleSheet("color: #666;")
        btn_row.addWidget(self.semester_status_label)

        btn_row.addStretch()

        # 区间表可能有多条，行数多时点「放大」整屏编辑。
        attach_zoom_button(self.semester_edit, btn_row, "学期区间设置（放大编辑）")

        semester_layout.addLayout(btn_row)

        return self.semester_group

    @staticmethod
    def _default_semester_text() -> str:
        """把 core 层的默认学期区间渲染成可编辑文本。

        Returns:
            每行一条 ``学期名, 开始日期, 结束日期`` 的文本。
        """
        return "\n".join(
            f"{name}, {start}, {end}"
            for name, start, end in archive_core.DEFAULT_SEMESTERS
        )

    # -----------------------------------------------------------------------
    # 标签页四：作业检查（新增功能）
    # -----------------------------------------------------------------------
    def _build_wordcount_tab(self) -> QWidget:
        """构建「作业检查」界面：字数统计 + 按检索信息汇总。

        页面自上而下共六个区块：

            1. 检查规则说明 —— 默认折叠，说明匹配口径与标色规则
            2. 检索信息     —— 每行一条，决定汇总表有哪些行
            3. 字数要求     —— 本页专属，低于该字数的文件被判为未达标
            4. 按钮行       —— 开始检查 / 查看报告 + 结果徽标
            5. 汇总表       —— 每条检索信息的识别/达标/未达标/读取失败份数
            6. 逐文件明细表 —— 保留原有的字数明细与标红效果

        Returns:
            装配完成的页面控件。
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        # ---- 检查规则说明（默认折叠）----
        hint = CollapsibleHint(
            summary="检查规则说明（按段匹配汇总，点击展开）",
            detail=(
                "① 检索信息每行一条，可以是姓名、学号、作业名等任意内容。\n"
                "   程序把文件名按「_」「+」空格切成任意多段，某一段与检索信息\n"
                "   **整段相等**才算命中（所以「张三」不会命中「张三丰」）。\n"
                "\n"
                "② 一个文件命中多条信息时，会同时计入每一行。\n"
                "\n"
                "③ 汇总表的四列数字：\n"
                "   · 识别份数   —— 命中该信息的文件总数\n"
                "   · 达标份数   —— 其中字数不低于要求的份数\n"
                "   · 未达标份数 —— 其中字数低于要求的份数\n"
                "   · 读取失败   —— 文件打不开（如扫描件 PDF），单独统计，\n"
                "                  既不计入未达标，也不影响绿标\n"
                "\n"
                "④ 标色规则：达标份数 ≥ 1 就标绿，否则标红。\n"
                "   所有行都标绿时表头出现「✓ 全部通过」；\n"
                "   存在标红行时会弹出提示，并点名到具体信息。\n"
                "\n"
                "⑤ 下方保留逐文件明细表，按文件角度看字数与标红原因。"
            ),
        )
        layout.addWidget(hint)

        # ---- 检索信息输入区 ----
        layout.addWidget(self._build_check_info_group())

        # ---- 字数要求（本页专属，不放在顶部公共区）----
        layout.addWidget(self._build_threshold_group())

        # ---- 按钮行 ----
        button_row = QHBoxLayout()

        self.wordcount_btn = QPushButton("① 开始作业检查")
        self.wordcount_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self.wordcount_btn.clicked.connect(self._on_wordcount)
        button_row.addWidget(self.wordcount_btn)

        self.wordcount_report_btn = QPushButton("② 查看检查报告")
        self.wordcount_report_btn.clicked.connect(self._on_wordcount_report)
        button_row.addWidget(self.wordcount_report_btn)

        button_row.addStretch()

        # 位于按钮行右侧的统计标签，显示整体通过情况。
        self.wordcount_badge = QLabel("尚未检查")
        self.wordcount_badge.setStyleSheet(
            "font-weight: bold; color: #666; padding: 4px 10px;"
        )
        button_row.addWidget(self.wordcount_badge)

        layout.addLayout(button_row)

        # ---- 汇总表 ----
        summary_header_row = QHBoxLayout()
        self.check_summary_note = QLabel("检查汇总：尚未检查")
        self.check_summary_note.setStyleSheet("font-weight: bold; color: #666;")
        summary_header_row.addWidget(self.check_summary_note)
        summary_header_row.addStretch()
        # 表格此刻尚未创建，放大按钮在表格创建后再挂（见下方）。
        layout.addLayout(summary_header_row)

        self.check_summary_table = QTableWidget()
        self.check_summary_table.setColumnCount(5)
        self.check_summary_table.setHorizontalHeaderLabels(
            ["输入信息", "识别份数", "达标份数", "未达标份数", "读取失败"]
        )
        summary_hdr = self.check_summary_table.horizontalHeader()
        summary_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            summary_hdr.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self.check_summary_table.verticalHeader().setVisible(False)
        self.check_summary_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.check_summary_table.setMinimumHeight(120)
        self.check_summary_table.setMaximumHeight(240)
        # 表格创建后再挂放大按钮，避免 attach 时控件尚不存在。
        attach_zoom_button(
            self.check_summary_table, summary_header_row, "检查汇总表（放大查看）"
        )
        layout.addWidget(self.check_summary_table)

        # ---- 逐文件明细表 ----
        detail_header_row = QHBoxLayout()
        detail_header_row.addWidget(QLabel("逐文件明细："))
        detail_header_row.addStretch()

        self.wordcount_table = QTableWidget()
        self.wordcount_table.setColumnCount(4)
        self.wordcount_table.setHorizontalHeaderLabels(
            ["文件名", "字数", "状态", "说明"]
        )
        header = self.wordcount_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.wordcount_table.verticalHeader().setVisible(False)
        self.wordcount_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        attach_zoom_button(
            self.wordcount_table, detail_header_row, "字数明细表（放大查看）"
        )
        layout.addLayout(detail_header_row)
        layout.addWidget(self.wordcount_table, stretch=1)

        # 缓存统计结果，供"查看报告"按钮复用。
        self._wordcount_results: list[wordcount_core.WordCountResult] = []
        self._wordcount_skipped: list[tuple[str, str]] = []
        self._wordcount_threshold: int = 0
        # 缓存汇总结果，供渲染汇总表与生成报告复用。
        self._check_summary: wordcount_core.HomeworkCheckSummary | None = None

        return page

    def _build_threshold_group(self) -> QGroupBox:
        """构建「字数要求」输入区，仅属于作业检查页。

        原先该控件放在顶部公共区，导致切换其它功能标签页时也会看到「字数要求」，
        与其使用场景不符。移入本页后只在作业检查界面出现。

        Returns:
            装配完成的分组框控件。
        """
        group = QGroupBox("字数要求")
        layout = QHBoxLayout(group)

        layout.addWidget(QLabel("不低于"))

        # 使用 QSpinBox 而非 QLineEdit，限定只能输入整数，避免非数字输入。
        self.threshold_spin = QSpinBox()
        # 下限 0 表示不限制。
        self.threshold_spin.setRange(0, 1_000_000)
        self.threshold_spin.setValue(500)
        self.threshold_spin.setSingleStep(100)
        self.threshold_spin.setSuffix(" 字")
        # 固定宽度，避免数字位数变化引起同行控件位移。
        self.threshold_spin.setFixedWidth(140)
        layout.addWidget(self.threshold_spin)

        tip = QLabel("填 0 表示不限制字数，所有文件都会被判为达标。")
        tip.setStyleSheet("color: #666;")
        layout.addWidget(tip)

        layout.addStretch()
        return group

    def _build_check_info_group(self) -> QGroupBox:
        """构建「检索信息」输入区。

        这块文本决定汇总表有哪些行：一行一条信息，程序逐条与文件名比对。

        Returns:
            装配完成的分组框控件。
        """
        group = QGroupBox("检索信息（每行一条，用于汇总统计与匹配）")
        layout = QVBoxLayout(group)

        tip = QLabel(
            "每行一条检索信息，可以是姓名、学号、作业名等任意内容。\n"
            "程序把文件名切成任意多段后逐段比对，**整段相等**才算命中。\n"
            "留空时可以照常做字数检查，但不会生成汇总表。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        self.check_info_edit = QPlainTextEdit()
        self.check_info_edit.setPlaceholderText(
            "每行一条，例如：\n张三\n20230101\n第一次作业"
        )
        self.check_info_edit.setPlainText(wordcount_core.SAMPLE_CHECK_INFO_TEXT)
        self.check_info_edit.setFixedHeight(84)
        layout.addWidget(self.check_info_edit)

        btn_row = QHBoxLayout()

        import_btn = QPushButton("导入 TXT 文件")
        import_btn.clicked.connect(self._on_import_check_info)
        btn_row.addWidget(import_btn)

        sample_btn = QPushButton("填入示例")
        sample_btn.clicked.connect(self._on_fill_check_info_sample)
        btn_row.addWidget(sample_btn)

        export_btn = QPushButton("导出示例 TXT 文件")
        export_btn.clicked.connect(self._on_export_check_info_sample)
        btn_row.addWidget(export_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._on_clear_check_info)
        btn_row.addWidget(clear_btn)

        self.check_info_count_label = QLabel("")
        self.check_info_count_label.setStyleSheet("color: #666;")
        btn_row.addWidget(self.check_info_count_label)

        btn_row.addStretch()
        # 控件须先创建才能挂放大按钮，此处 check_info_edit 已就绪。
        attach_zoom_button(
            self.check_info_edit, btn_row, "检索信息（放大编辑）"
        )

        layout.addLayout(btn_row)

        # 文本变化时刷新计数，让用户随时知道表里有几条。
        self.check_info_edit.textChanged.connect(self._refresh_check_info_count)
        self._refresh_check_info_count()

        return group

    def _refresh_check_info_count(self) -> None:
        """刷新检索信息右侧的条数提示。"""
        keywords = self._read_check_info()
        if keywords:
            self.check_info_count_label.setText(f"共 {len(keywords)} 条")
            self.check_info_count_label.setStyleSheet("color: #2e7d32;")
        else:
            self.check_info_count_label.setText("检索信息为空，不会生成汇总表")
            self.check_info_count_label.setStyleSheet("color: #c62828;")

    def _read_check_info(self) -> list[str]:
        """读取并解析检索信息输入框的内容。

        复用作业归类的解析器，两处的注释/空行/去重/保序规则因此完全一致。

        Returns:
            解析所得的关键词列表；输入框为空时返回空列表。
        """
        return archive_core.parse_keyword_list_text(
            self.check_info_edit.toPlainText()
        )

    # -----------------------------------------------------------------------
    # 标签页五：作业归类（新增功能）
    # -----------------------------------------------------------------------
    def _build_titlesort_tab(self) -> QWidget:
        """构建「作业归类」界面：按文件名中的每一段与关键词表匹配来分组归档。

        与需求3 的「按学期」等分类方式并列，但分类依据来自文件名本身——
        程序把文件名切成**任意多段**，每一段都与用户给出的关键词表比对，
        命中哪一段就用哪一段建文件夹。

        与旧版「识别作业名 + 白名单」的做法不同，本页不做任何字段推断：
        表里写作业名就按作业名分，写姓名就按姓名分，写学号就按学号分。

        页面自上而下共四个区块：

            1. 归类规则   —— 默认折叠，说明逐段匹配的机制
            2. 关键词表   —— 唯一输入，决定哪些段可以被归类
            3. 操作按钮   —— 预览归类结果 / 执行归类 / 撤销
            4. 归类预览表 —— 展示每个文件命中的段及其目标文件夹

        Returns:
            装配完成的页面控件。
        """
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(
            CollapsibleHint(
                summary="归类规则说明（逐段匹配，不限段数，点击展开）",
                detail=(
                    "本功能把文件名按 _ + 空格 切成**任意多段**，每一段都与下方"
                    "「关键词表」逐一比对。\n"
                    "· 不限制段数：2 段、3 段、10 段都可以，多少段都能扫；\n"
                    "· 表里写什么就匹配什么：作业名、姓名、学号一视同仁；\n"
                    "· 命中哪一段，就用**那一段本身**作为文件夹名；\n"
                    "· 一个文件可以**同时命中多段**，此时会为每个命中项各建一个\n"
                    "  文件夹，文件本体放进第一个，其余文件夹里各放一份**副本**；\n"
                    "· 一个段都没命中的文件**保持原地不动**，只在预览表与整理报告的\n"
                    "  「未命中关键词 · 未移动」区块中列出，供你核对；\n"
                    "· 关键词表**留空时无法工作**（本机制完全依赖这张表）。\n"
                    "举例：表里写「第一次作业」和「张三」，文件名\n"
                    "「第一次作业_张三_20230101.docx」会同时出现在\n"
                    "「第一次作业/」和「张三/」两个文件夹里。\n"
                    "文件名不会被修改；副本会占用额外的磁盘空间。"
                ),
            )
        )

        # ---- 关键词表 ----
        layout.addWidget(self._build_title_whitelist_group())

        # ---- 操作按钮区 ----
        button_row = QHBoxLayout()

        self.titlesort_preview_btn = QPushButton("① 预览归类结果")
        self.titlesort_preview_btn.clicked.connect(self._on_titlesort_preview)
        button_row.addWidget(self.titlesort_preview_btn)

        self.titlesort_apply_btn = QPushButton("② 确认执行归类")
        # 初始禁用：与需求二一致，未预览前不允许执行。
        self.titlesort_apply_btn.setEnabled(False)
        self.titlesort_apply_btn.setStyleSheet(
            "QPushButton { background-color: #f57c00; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #ef6c00; }"
            "QPushButton:disabled { background-color: #ccc; color: #888; }"
        )
        self.titlesort_apply_btn.clicked.connect(self._on_titlesort_apply)
        button_row.addWidget(self.titlesort_apply_btn)

        self.titlesort_undo_btn = QPushButton("③ 撤销上次操作")
        self.titlesort_undo_btn.setStyleSheet(
            "QPushButton { background-color: #d32f2f; color: white; "
            "font-weight: bold; padding: 6px 18px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #c62828; }"
        )
        self.titlesort_undo_btn.clicked.connect(self._on_undo)
        button_row.addWidget(self.titlesort_undo_btn)

        button_row.addStretch()

        self.titlesort_status_label = QLabel("尚未预览")
        self.titlesort_status_label.setStyleSheet("color: #666; padding: 4px 10px;")
        button_row.addWidget(self.titlesort_status_label)

        layout.addLayout(button_row)

        # ---- 归类预览表 ----
        self.titlesort_table = QTableWidget()
        self.titlesort_table.setColumnCount(4)
        self.titlesort_table.setHorizontalHeaderLabels(
            ["状态", "文件名", "命中的段", "目标文件夹"]
        )
        header = self.titlesort_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.titlesort_table.verticalHeader().setVisible(False)
        self.titlesort_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )

        table_header_row = QHBoxLayout()
        table_header_row.addWidget(QLabel("归类预览："))
        table_header_row.addStretch()
        attach_zoom_button(
            self.titlesort_table, table_header_row, "归类预览（放大查看）"
        )
        layout.addLayout(table_header_row)

        layout.addWidget(self.titlesort_table, stretch=1)

        return page

    def _build_title_whitelist_group(self) -> QGroupBox:
        """构建「关键词表」输入区。

        表里可写作业名、姓名、学号等任意内容，程序逐段与之比对。

        Returns:
            装配完成的分组框控件。
        """
        group = QGroupBox("关键词表（文件名的每一段都会与本表比对）")
        layout = QVBoxLayout(group)

        tip = QLabel(
            "每行一个关键词。程序把文件名切成任意多段后，逐段与本表比对：\n"
            "命中则归入以**该段本身**命名的文件夹；一个文件命中多段时，会同时"
            "放进多个文件夹（第一个是本体，其余是副本）。\n"
            "任何一段都没命中的文件保持原地不动，只在下方的预览表与整理报告中列出。\n"
            "表里可以混写作业名、姓名、学号。**本表留空时无法执行归类**。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(tip)

        self.titlesort_edit = QPlainTextEdit()
        self.titlesort_edit.setPlaceholderText(
            "每行一个关键词，例如：\n第一次作业\n数据结构实验\n张三"
        )
        self.titlesort_edit.setPlainText(archive_core.SAMPLE_KEYWORD_LIST_TEXT)
        self.titlesort_edit.setFixedHeight(84)
        layout.addWidget(self.titlesort_edit)

        btn_row = QHBoxLayout()

        import_btn = QPushButton("导入 TXT 文件")
        import_btn.clicked.connect(self._on_import_title_whitelist)
        btn_row.addWidget(import_btn)

        sample_btn = QPushButton("填入示例")
        sample_btn.clicked.connect(self._on_fill_title_sample)
        btn_row.addWidget(sample_btn)

        export_btn = QPushButton("导出示例 TXT 文件")
        export_btn.clicked.connect(self._on_export_title_sample)
        btn_row.addWidget(export_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.titlesort_edit.setPlainText(""))
        btn_row.addWidget(clear_btn)

        self.titlesort_count_label = QLabel("")
        self.titlesort_count_label.setStyleSheet("color: #666;")
        btn_row.addWidget(self.titlesort_count_label)

        btn_row.addStretch()
        attach_zoom_button(
            self.titlesort_edit, btn_row, "关键词表（放大编辑）"
        )

        layout.addLayout(btn_row)

        # 文本变化时刷新计数，让用户随时知道表里有几条。
        self.titlesort_edit.textChanged.connect(self._refresh_title_count)
        self._refresh_title_count()

        return group

    def _refresh_title_count(self) -> None:
        """刷新关键词表右侧的条数提示，并同步预览按钮的可用状态。

        关键词表是逐段匹配的唯一依据，留空时整个功能无法工作，因此这里把
        「表为空」直接反映到按钮上：预览按钮变灰并给出原因提示，用户不用
        点一下才知道填表。
        """
        keywords = archive_core.parse_keyword_list_text(
            self.titlesort_edit.toPlainText()
        )
        # 本方法会在布局过程中被提前调用（关键词表控件先于按钮创建），
        # 此时按钮属性尚不存在，因此先取出来判断，避免构建期 AttributeError。
        preview_btn = getattr(self, "titlesort_preview_btn", None)
        apply_btn = getattr(self, "titlesort_apply_btn", None)

        if keywords:
            self.titlesort_count_label.setText(f"共 {len(keywords)} 个关键词")
            self.titlesort_count_label.setStyleSheet("color: #2e7d32;")
            if preview_btn is not None:
                preview_btn.setEnabled(True)
                preview_btn.setToolTip("")
        else:
            self.titlesort_count_label.setText("关键词表为空，无法归类")
            self.titlesort_count_label.setStyleSheet("color: #c62828;")
            if preview_btn is not None:
                preview_btn.setEnabled(False)
                preview_btn.setToolTip(
                    "请先在上方的关键词表中至少填写一项"
                )
            # 表被清空时，之前预览得到的执行按钮也要一并失效。
            if apply_btn is not None:
                apply_btn.setEnabled(False)

    # =======================================================================
    # 事件处理区
    # =======================================================================
    # _build_* 系列方法仅负责界面布局，不含业务逻辑；用户操作触发的行为集中
    # 于下列 _on_* 系列方法。两部分分离，便于分别维护界面与逻辑。

    def _log(self, text: str) -> None:
        """向底部日志区追加一行文本。

        Args:
            text: 待显示的文本。
        """
        # appendPlainText 自动滚动至末尾，保证最新内容始终可见。
        self.log_view.appendPlainText(text)

    def _log_separator(self) -> None:
        """向日志区输出分隔线，区隔不同操作的结果。"""
        self._log("")
        self._log("=" * 72)

    def _current_dir(self) -> Path | None:
        """读取并校验用户填写的目录路径。

        路径为空、不存在或不是目录时弹出提示。

        Returns:
            合法的目录路径；校验失败时返回 ``None``。
        """
        # 去除首尾空格，用户复制路径时可能附带空白字符。
        raw = self.dir_edit.text().strip()

        if not raw:
            QMessageBox.warning(self, "提示", "请先选择或填写作业目录。")
            return None

        path = Path(raw)
        if not path.exists():
            QMessageBox.warning(self, "路径错误", f"目录不存在：\n{path}")
            return None
        if not path.is_dir():
            QMessageBox.warning(self, "路径错误", f"这不是一个文件夹：\n{path}")
            return None

        return path

    def _parse_extensions(self) -> list[str]:
        """解析扩展名输入框的内容，返回扩展名列表。

        支持空格、逗号及中英文逗号分隔，例如 ``".docx .pdf"``、
        ``".docx,.pdf"``、``".docx，.pdf"`` 均有效。

        Returns:
            扩展名列表；输入为空时返回空列表，表示不过滤。
        """
        raw = self.ext_edit.text().strip()
        if not raw:
            return []

        # 统一分隔符后按空白切分；split 无参数时自动处理连续空白。
        normalized = raw.replace(",", " ").replace("，", " ")
        parts = normalized.split()

        return [part for part in parts if part]

    def _set_progress(self, current: int, total: int, text: str) -> None:
        """更新进度条与状态文字。

        该方法作为 progress 回调传入 ``core`` 层的处理函数；``core`` 层只
        负责上报进度，不感知进度条的实现。

        Args:
            current: 当前处理到第几项。
            total: 总项数。
            text: 当前处理项的说明文本。
        """
        if total <= 0:
            self.progress_bar.setValue(0)
            return

        percent = int(current * 100 / total)
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"({current}/{total}) {text}")

        # Qt 默认在事件循环空闲时才重绘界面。在循环中连续更新进度时，界面会
        # 停滞至循环结束。processEvents 强制立即处理待办的重绘事件。
        QApplication.processEvents()

    def _reset_progress(self) -> None:
        """将进度条与状态文字恢复为初始状态。"""
        self.progress_bar.setValue(0)
        self.status_label.setText("就绪")

    def _on_browse_dir(self) -> None:
        """弹出目录选择对话框，将选中路径写入输入框。"""
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择作业目录",
            self.dir_edit.text() or "",
        )

        # 用户取消时返回空字符串，此时不做处理。
        if selected:
            # 统一为正斜杠，与文档中的路径写法保持一致。
            self.dir_edit.setText(selected.replace("\\", "/"))

    def _on_scan(self) -> None:
        """执行扫描并展示结果。

        扫描结果保存至 ``_records``，供改名、归档、字数检查三个功能共用。
        """
        directory = self._current_dir()
        if directory is None:
            return

        extensions = self._parse_extensions()
        recursive = self.recursive_check.isChecked()

        self._log_separator()
        self._log(f"开始扫描：{directory}")
        # 输出生效条件，便于用户确认过滤设置是否正确。
        self._log(f"扩展名过滤：{extensions if extensions else '（不过滤，显示全部）'}")
        self._log(f"包含子文件夹：{'是' if recursive else '否'}")

        # 兜底捕获目录异常：校验与调用之间文件系统状态可能发生变化。
        try:
            self._records = scan_core.scan_directory(
                root=directory,
                extensions=extensions,
                recursive=recursive,
            )
        except NotADirectoryError as exc:
            QMessageBox.critical(self, "扫描失败", str(exc))
            return

        # 把结果画进表格。
        self._render_scan_table()

        # 打印纯文本版本到日志区，方便用户复制。
        self._log("")
        self._log(scan_core.format_records(self._records))

        # 更新汇总标签。
        total_size = sum(r.size_bytes for r in self._records)
        self.scan_summary.setText(
            f"共 {len(self._records)} 个文件，合计 {_human_size(total_size)}"
        )

        # 扫描完成，重置进度条。
        self._reset_progress()

    def _render_scan_table(self) -> None:
        """将 ``_records`` 渲染至需求 1 的表格。"""
        # setRowCount 会清除既有数据，无需手动清空。
        self.scan_table.setRowCount(len(self._records))

        for row, rec in enumerate(self._records):
            cells = [
                str(row + 1),
                rec.name,
                rec.size_human,
                rec.modified_human,
                # 未统计字数时显示 "-"，与"零字"区分。
                str(rec.word_count) if rec.word_count is not None else "-",
            ]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)

                # 序号、大小、字数列右对齐。
                if col in (0, 2, 4):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )

                # 设为只读，避免用户双击修改显示内容。
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                self.scan_table.setItem(row, col, item)

    def _on_rename_target_preset(self, index: int) -> None:
        """把「新文件名格式」下拉框的选中项转换为模板并填入自定义输入框。

        Args:
            index: 下拉框当前选中项索引，由信号传入。
        """
        template = self.rename_target_combo.itemData(index)
        if template:
            self.rename_template_edit.setText(template)

    def _on_rename_template_edited(self, text: str) -> None:
        """模板被手工编辑时，让下拉框选中匹配的预设。

        若编辑后的文本恰好等于某个预设模板，就把下拉框切到那一项；否则保持
        当前选中项不动，避免用户每次输入都触发选中项跳动。

        Args:
            text: 输入框内的当前模板文本。
        """
        for index in range(self.rename_target_combo.count()):
            if self.rename_target_combo.itemData(index) == text:
                # 切换选中项会再次触发 _on_rename_target_preset，但两者文本
                # 相同，不会造成反复写入。此处用 blockSignals 更稳妥。
                self.rename_target_combo.blockSignals(True)
                self.rename_target_combo.setCurrentIndex(index)
                self.rename_target_combo.blockSignals(False)
                break

    def _current_source_format(self) -> rename_core.SourceFormat:
        """读取下拉框选中的原文件名格式。

        Returns:
            字段顺序元组，例如 ``("id", "name", "title")``。
        """
        fmt = self.rename_source_combo.currentData()
        if fmt:
            return tuple(fmt)
        return rename_core.DEFAULT_SOURCE_FORMAT

    def _on_import_roster(self) -> None:
        """从 TXT 文件导入学生信息表，把内容填入文本框。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择学生信息表", "", "文本文件 (*.txt *.csv *.md);;所有文件 (*)"
        )
        if not path:
            return

        try:
            # 先经 core 层读取，以便复用其编码回退逻辑（utf-8-sig → utf-8 → gbk）。
            # 读取成功后把文本回填到界面，用户可在导入基础上继续编辑。
            roster = roster_core.load_roster_file(path)
        except roster_core.RosterError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            self._log(f"【信息表】导入失败：{exc}")
            return

        # 重新读取原始文本以保留注释与格式，仅用于显示。
        text = self._read_text_best_effort(Path(path))
        self.roster_edit.setPlainText(text)
        self._refresh_roster()
        self._log(f"【信息表】已从 {Path(path).name} 导入 {len(roster)} 条记录。")

    def _read_text_best_effort(self, path: Path) -> str:
        """以常见编码尽力读取文本文件内容。

        Args:
            path: 待读取的文件路径。

        Returns:
            文件文本；全部编码均失败时返回空字符串。
        """
        for encoding in ("utf-8-sig", "utf-8", "gbk"):
            try:
                return path.read_text(encoding=encoding)
            except (UnicodeDecodeError, OSError):
                continue
        return ""

    def _on_fill_roster_sample(self) -> None:
        """把内置示例信息表填入文本框。"""
        self.roster_edit.setPlainText(roster_core.SAMPLE_ROSTER_TEXT)
        self._refresh_roster()
        self._log("【信息表】已填入示例内容，可直接修改后使用。")

    def _on_export_roster_sample(self) -> None:
        """把示例信息表另存为 TXT 文件，方便用户按此格式填写。

        需求要求「为 TXT 文件传输时为用户提供一个示例」，本按钮即是该示例的
        出口：用户拿到文件后照着改，再通过「导入 TXT 文件」读回来。
        """
        path, _ = QFileDialog.getSaveFileName(
            self, "导出信息表示例", "学生信息表_示例.txt", "文本文件 (*.txt)"
        )
        if not path:
            return

        try:
            Path(path).write_text(roster_core.SAMPLE_ROSTER_TEXT, encoding="utf-8-sig")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写入文件失败：{exc}")
            return

        self._log(f"【信息表】示例已导出到：{path}")
        QMessageBox.information(
            self,
            "导出成功",
            f"示例信息表已保存到：\n{path}\n\n"
            "用记事本打开，按同样的格式把学号和姓名替换成你的名单即可。",
        )

    def _on_clear_roster(self) -> None:
        """清空信息表，改用「文件名中自带学号姓名」的方式解析。"""
        self.roster_edit.setPlainText("")
        self._refresh_roster()
        self._log("【信息表】已清空，改名时将不使用信息表补全。")

    def _refresh_roster(self) -> None:
        """重新解析文本框内容并更新状态提示。

        文本框内容一有变化即重新解析，因为一份几百行的信息表解析耗时可忽略，
        无需做成按钮触发。
        """
        self._roster = roster_core.parse_roster_text(self.roster_edit.toPlainText())

        if self._roster:
            text = f"已解析 {len(self._roster)} 条记录"
            if self._roster.conflicts:
                text += f"，{len(self._roster.conflicts)} 条重复已忽略"
            self.roster_status_label.setText(text)
            self.roster_status_label.setStyleSheet("color: #1b5e20;")
        else:
            self.roster_status_label.setText("信息表为空，将仅使用文件名中自带的信息")
            self.roster_status_label.setStyleSheet("color: #666;")

    def _on_preview_rename(self) -> None:
        """计算并展示改名计划，不修改任何文件。

        调用只读的 :func:`~homework_organizer.core.rename.calculate_rename_plan`，
        供用户在确认前查看改名结果、不合格项与跳过原因。
        """
        if not self._records:
            QMessageBox.information(self, "提示", "请先点击上方「扫描」按钮。")
            return

        # 每次预览前重新解析信息表，保证「改完文本框直接点预览」也能生效。
        self._refresh_roster()

        template = (
            self.rename_template_edit.text().strip() or rename_core.DEFAULT_TEMPLATE
        )
        source_format = self._current_source_format()

        warning = rename_core.validate_template(template)
        if warning and not warning.startswith("提示："):
            QMessageBox.warning(self, "模板有误", warning)
            self._log(f"【批量改名】模板校验未通过：{warning}")
            return

        # 作业白名单只对「自动识别」有意义：固定排列下作业名由声明的字段位置
        # 决定。若用户在固定排列下填了白名单，提示他这份表不会被使用，避免
        # 他以为填了没生效。
        preferred_titles = self._read_rename_title_whitelist()
        if preferred_titles and tuple(source_format) != rename_core.AUTO_SOURCE_FORMAT:
            QMessageBox.information(
                self,
                "作业白名单未生效",
                "作业白名单只在「自动识别」原格式下生效。\n\n"
                "当前选择的是固定排列格式，作业名由你指定的字段位置决定，"
                "因此白名单暂不参与判断。\n\n"
                "若想使用白名单，请把「原文件名格式」改回「自动识别」。",
            )
            self._log(
                "【批量改名】当前为固定排列格式，作业白名单不参与判断。"
            )

        # 纯计算，不访问磁盘。
        self._rename_plan = rename_core.calculate_rename_plan(
            records=self._records,
            template=template,
            source_format=source_format,
            roster=self._roster if self._roster else None,
            preferred_titles=preferred_titles or None,
        )

        self._render_rename_table(self._rename_plan)

        self._log_separator()
        self._log("【批量改名 · 预览】尚未修改任何文件")
        self._log(f"原文件名格式：{self.rename_source_combo.currentText()}")
        self._log(f"新文件名格式：{template}")
        if self._roster:
            self._log(f"信息表：已启用（{len(self._roster)} 条记录）")
        else:
            self._log("信息表：未启用")
        if preferred_titles and tuple(source_format) == rename_core.AUTO_SOURCE_FORMAT:
            self._log(
                f"作业白名单：已启用（{len(preferred_titles)} 个作业名）——"
                "命中白名单的段将被优先认定为作业名"
            )
        elif not preferred_titles:
            self._log("作业白名单：未启用（按内置特征词规则判断作业名）")
        self._log("")
        self._log(rename_core.format_plan(self._rename_plan))

        # 无待改文件时保持执行按钮禁用，避免用户点击无响应的按钮。
        if self._rename_plan.pending:
            self.apply_rename_btn.setEnabled(True)
            self._log("")
            self._log(
                f"→ 请确认以上 {len(self._rename_plan.pending)} 项改名内容，"
                "确认无误后点击「② 确认执行改名」。"
            )
        else:
            self.apply_rename_btn.setEnabled(False)
            self._log("")
            self._log("→ 没有需要改名的文件，无需执行。")

        if self._rename_plan.invalid:
            QMessageBox.warning(
                self,
                "存在不合格文件",
                f"有 {len(self._rename_plan.invalid)} 个文件的文件名中既无学号也无姓名，"
                "已在表格中标红显示，将不会被改名。\n\n"
                "请检查这些文件，或确认信息表内容是否完整。\n"
                "若它们是作业名不含特征词的文件，可把作业名写进「作业白名单」再试。",
            )

    def _render_rename_table(self, plan: rename_core.RenamePlan) -> None:
        """将改名计划渲染为表格。

        每行为 ``(状态, 原文件名, 新文件名, 识别结果, 判定依据, 底色, 字色)``。
        五种状态用不同颜色区分：待改名为浅绿，无需改动无色，不合格为浅红
        （对应"标红"需求），跳过为浅黄。

        「识别结果」列显示推断出的学号/姓名，让人一眼确认程序认对了没有；
        「判定依据」列写明命中了哪条规则，这是本版新增的标示能力——用户能
        看到"为什么这样判"，而不只是看到结果。

        Args:
            plan: 待渲染的改名计划。
        """
        rows: list[tuple[str, str, str, str, str, QColor | None, QColor | None]] = []

        for action in plan.pending:
            fields, rule = self._describe_action(action)
            rows.append(
                ("将改名", action.old_name, action.new_name, fields, rule,
                 COLOR_PASSED_BG, COLOR_PASSED_FG)
            )

        for action in plan.actions:
            if not action.changed:
                fields, rule = self._describe_action(action)
                rows.append(
                    ("无需改动", action.old_name, action.new_name, fields, rule, None, None)
                )

        # 不合格项标红：学号姓名都缺、或段数内容不符合识别规则的文件。
        for path, reason in plan.invalid:
            rows.append(("不合格", path.name, "—", "—", reason,
                         COLOR_BELOW_THRESHOLD_BG, COLOR_BELOW_THRESHOLD_FG))

        for path, reason in plan.skipped:
            rows.append(("跳过", path.name, "—", "—", reason,
                         COLOR_ERROR_BG, COLOR_ERROR_FG))

        self.rename_table.setRowCount(len(rows))

        for row, data in enumerate(rows):
            status, old_name, new_name, fields, rule, bg_color, fg_color = data
            for col, text in enumerate((status, old_name, new_name, fields, rule)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                # 整行着色，使各种状态易于区分。
                if bg_color is not None:
                    item.setBackground(QBrush(bg_color))
                if fg_color is not None:
                    item.setForeground(QBrush(fg_color))

                self.rename_table.setItem(row, col, item)

    def _describe_action(
        self, action: rename_core.RenameAction
    ) -> tuple[str, str]:
        """为一条改名动作生成「识别结果」与「判定依据」两列的文本。

        Args:
            action: 待描述的改名动作。

        Returns:
            二元组 ``(识别结果, 判定依据)``。识别结果为形如
            ``学号 20230001 · 姓名 张三`` 的文本；判定依据为命中的规则说明，
            例如 ``[数字+文本] 数字段判为学号，文本段判为作业名``。
        """
        parts: list[str] = []
        if action.parsed_id:
            parts.append(f"学号 {action.parsed_id}")
        if action.parsed_name:
            parts.append(f"姓名 {action.parsed_name}")

        fields = " · ".join(parts) if parts else "—"

        # fill_note 在自动识别模式下形如「[规则名] 说明；补全说明」，
        # 在固定排列下形如「20230001 → 张三」。直接整条展示即可。
        rule = action.fill_note or "—"
        return fields, rule

    def _on_apply_rename(self) -> None:
        """执行改名计划，实际修改文件名。

        执行前弹出确认对话框，作为"确认后才执行"的最后一道保障。
        """
        # 双重校验：即使按钮被误启用，仍在此检查计划是否存在。
        if self._rename_plan is None:
            QMessageBox.warning(self, "提示", "请先点击「① 预览改名结果」。")
            return

        pending = self._rename_plan.pending
        if not pending:
            QMessageBox.information(self, "提示", "没有需要改名的文件。")
            return

        # 展示前若干条改名示例，供用户最终确认。
        preview_lines = []
        for action in pending[:5]:
            preview_lines.append(f"  {action.old_name}  →  {action.new_name}")
        if len(pending) > 5:
            preview_lines.append(f"  ...（还有 {len(pending) - 5} 个）")

        message = (
            f"即将修改 {len(pending)} 个文件的名称：\n\n"
            + "\n".join(preview_lines)
            + "\n\n这个操作会真的修改磁盘上的文件名，确定继续吗？\n"
            "（如果结果不符预期，可以关闭程序后手动改回，或在归档前先备份）"
        )

        # 有不合格文件时在确认框里再提醒一次，避免用户误以为它们也会被改。
        if self._rename_plan.invalid:
            message += (
                f"\n\n注意：另有 {len(self._rename_plan.invalid)} 个文件因文件名中"
                "既无学号也无姓名而不会被改名。"
            )

        answer = QMessageBox.question(
            self,
            "确认执行改名",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            # 默认按钮为 No，防止误按回车直接执行。
            QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            self._log("")
            self._log("【批量改名】用户取消了操作，未修改任何文件。")
            return

        self._log_separator()
        self._log(f"【批量改名 · 执行】共 {len(pending)} 个文件")
        self._log("")

        succeeded, failed = rename_core.apply_rename_plan(
            self._rename_plan,
            progress=self._set_progress,
        )

        for action in succeeded:
            self._log(f"  ✓ {action.old_name}  →  {action.new_name}")

        if failed:
            self._log("")
            self._log(f"【失败 {len(failed)} 个】")
            for path, reason in failed:
                self._log(f"  ✗ {path.name}")
                self._log(f"      {reason}")

        self._log("")
        self._log(f"改名完成：成功 {len(succeeded)} 个，失败 {len(failed)} 个。")

        # 清空计划，防止用户连续点击"确认执行"导致第二次全部失败。
        self._rename_plan = None
        self.apply_rename_btn.setEnabled(False)

        # 重新扫描以刷新表格中的文件名。
        self._on_scan()

        self._reset_progress()

        QMessageBox.information(
            self,
            "改名完成",
            f"成功改名 {len(succeeded)} 个文件。\n"
            + (f"失败 {len(failed)} 个，详见下方日志。" if failed else ""),
        )

    def _on_archive(self) -> None:
        """执行归档，按选定方式将文件移动至子文件夹。"""
        if not self._records:
            QMessageBox.information(self, "提示", "请先点击上方「扫描」按钮。")
            return

        directory = self._current_dir()
        if directory is None:
            return

        # currentData 取 addItem 时存入的程序内部取值。
        mode = self.archive_mode_combo.currentData()
        mode_label = self.archive_mode_combo.currentText()

        # 按学期归档时才需要区间；区间文本有误就直接拦下，不进入确认流程。
        semester_ranges: list[tuple[str, str, str]] = []
        if mode == "semester":
            ranges, error = self._read_semester_ranges()
            if error:
                QMessageBox.warning(self, "学期区间有误", error)
                return
            if not ranges:
                QMessageBox.warning(
                    self,
                    "学期区间为空",
                    "按学期归档需要至少一条学期区间。\n"
                    "请展开「学期区间设置」填写，或点「填入默认区间」。",
                )
                return
            semester_ranges = ranges

        message = (
            f"即将按「{mode_label}」把 {len(self._records)} 个文件\n"
            f"移动到 {directory} 下的子文件夹中。\n\n"
            "如果结果不符合预期，可以使用「撤销上次操作」恢复。\n"
            "确定继续吗？"
        )
        answer = QMessageBox.question(
            self,
            "确认执行归档",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            # 默认按钮为 No，防止误操作。
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._log("")
            self._log("【归档】用户取消了操作。")
            return

        self._log_separator()
        self._log(f"【归档 · 执行】方式：{mode_label}")

        if mode == "semester":
            self._log(f"学期区间：共 {len(semester_ranges)} 条")
            for name, start, end in semester_ranges:
                self._log(f"    {name}：{start} ~ {end}")

        result = archive_core.archive(
            records=self._records,
            root=directory,
            mode=mode,
            semester_ranges=semester_ranges or None,
            progress=self._set_progress,
        )

        report = archive_core.build_report(
            result=result,
            root=directory,
            extra_info={"归档方式": mode_label},
            mode=mode,
        )

        self.report_view.setPlainText(report)
        self._log(report)

        # 报告存盘留档；存盘失败不影响归档结果，仅提示。
        try:
            saved_path = archive_core.save_report(report, directory)
            self._log("")
            self._log(f"报告已保存至：{saved_path}")
        except OSError as exc:
            self._log(f"报告保存失败（不影响归档结果）：{exc}")

        self._reset_progress()

        # 若存在未归类文件，在弹窗里点明，引导用户补充区间。
        unclassified_count = sum(
            1 for action in result.moved
            if action.category == archive_core.UNCLASSIFIED_NAME
        )
        tip = ""
        if unclassified_count:
            tip = (
                f"\n\n其中 {unclassified_count} 个文件的修改时间不落在任何学期区间内，"
                "已归入「未归类」。报告里列出了它们的修改时间，"
                "可据此补充「学期区间设置」后重新归档。"
            )

        QMessageBox.information(
            self,
            "归档完成",
            f"成功移动 {len(result.moved)} 个文件，跳过 {len(result.skipped)} 个。"
            f"{tip}\n\n详细报告见界面下方。",
        )

        # 归档后文件位置已变更，重新扫描以刷新表格。
        self._on_scan()

    def _read_semester_ranges(self) -> tuple[list[tuple[str, str, str]], str]:
        """读取界面上的学期区间文本并解析。

        Returns:
            二元组 ``(区间列表, 错误信息)``。解析成功时错误信息为空字符串；
            失败时区间列表为空列表，错误信息为可直接展示给用户的提示文本。
        """
        text = self.semester_edit.toPlainText()

        try:
            ranges = archive_core.parse_semester_ranges_text(text)
        except ValueError as exc:
            return [], str(exc)

        return ranges, ""

    def _on_fill_semester_default(self) -> None:
        """把学期区间文本框恢复为程序内置的默认区间。"""
        self.semester_edit.setPlainText(self._default_semester_text())
        self.semester_status_label.setText("已填入默认区间")
        self.semester_status_label.setStyleSheet("color: #1b5e20;")

    def _on_validate_semester(self) -> None:
        """校验学期区间文本，把结果显示在状态标签上。"""
        ranges, error = self._read_semester_ranges()

        if error:
            self.semester_status_label.setText("校验未通过")
            self.semester_status_label.setStyleSheet("color: #c00000;")
            QMessageBox.warning(self, "学期区间有误", error)
            return

        if not ranges:
            self.semester_status_label.setText("区间为空")
            self.semester_status_label.setStyleSheet("color: #8d6e00;")
            QMessageBox.information(
                self, "学期区间为空", "当前没有任何有效区间，请先填写或点「填入默认区间」。"
            )
            return

        self.semester_status_label.setText(f"校验通过，共 {len(ranges)} 条")
        self.semester_status_label.setStyleSheet("color: #1b5e20;")

        lines = ["学期区间校验通过，共 %d 条：" % len(ranges)]
        for name, start, end in ranges:
            lines.append(f"  {name}：{start} ~ {end}")
        QMessageBox.information(self, "校验通过", "\n".join(lines))

    def _on_undo(self) -> None:
        """撤销最近一次归档，将文件移回原位置。"""
        directory = self._current_dir()
        if directory is None:
            return

        # 无历史记录时不进入确认流程。
        history = archive_core.list_history(directory)
        if not history:
            QMessageBox.information(
                self,
                "无可撤销的操作",
                "这个目录下没有找到操作记录。\n"
                "（只有执行过归档操作后才会产生记录）",
            )
            return

        # 展示最近一次操作的摘要，供用户确认撤销对象。
        last = history[0]
        message = (
            f"将撤销这次操作（{last.timestamp}），\n"
            f"涉及 {len(last.actions)} 个文件，把它们从子文件夹搬回原位置。\n\n"
            "确定继续吗？"
        )
        answer = QMessageBox.question(
            self,
            "确认撤销",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._log("")
            self._log("【撤销】用户取消了操作。")
            return

        self._log_separator()
        self._log("【撤销 · 执行】")

        result = archive_core.undo_last(directory, progress=self._set_progress)

        report = archive_core.format_undo_result(result)
        self.report_view.setPlainText(report)
        self._log(report)

        self._reset_progress()

        QMessageBox.information(
            self,
            "撤销完成",
            f"已还原 {len(result.restored)} 个文件，失败 {len(result.failed)} 个。",
        )

        self._on_scan()

    def _on_show_history(self) -> None:
        """列出全部历史归档记录。"""
        directory = self._current_dir()
        if directory is None:
            return

        history = archive_core.list_history(directory)

        self._log_separator()
        self._log("【操作历史】")

        if not history:
            self._log("（无历史记录）")
            return

        # history 已按时间从新到旧排列。
        for i, entry in enumerate(history, start=1):
            self._log(f"  {i:>2}. {entry.timestamp}  |  "
                      f"{entry.operation}  |  {len(entry.actions)} 个文件")

    def _on_wordcount(self) -> None:
        """执行作业检查：统计字数 + 按检索信息汇总。

        该方法是作业检查功能的主入口，同时驱动三处呈现：

            1. 汇总表   —— 每条检索信息的识别/达标/未达标/读取失败份数
            2. 明细表   —— 逐文件的字数与标红效果
            3. 徽标与提示 —— 全绿给绿勾，存在标红则弹窗点名
        """
        if not self._records:
            QMessageBox.information(self, "提示", "请先点击上方「扫描」按钮。")
            return

        threshold = self.threshold_spin.value()
        keywords = self._read_check_info()

        self._log_separator()

        # 阈值为 0 表示不限制，明确指出以免用户误解为标红功能失效。
        if threshold <= 0:
            self._log("【作业检查】字数要求为 0，表示不限制，所有文件都会被判为达标。")
        else:
            self._log(f"【作业检查】字数要求：不低于 {threshold} 字")

        if keywords:
            self._log(f"检索信息：共 {len(keywords)} 条")
        else:
            self._log("检索信息为空，本次只输出逐文件明细，不生成汇总表。")

        self._log("正在读取文档并统计字数，文档较多时请稍候...")
        self._log("")

        # 传入进度回调，使等待期间界面有反馈。
        results, skipped = wordcount_core.scan_documents_wordcount(
            records=self._records,
            threshold=threshold,
            progress=self._set_progress,
        )

        # 将统计结果回填至 _records，使需求 1 的表格也能显示字数。
        count_by_path = {str(r.path): r.count for r in results}
        for rec in self._records:
            key = str(rec.path)
            if key in count_by_path:
                rec.word_count = count_by_path[key]

        self._wordcount_results = results
        self._wordcount_skipped = skipped
        self._wordcount_threshold = threshold

        # 检索信息为空时不做汇总，避免渲染出一张空表。
        summary = (
            wordcount_core.build_homework_check(results, keywords, threshold=threshold)
            if keywords
            else None
        )
        self._check_summary = summary

        # 标红效果在渲染表格时产生。
        self._render_check_summary_table(summary)
        self._render_wordcount_table(results, threshold)

        below_count = sum(1 for r in results if r.is_ok and r.below_threshold)
        failed_count = sum(1 for r in results if not r.is_ok)

        # 徽标优先反映汇总结论：有汇总时看"逐条是否通过"，无汇总时退回看明细。
        if summary is not None:
            if summary.all_green:
                self.wordcount_badge.setText("✓ 全部通过")
                self.wordcount_badge.setStyleSheet(
                    "font-weight: bold; color: white; background-color: #1b5e20;"
                    "padding: 4px 10px; border-radius: 4px;"
                )
            else:
                red_count = len(summary.red_rows)
                self.wordcount_badge.setText(f"⚠ 未通过 {red_count} 条")
                self.wordcount_badge.setStyleSheet(
                    "font-weight: bold; color: white; background-color: #c00000;"
                    "padding: 4px 10px; border-radius: 4px;"
                )
        elif below_count > 0:
            # 存在不合格文件时以红色标识。
            self.wordcount_badge.setText(f"⚠ 低于要求：{below_count} 个")
            self.wordcount_badge.setStyleSheet(
                "font-weight: bold; color: white; background-color: #c00000;"
                "padding: 4px 10px; border-radius: 4px;"
            )
        else:
            self.wordcount_badge.setText("✓ 全部达标")
            self.wordcount_badge.setStyleSheet(
                "font-weight: bold; color: white; background-color: #1b5e20;"
                "padding: 4px 10px; border-radius: 4px;"
            )

        self._log(
            f"检查完成：共 {len(results)} 个文件，"
            f"达标 {len(results) - below_count - failed_count} 个，"
            f"低于要求 {below_count} 个，"
            f"统计失败 {failed_count} 个。"
        )

        # 存在标红项时弹窗提示，并给出三种常见原因，避免用户无从下手。
        if summary is not None and summary.red_rows:
            red_names = "、".join(row.keyword for row in summary.red_rows)
            self._log("")
            self._log(f"⚠ 以下检索信息未通过（达标份数为 0）：{red_names}")
            QMessageBox.warning(
                self,
                "检查未通过",
                f"以下检索信息没有任何一份达标：\n\n{red_names}\n\n"
                "常见原因有三种：\n"
                "  1. 该信息没有出现在任何文件名里；\n"
                "  2. 文件名里没有该信息对应的文档；\n"
                "  3. 文件名里的那一段与检索信息不完全一致（多字、少字或含空格）。\n\n"
                "请核对「检索信息」与文件名后重试。",
            )

        if below_count > 0:
            self._log("")
            self._log("以下文件字数不足，已在明细表中标红：")
            for r in results:
                if r.is_ok and r.below_threshold:
                    gap = threshold - (r.count or 0)
                    self._log(f"  ⚠ {r.path.name}  （{r.count} 字，还差 {gap} 字）")

        # 刷新需求 1 的表格以显示字数。
        self._render_scan_table()
        self._reset_progress()

    def _render_check_summary_table(
        self,
        summary: wordcount_core.HomeworkCheckSummary | None,
    ) -> None:
        """把汇总结果渲染至汇总表，并按达标情况整行标色。

        与明细表一样，背景色与前景色必须**成对设置**，否则深色主题下会出现
        深色文字配深色背景、内容无法辨认的问题。

        判定条件只有一条：达标份数 ≥ 1 标绿，否则标红。读取失败列单独呈现，
        不参与标色判定。

        Args:
            summary: 汇总结果；为 ``None`` 时清空表格并说明原因。
        """
        if summary is None:
            self.check_summary_table.setRowCount(0)
            self.check_summary_note.setText(
                "检查汇总：未填写检索信息，本次不生成汇总表"
            )
            self.check_summary_note.setStyleSheet(
                "font-weight: bold; color: #666;"
            )
            return

        rows = summary.rows
        self.check_summary_table.setRowCount(len(rows))

        for row_index, row_data in enumerate(rows):
            # 绿标行用绿色系，标红行用红色系，两者必须成对设置。
            if row_data.is_green:
                bg = COLOR_PASSED_BG
                fg = COLOR_PASSED_FG
            else:
                bg = COLOR_BELOW_THRESHOLD_BG
                fg = COLOR_BELOW_THRESHOLD_FG

            cells = [
                row_data.keyword,
                str(row_data.total),
                str(row_data.passed),
                str(row_data.below),
                str(row_data.failed),
            ]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setBackground(QBrush(bg))
                item.setForeground(QBrush(fg))

                # 数字列右对齐，方便纵向比较大小。
                if col > 0:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )

                self.check_summary_table.setItem(row_index, col, item)

        # 表头右侧给出总体结论：全绿给勾，否则点名到具体信息。
        if summary.all_green:
            self.check_summary_note.setText("检查汇总：✓ 全部通过")
            self.check_summary_note.setStyleSheet(
                "font-weight: bold; color: #1b5e20;"
            )
        else:
            red_names = "、".join(row.keyword for row in summary.red_rows)
            self.check_summary_note.setText(f"检查汇总：⚠ 未通过：{red_names}")
            self.check_summary_note.setStyleSheet(
                "font-weight: bold; color: #c00000;"
            )

    def _render_wordcount_table(
        self,
        results: list[wordcount_core.WordCountResult],
        threshold: int,
    ) -> None:
        """将字数统计结果渲染至表格，并标记低于阈值的文件。

        标红通过为每个单元格同时设置 ``setBackground`` 与 ``setForeground``
        实现。两者必须成对设置：仅设背景色时，深色主题下深色文字与深色背景
        将难以辨认。

        三种状态对应三套配色：
            - 低于阈值：红底红字，需要退回处理。
            - 统计失败：黄底黄字，需要人工检查，但不属于内容不足。
            - 达标：绿底绿字，正常。

        Args:
            results: 统计结果序列。
            threshold: 字数下限，用于计算差值。
        """
        self.wordcount_table.setRowCount(len(results))

        for row, result in enumerate(results):
            if not result.is_ok:
                # 统计失败，标黄。
                bg = COLOR_ERROR_BG
                fg = COLOR_ERROR_FG
                status_text = "统计失败"
                note = result.error
                count_text = "-"
            elif result.below_threshold:
                # 低于阈值，标红。
                bg = COLOR_BELOW_THRESHOLD_BG
                fg = COLOR_BELOW_THRESHOLD_FG
                status_text = "★ 字数不足"
                # 给出与阈值的差值，比单纯标注"不足"更具参考价值。
                gap = threshold - (result.count or 0)
                note = f"低于要求 {threshold} 字，还差 {gap} 字"
                count_text = str(result.count)
            else:
                # 达标，标绿。
                bg = COLOR_PASSED_BG
                fg = COLOR_PASSED_FG
                status_text = "达标"
                note = f"达到要求（{threshold} 字）"
                count_text = str(result.count)

            cells = [result.path.name, count_text, status_text, note]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                # 背景色与前景色同时设置，实现整行标色。
                item.setBackground(QBrush(bg))
                item.setForeground(QBrush(fg))

                # 不合格行加粗，使其在长列表中更醒目。
                if result.is_ok and result.below_threshold:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)

                # 数字右对齐。
                if col == 1:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )

                self.wordcount_table.setItem(row, col, item)

        # 不按不合格优先重排：保持与扫描列表一致的顺序（按提交时间）更便于
        # 对照查看，且标红已足够醒目。

    def _on_wordcount_report(self) -> None:
        """生成并展示完整的作业检查报告。

        依赖 :meth:`_on_wordcount` 先行执行以获取统计结果；若已生成汇总，
        报告会一并带上「按检索信息汇总」区块。
        """
        if not self._wordcount_results:
            QMessageBox.information(
                self,
                "提示",
                "请先点击「① 开始作业检查」，生成结果后才能查看报告。",
            )
            return

        directory = self._current_dir()

        report = wordcount_core.build_wordcount_report(
            results=self._wordcount_results,
            threshold=self._wordcount_threshold,
            root=directory,
            summary=self._check_summary,
        )

        self._log_separator()
        self._log(report)

        # 同时存盘留档，复用归档模块的报告保存机制。
        if directory is not None:
            try:
                from datetime import datetime

                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                saved = archive_core.save_report(
                    report,
                    directory,
                    filename=f"作业检查报告-{stamp}.txt",
                )
                self._log("")
                self._log(f"报告已保存至：{saved}")
            except OSError as exc:
                self._log(f"报告保存失败（不影响结果查看）：{exc}")

    # -----------------------------------------------------------------------
    # 检索信息输入区的四个按钮
    # -----------------------------------------------------------------------
    def _on_import_check_info(self) -> None:
        """从 TXT 文件导入检索信息。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择检索信息文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return

        text = self._read_text_best_effort(Path(path))
        if not text.strip():
            QMessageBox.warning(
                self,
                "文件为空",
                f"该文件没有可读取的内容：\n{path}\n\n"
                "请确认文件编码为 UTF-8 或 GBK。",
            )
            return

        self.check_info_edit.setPlainText(text)
        count = len(self._read_check_info())
        self._log(f"已导入检索信息：{path}（{count} 条）")

    def _on_fill_check_info_sample(self) -> None:
        """把内置示例填入检索信息输入框。"""
        self.check_info_edit.setPlainText(wordcount_core.SAMPLE_CHECK_INFO_TEXT)
        self._log("已填入检索信息示例。")

    def _on_export_check_info_sample(self) -> None:
        """把当前检索信息导出为 TXT 文件。"""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出检索信息",
            "检索信息.txt",
            "文本文件 (*.txt)",
        )
        if not path:
            return

        try:
            Path(path).write_text(
                self.check_info_edit.toPlainText(), encoding="utf-8"
            )
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：\n{exc}")
            return

        self._log(f"检索信息已导出至：{path}")

    def _on_clear_check_info(self) -> None:
        """清空检索信息输入框。"""
        self.check_info_edit.setPlainText("")
        self._log("已清空检索信息。")

    # -----------------------------------------------------------------------
    # 标签页五：作业归类（新增功能）
    # -----------------------------------------------------------------------
    def _on_import_title_whitelist(self) -> None:
        """从 TXT 文件导入关键词表。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择关键词表文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return

        text = self._read_text_best_effort(Path(path))
        if not text.strip():
            QMessageBox.warning(
                self, "导入失败", "文件内容为空或无法识别编码（支持 UTF-8 / GBK）。"
            )
            return

        self.titlesort_edit.setPlainText(text)
        self._refresh_title_count()
        self._log(f"【作业归类】已导入关键词表：{path}")

    def _on_fill_title_sample(self) -> None:
        """把内置示例关键词表填入文本框。"""
        self.titlesort_edit.setPlainText(archive_core.SAMPLE_KEYWORD_LIST_TEXT)
        self._refresh_title_count()
        self._log("【作业归类】已填入示例关键词表。")

    def _on_export_title_sample(self) -> None:
        """把示例关键词表另存为 TXT 文件，方便用户照着填写。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出关键词表示例", "关键词表_示例.txt", "文本文件 (*.txt)"
        )
        if not path:
            return

        try:
            Path(path).write_text(
                archive_core.SAMPLE_KEYWORD_LIST_TEXT, encoding="utf-8-sig"
            )
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写入文件失败：{exc}")
            return

        self._log(f"【作业归类】示例已导出到：{path}")
        QMessageBox.information(
            self,
            "导出成功",
            f"示例关键词表已保存到：\n{path}\n\n"
            "用记事本打开，每行改成你的一个关键词即可\n"
            "（作业名、姓名、学号都可以写）。",
        )

    def _on_import_rename_title_whitelist(self) -> None:
        """从 TXT 文件导入需求 2 的作业白名单。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择作业白名单文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return

        text = self._read_text_best_effort(Path(path))
        if not text.strip():
            QMessageBox.warning(
                self, "导入失败", "文件内容为空或无法识别编码（支持 UTF-8 / GBK）。"
            )
            return

        self.rename_title_edit.setPlainText(text)
        self._refresh_rename_title_count()
        self._log(f"【批量改名】已导入作业白名单：{path}")

    def _on_fill_rename_title_sample(self) -> None:
        """把内置示例作业白名单填入需求 2 的文本框。"""
        self.rename_title_edit.setPlainText(
            archive_core.SAMPLE_TITLE_WHITELIST_TEXT
        )
        self._refresh_rename_title_count()
        self._log("【批量改名】已填入示例作业白名单。")

    def _on_export_rename_title_sample(self) -> None:
        """把示例作业白名单另存为 TXT 文件，方便用户照着填写。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出作业白名单示例", "作业白名单_示例.txt", "文本文件 (*.txt)"
        )
        if not path:
            return

        try:
            Path(path).write_text(
                archive_core.SAMPLE_TITLE_WHITELIST_TEXT, encoding="utf-8-sig"
            )
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"写入文件失败：{exc}")
            return

        self._log(f"【批量改名】作业白名单示例已导出到：{path}")
        QMessageBox.information(
            self,
            "导出成功",
            f"示例作业白名单已保存到：\n{path}\n\n"
            "用记事本打开，每行改成你的一个作业名即可。",
        )

    def _on_clear_rename_title_whitelist(self) -> None:
        """清空需求 2 的作业白名单，恢复为按内置规则判断。"""
        self.rename_title_edit.setPlainText("")
        self._refresh_rename_title_count()
        self._log("【批量改名】已清空作业白名单，将按内置特征词规则判断作业名。")

    def _read_rename_title_whitelist(self) -> list[str]:
        """读取需求 2 的作业白名单。

        Returns:
            作业名列表；表为空时返回空列表（调用方据此走内置规则）。
        """
        return archive_core.parse_title_whitelist_text(
            self.rename_title_edit.toPlainText()
        )

    def _read_title_whitelist(self) -> list[str]:
        """读取界面上的关键词表。

        Returns:
            关键词列表；表为空时返回空列表。按段归类没有表就无法工作，
            因此调用方需自行判断空表并提示用户。
        """
        return archive_core.parse_keyword_list_text(
            self.titlesort_edit.toPlainText()
        )

    def _on_titlesort_preview(self) -> None:
        """预览归类结果，填充预览表但不移动任何文件。

        与需求二同构：预览阶段只读，不碰磁盘；执行阶段另行确认。
        """
        if not self._records:
            QMessageBox.information(self, "提示", "请先点击上方「扫描」按钮。")
            return

        keywords = self._read_title_whitelist()
        if not keywords:
            QMessageBox.information(
                self,
                "请先填写关键词表",
                "「作业归类」完全依据关键词表判断归到哪个文件夹，\n"
                "表为空时无法进行任何归类。\n\n"
                "请在上方「关键词表」中每行填写一个关键词\n"
                "（作业名、姓名、学号都可以），再点预览。",
            )
            return

        rows: list[tuple[str, str, str, str, QColor | None, QColor | None]] = []

        for record in self._records:
            path = record.path
            # 用与执行阶段完全相同的分类函数，保证「预览所见」＝「执行所得」。
            matched, _reason = archive_core.classify_by_segments(path, keywords)

            if not matched:
                rows.append(
                    (
                        "未命中",
                        path.name,
                        "—",
                        "（不移动）",
                        COLOR_ERROR_BG,
                        COLOR_ERROR_FG,
                    )
                )
                continue

            # 命中的段用「、」连接展示；多命中时在末尾标注份数，提示会产生副本。
            hit_text = "、".join(matched)
            if len(matched) > 1:
                hit_text = f"{hit_text}（{len(matched)} 个）"

            target_text = "、".join(f"{item}/" for item in matched)
            rows.append(
                (
                    "将归类",
                    path.name,
                    hit_text,
                    target_text,
                    COLOR_PASSED_BG,
                    COLOR_PASSED_FG,
                )
            )

        self._titlesort_rows = rows
        self._render_titlesort_table(rows)

        movable = sum(1 for row in rows if row[0] == "将归类")
        blocked = sum(1 for row in rows if row[0] != "将归类")
        # 多命中会产生副本，把副本总数也算出来，让用户对磁盘占用有预期。
        copies_total = 0
        for record in self._records:
            matched, _ = archive_core.classify_by_segments(record.path, keywords)
            if len(matched) > 1:
                copies_total += len(matched) - 1

        status = f"将归类 {movable} 个 | 未命中 {blocked} 个"
        if copies_total:
            status += f" | 将产生副本 {copies_total} 份"
        self.titlesort_status_label.setText(status)
        self.titlesort_status_label.setStyleSheet("color: #1565c0; padding: 4px 10px;")

        # 预览完成后才放开执行按钮——与需求二同一条安全约束。
        self.titlesort_apply_btn.setEnabled(movable > 0)

        self._log_separator()
        self._log("【作业归类 · 预览】")
        self._log(f"关键词表：{len(keywords)} 个 —— {'、'.join(keywords)}")
        self._log(f"将归类：{movable} 个；未命中：{blocked} 个")
        if copies_total:
            self._log(
                f"注意：有文件命中多个关键词，将额外产生 {copies_total} 份副本"
                "（占用额外磁盘空间）。"
            )
        self._log("（当前仅为预览，未移动任何文件。确认无误后点「② 确认执行归类」。）")

    def _render_titlesort_table(
        self,
        rows: list[tuple[str, str, str, str, QColor | None, QColor | None]],
    ) -> None:
        """把归类预览数据渲染进表格。

        Args:
            rows: 行数据，元素为 ``(状态, 文件名, 命中的段, 目标文件夹, 底色, 字色)``。
                色彩为 ``None`` 时使用默认配色。
        """
        table = self.titlesort_table
        table.setRowCount(len(rows))

        for row_index, (status, name, hit, target, bg, fg) in enumerate(rows):
            values = [status, name, hit, target]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if bg is not None:
                    item.setBackground(QBrush(bg))
                if fg is not None:
                    item.setForeground(QBrush(fg))
                table.setItem(row_index, col, item)

    def _on_titlesort_apply(self) -> None:
        """执行归类：按文件名中命中的段把文件放进同名子文件夹。

        命中多个段时，文件本体会放进第一个命中项，其余命中项各放一份副本。
        """
        if not getattr(self, "_titlesort_rows", None):
            QMessageBox.information(
                self, "提示", "请先点击「① 预览归类结果」，确认后再执行。"
            )
            return

        directory = self._current_dir()
        if directory is None:
            return

        keywords = self._read_title_whitelist()
        if not keywords:
            QMessageBox.information(
                self, "请先填写关键词表", "关键词表为空，无法执行归类。"
            )
            return

        movable = sum(1 for row in self._titlesort_rows if row[0] == "将归类")
        if movable == 0:
            QMessageBox.information(
                self,
                "无可归类的文件",
                "本次没有任何文件命中关键词表，未执行任何操作。",
            )
            return

        # 先算一遍副本总数，写进确认框——副本会真实占用磁盘空间，
        # 这件事必须在用户点头之前说清楚。
        copies_total = 0
        for record in self._records:
            matched, _ = archive_core.classify_by_segments(record.path, keywords)
            if len(matched) > 1:
                copies_total += len(matched) - 1

        confirm_text = (
            f"即将把 {movable} 个文件按命中的关键词\n"
            f"移动到 {directory} 下的同名子文件夹中。\n\n"
            "文件名不会被修改。\n"
        )
        if copies_total:
            confirm_text += (
                f"\n注意：有文件同时命中多个关键词，将额外产生 {copies_total} 份"
                "副本（占用额外磁盘空间）。\n"
            )
        confirm_text += (
            "\n如果结果不符合预期，可以使用「③ 撤销上次操作」恢复"
            "（副本会一并清理）。\n\n确定继续吗？"
        )

        answer = QMessageBox.question(
            self,
            "确认执行归类",
            confirm_text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._log("【作业归类】用户取消了操作。")
            return

        # 只把「至少命中一段」的文件交给归档层。全都没命中的文件不进归档
        # 流程——它们本来就不会被移动，归档层不必再处理一遍。
        records = [
            record
            for record in self._records
            if archive_core.classify_by_segments(record.path, keywords)[0]
        ]

        result = archive_core.archive(
            records=records,
            root=directory,
            mode="segment",
            keywords=keywords,
            progress=self._set_progress,
        )

        report = archive_core.build_report(
            result=result,
            root=directory,
            extra_info={"整理方式": "按段归类（逐段匹配关键词表）"},
            mode="segment",
        )

        self.report_view.setPlainText(report)
        self._log_separator()
        self._log("【作业归类 · 执行】")
        self._log(report)

        try:
            saved_path = archive_core.save_report(report, directory)
            self._log("")
            self._log(f"报告已保存至：{saved_path}")
        except OSError as exc:
            self._log(f"报告保存失败（不影响归类结果）：{exc}")

        self._reset_progress()

        copied = sum(len(action.copies) for action in result.moved)
        done_text = (
            f"成功归类 {len(result.moved)} 个文件"
            f"（并生成 {copied} 份副本）。\n"
        )
        if result.skipped:
            done_text += f"跳过 {len(result.skipped)} 个（未命中关键词，仍留在原处）。\n"
        done_text += "详细报告见「需求3 · 归档报告」页或界面下方输出区。"
        QMessageBox.information(self, "归类完成", done_text)

        # 归类后文件位置已变更，重新扫描并失效旧的预览结果。
        self._titlesort_rows = []
        self.titlesort_apply_btn.setEnabled(False)
        self._on_scan()


# ---------------------------------------------------------------------------
# 模块级工具函数
# ---------------------------------------------------------------------------
def _human_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读的文本。

    与 ``ScanRecord.size_human`` 的区别在于：该属性格式化单个文件的大小，
    而本函数用于格式化多个文件求和后的总大小，故需独立实现。

    Args:
        size_bytes: 字节数。

    Returns:
        格式化后的大小文本，例如 ``"4.2 MB"``。
    """
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"

