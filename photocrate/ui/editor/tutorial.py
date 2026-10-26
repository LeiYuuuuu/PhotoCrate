"""修图新手教程：在修图窗口上盖一层半透明遮罩，一步一步把要介绍的区域“照亮”，旁边配说明卡片。

第一次打开修图窗口时自动开始（设置项 editor_tour_done），以后从「帮助 → 新手教程」重看。
← → 或按钮翻页，Esc 跳过。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import theme
from ..theme import font
from ..widgets.deco import TacButton
from ...i18n import t as _t

# (目标, 标题, 说明)。目标是 EditorWindow 上的区域名，None = 不照亮任何区域。
STEPS: list[tuple[str | None, str, str]] = [
    (None, _t("欢迎使用修图"),
     _t("这里可以像 Photoshop 一样修照片：调色、去瑕疵、抠图、加字、裁剪……<br><br>"
     "<b>放心大胆地试</b>：原片永远不会被修改，改坏了按 <b>Ctrl+Z</b> 撤销就行。<br>"
     "花一分钟看看各个功能在哪里吧。")),
    ("toolbar", _t("① 工具栏"),
     _t("左边是所有工具，鼠标停在图标上会显示名字和用法。最常用的几个：<br>"
     "• <b>移动 V</b>：拖动图层<br>"
     "• <b>选框 M / 套索 L / 对象选择 W</b>：圈出要单独处理的地方<br>"
     "• <b>裁剪 C</b>：裁掉多余部分、拉直歪掉的地平线<br>"
     "• <b>污点修复 J</b>：涂一下就能去掉痘痘、灰尘、电线<br>"
     "• <b>画笔 B / 文字 T</b>：画画、加字<br>"
     "最下面的两个方块是<b>前景色 / 背景色</b>，单击修改，按 X 交换。")),
    ("options", _t("② 选项栏"),
     _t("选中一个工具后，它的设置都在这一条里：画笔的<b>大小、硬度、不透明度</b>，"
     "裁剪的<b>比例</b>和<b>拉直角度</b>，魔棒的<b>容差</b>……<br><br>"
     "小技巧：用画笔时按 <b>[</b> 和 <b>]</b> 可以直接调大小。")),
    ("canvas", _t("③ 画布"),
     _t("中间是你的照片。<br>"
     "• <b>滚轮</b>：以鼠标为中心放大缩小<br>"
     "• <b>按住空格拖动</b>：移动画面<br>"
     "• <b>Ctrl+0</b> 适合窗口，<b>Ctrl+1</b> 看 100% 细节<br>"
     "• <b>按住 \\ 键</b>（或右下角的按钮）：临时看原图，对比修了多少")),
    ("props", _t("④ 属性：调色在这里"),
     _t("最常用的调色方法：点图层面板底部的<b>调整图层按钮</b>（半黑半白的圆），或菜单「图像 → 调整」，新建一个<b>调整图层</b>，"
     "然后在这里拖<b>曝光、对比度、高光、阴影、色温、曲线、HSL</b>……<br><br>"
     "顶部的<b>「套用预设」</b>可以一键套用胶片、日系、电影感等效果，再慢慢微调。<br>"
     "调整图层不改像素，随时可以回来改、隐藏或删掉。")),
    ("layers", _t("⑤ 图层"),
     _t("每一次修改最好放在单独的图层上，互不影响：<br>"
     "• <b>眼睛</b>：显示 / 隐藏这一层，方便对比<br>"
     "• 上方的<b>混合模式</b>和<b>不透明度</b>：控制这一层的强弱<br>"
     "• 底部的<b>蒙版按钮</b>（方框里一个圆）：只让一部分生效（在蒙版上用黑色画笔擦掉不要的地方）<br>"
     "• 双击图层可以改名，用底部的上下箭头调整上下顺序<br>"
     "• 旁边的<b>历史记录</b>标签：做过的每一步")),
    ("history", _t("⑥ 历史记录"),
     _t("做过的每一步都记在这里。<b>Ctrl+Z</b> 撤销、<b>Ctrl+Shift+Z</b> 重做，"
     "也可以直接点某一行，回到那一步时的样子。")),
    ("menu_image", _t("⑦ 菜单"),
     _t("更多功能在顶部菜单里：<br>"
     "• <b>图像</b>：调整、预设、图像大小、旋转翻转<br>"
     "• <b>选择</b>：全选、反选、<b>选择主体</b>（自动抠人 / 物）、羽化<br>"
     "• <b>滤镜</b>：模糊、锐化、降噪等，调参数时能实时预览<br>"
     "• <b>帮助 → 快捷键</b>（F1）：全部快捷键一览")),
    ("menu_file", _t("⑧ 保存：两种方式"),
     _t("• <b>文件 → 导出图片</b>（Ctrl+Shift+S）：合成为一张新的 JPG / PNG，"
     "默认存在原片旁边，叫「原名_修图.jpg」，会自动出现在照片库里。<br>"
     "• <b>文件 → 保存工程</b>（Ctrl+S）：保留所有图层，下次打开这张照片可以接着改。<br><br>"
     "原片在任何情况下都不会被覆盖。")),
    (None, _t("试试这三个小练习"),
     _t("<b>1. 一键调色</b>：点图层面板底部的调整图层按钮 → 右侧「套用预设」选「日系」。<br><br>"
     "<b>2. 去掉一个小瑕疵</b>：在图层面板选中「背景」→ 按 J → 在瑕疵上涂一下。<br><br>"
     "<b>3. 裁剪</b>：按 C → 选项栏选比例 1 : 1 → 拖动框 → 回车。<br><br>"
     "想再看一遍这个教程：<b>帮助 → 新手教程</b>。祝修图愉快！")),
]


class TourOverlay(QWidget):
    """盖在修图窗口上的教程层。"""

    def __init__(self, win, on_done=None):
        super().__init__(win)
        self.win = win
        self.on_done = on_done
        self.i = 0
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        p = theme.current()
        self.card = QFrame(self)
        self.card.setObjectName("TourCard")
        self.card.setStyleSheet(f"#TourCard {{ background: {p.surface}; border: 1px solid {p.ink}; "
                                f"border-top: 4px solid {p.accent}; }}")
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setFont(font("cn", 13, 700))
        self.count = QLabel()
        self.count.setFont(font("mono", 8.5))
        self.count.setProperty("role", "faint")
        head.addWidget(self.title, 1)
        head.addWidget(self.count)
        lay.addLayout(head)
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setFont(font("cn", 10))
        self.body.setStyleSheet("line-height: 150%;")
        lay.addWidget(self.body)
        row = QHBoxLayout()
        self.b_skip = TacButton(_t("跳过教程"), "secondary", height=30)
        self.b_prev = TacButton(_t("上一步"), "secondary", height=30)
        self.b_next = TacButton(_t("下一步"), "primary", height=30)
        self.b_skip.clicked.connect(self.finish)
        self.b_prev.clicked.connect(lambda: self.go(self.i - 1))
        self.b_next.clicked.connect(lambda: self.go(self.i + 1) if self.i < len(STEPS) - 1 else self.finish())
        row.addWidget(self.b_skip)
        row.addStretch(1)
        row.addWidget(self.b_prev)
        row.addWidget(self.b_next)
        lay.addLayout(row)
        self.card.setFixedWidth(430)
        win.installEventFilter(self)
        self.setGeometry(win.rect())
        self.show()
        self.raise_()
        self.go(0)

    # ---- 目标区域 ----
    def target_rect(self, key: str | None) -> QRect | None:
        w = self.win
        if key is None:
            return None
        if key.startswith("menu_"):
            menu = getattr(w, key, None)
            if menu is None:
                return None
            mb = w.menuBar()
            r = mb.actionGeometry(menu.menuAction())
            return QRect(mb.mapTo(w, r.topLeft()), r.size())
        tabs = getattr(w, "side_tabs", None)
        if key in ("layers", "history") and tabs is not None:
            w.show_side_tab(key)                       # 图层和历史记录在同一组标签页里：先切过去，照亮整组（连同标签）
        widget = {"toolbar": w.toolbar, "options": w.options, "canvas": w.canvas, "props": w.panel_props,
                  "layers": tabs or w.panel_layers, "history": tabs or w.panel_history}.get(key)
        if widget is None or not widget.isVisible():
            return None
        return QRect(widget.mapTo(w, widget.rect().topLeft()), widget.size())

    # ---- 翻页 ----
    def go(self, i: int) -> None:
        self.i = max(0, min(len(STEPS) - 1, i))
        key, title, text = STEPS[self.i]
        self.title.setText(title)
        self.body.setText(text)
        self.count.setText(f"{self.i + 1} / {len(STEPS)}")
        self.b_prev.setEnabled(self.i > 0)
        self.b_next.setText(_t("开始修图") if self.i == len(STEPS) - 1 else _t("下一步"))
        self.b_skip.setVisible(self.i < len(STEPS) - 1)
        self._layout()
        self.update()
        self.setFocus()

    def _layout(self) -> None:
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.sizeHint().height()
        self.card.resize(cw, ch)
        r = self.target_rect(STEPS[self.i][0])
        W, H = self.width(), self.height()
        if r is None:
            x, y = (W - cw) // 2, (H - ch) // 2
        else:
            gap = 18
            # 优先放在目标右边，其次左边、下边、上边，放不下就放在目标里面居中
            if r.right() + gap + cw < W - 10:
                x, y = r.right() + gap, r.top()
            elif r.left() - gap - cw > 10:
                x, y = r.left() - gap - cw, r.top()
            elif r.bottom() + gap + ch < H - 10:
                x, y = r.left(), r.bottom() + gap
            elif r.top() - gap - ch > 10:
                x, y = r.left(), r.top() - gap - ch
            else:
                x, y = r.center().x() - cw // 2, r.center().y() - ch // 2
            x = max(10, min(W - cw - 10, x))
            y = max(10, min(H - ch - 10, y))
        self.card.move(x, y)

    def finish(self) -> None:
        if hasattr(self.win, "show_side_tab"):
            self.win.show_side_tab("layers")
        self.win.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        if self.on_done:
            self.on_done()

    # ---- 事件 ----
    def eventFilter(self, obj, e):
        if obj is self.win and e.type() == QEvent.Type.Resize:
            self.setGeometry(self.win.rect())
            self._layout()
        return False

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.finish()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.b_next.click()
        elif k == Qt.Key.Key_Left:
            self.go(self.i - 1)
        e.accept()

    def mousePressEvent(self, e):
        e.accept()                                      # 教程进行中不让点到下面的界面

    def wheelEvent(self, e):
        e.accept()

    def paintEvent(self, e):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        r = self.target_rect(STEPS[self.i][0])
        hole = None
        if r is not None:
            hole = QRectF(r).adjusted(-5, -5, 5, 5)
            path.addRoundedRect(hole, 4, 4)
            path.setFillRule(Qt.FillRule.OddEvenFill)
        qp.fillPath(path, QColor(8, 10, 12, 165))
        if hole is not None:
            p = theme.current()
            qp.setPen(QPen(QColor(p.accent), 3))
            qp.drawRoundedRect(hole, 4, 4)
        qp.end()
