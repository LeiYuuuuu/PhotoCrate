"""小匣助手的面板：顶栏「助手」按钮或 Ctrl+K 打开，浮在主窗口右侧，相册和修图里都能用。

回答默认来自 photocrate/assist（本地、确定性）；在面板顶部可以换成大模型（assist/llm.py，设置见 assistant_llm.py），
请求在后台线程里跑，失败就显示本地回答。面板只负责显示和“带我去”：切页面、切到修图、选工具、执行或展开修图菜单，
以及把调色方案「应用」成一个新的调整图层（不改像素，Ctrl+Z 或删掉图层就回去了）。
主窗口里只有一行 install(self)。
"""
from __future__ import annotations

import html
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTextBrowser, QVBoxLayout)

from ..assist import llm, secrets
from ..assist.engine import Answer, Assistant, Plan
from ..assist.features import BY_ID, DESCRIPTIVE, PHOTOS, STUDIO, Feature
from . import theme
from .assistant_llm import Job, LLMDialog, label as llm_label, thumbnail_jpeg
from .theme import font
from .widgets.deco import TacButton
from ..i18n import t as _t

SHORTCUT = "Ctrl+K"


def _mode_name(i: int) -> str:
    from .main_window import ModeSwitch
    return ModeSwitch.MODES[i]


class AssistantPanel(QFrame):
    WIDTH = 400

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.engine = Assistant()
        self.plans: list[Plan] = []
        self.history: list[str] = []
        self.turns: list[tuple[str, str]] = []          # 给大模型的最近几轮（问题, 回答摘要）
        self._job = None
        self._hist_i = 0
        self._html: list[str] = []
        self.setObjectName("AssistantPanel")
        p = theme.current()
        self.setStyleSheet(f"""
#AssistantPanel {{ background: {p.surface}; border: 1px solid {p.line}; border-radius: 8px; }}
#AssistantPanel QLabel {{ color: {p.ink}; background: transparent; }}
#AssistantPanel QTextBrowser {{ background: {p.surface}; color: {p.ink}; border: none; }}
#AssistantPanel QLineEdit {{ background: {p.bg}; color: {p.ink}; border: 1px solid {p.line}; border-radius: 4px;
    padding: 6px 8px; }}
#AssistantPanel QLineEdit:focus {{ border-color: {p.accent}; }}
#AssistantPanel QPushButton#close {{ background: transparent; color: {p.ink2}; border: none; font-size: 13pt; }}
#AssistantPanel QPushButton#close:hover {{ color: {p.ink}; }}
#AssistantPanel QPushButton#clear {{ background: transparent; color: {p.ink3}; border: none; }}
#AssistantPanel QPushButton#clear:hover {{ color: {p.ink}; }}
#AssistantPanel QPushButton#model {{ background: {p.bg}; color: {p.ink2}; border: 1px solid {p.line}; border-radius: 10px;
    padding: 1px 8px; font-size: 8.5pt; }}
#AssistantPanel QPushButton#model:hover {{ color: {p.ink}; border-color: {p.accent}; }}
""")
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(28)
        sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(sh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 10, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel(_t("小匣助手"))
        title.setFont(font("cn", 11.5, 800))
        head.addWidget(title)
        self.b_model = QPushButton()
        self.b_model.setObjectName("model")
        self.b_model.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_model.setToolTip(_t("换成 Claude / DeepSeek / GPT / Grok / Kimi，或回到本地"))
        self.b_model.clicked.connect(self.settings)
        head.addWidget(self.b_model)
        head.addStretch(1)
        b_clear = QPushButton(_t("清空"))
        b_clear.setObjectName("clear")
        b_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        b_clear.clicked.connect(self.clear)
        head.addWidget(b_clear)
        b_close = QPushButton("×")
        b_close.setObjectName("close")
        b_close.setCursor(Qt.CursorShape.PointingHandCursor)
        b_close.setToolTip(_t("关闭（Esc）"))
        b_close.clicked.connect(self.hide)
        head.addWidget(b_close)
        lay.addLayout(head)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._link)
        self.view.setFont(font("cn", 9.5))
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText(_t("问功能在哪，或描述想要的效果…"))
        self.input.returnPressed.connect(self.send)
        self.input.installEventFilter(self)
        row.addWidget(self.input, 1)
        b = TacButton(_t("发送"), "primary", height=32)
        b.clicked.connect(self.send)
        row.addWidget(b)
        lay.addLayout(row)
        self.cfg = llm.load_config(main.ctx.settings)
        self.b_model.setText(llm_label(self.cfg))
        self.clear()
        self.hide()
        main.installEventFilter(self)

    # ---- 位置 ----
    def place(self) -> None:
        """靠右浮在主窗口上；修图模式里停在右侧面板（属性 / 图层）左边，应用后还能看到滑块。"""
        m = self.main
        top = m.top.geometry().bottom() + 8
        bottom = m.status.geometry().top() - 8 if m.status.isVisible() else m.height() - 12
        h = max(320, bottom - top)
        right = m.width() - 12
        ew = self._editor()
        if ew is not None and m.mode() == 1 and ew.props.isVisible():
            x = ew.props.mapTo(m, QPoint(0, 0)).x() - 8
            if x - self.WIDTH > 240:                   # 窗口太窄就还是盖在右边
                right = x
        self.setGeometry(right - self.WIDTH, top, self.WIDTH, h)

    def eventFilter(self, obj, ev):
        if obj is self.main and ev.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        elif obj is self.input and ev.type() == QEvent.Type.KeyPress:
            k = ev.key()
            if k == Qt.Key.Key_Escape:
                self.hide()
                return True
            if k in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self.history:
                self._hist_i = max(0, min(len(self.history), self._hist_i + (-1 if k == Qt.Key.Key_Up else 1)))
                self.input.setText(self.history[self._hist_i] if self._hist_i < len(self.history) else "")
                return True
        return super().eventFilter(obj, ev)

    def toggle(self) -> None:
        if self.isVisible() and self.input.hasFocus():
            self.hide()
            return
        self.place()
        self.show()
        self.raise_()
        self.input.setFocus()
        self.input.selectAll()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    # ---- 对话 ----
    def settings(self) -> None:
        d = LLMDialog(self.main, self.main.ctx.settings)
        if d.exec():
            self.cfg = llm.load_config(self.main.ctx.settings)
            self.b_model.setText(llm_label(self.cfg))
            self._say(_t("之后的问题由 {0} 回答。", llm_label(self.cfg)))

    def clear(self) -> None:
        self.plans.clear()
        self.turns.clear()
        self._html = [self._render(self.engine.ask(""), first=True)]
        self._refresh()

    def ask(self, text: str) -> None:
        self.input.setText(text)
        self.send()

    def send(self) -> None:
        q = self.input.text().strip()
        if not q or self._job is not None:
            return
        self.input.clear()
        self.history.append(q)
        self._hist_i = len(self.history)
        stats, mode = self._stats(), self.main.mode()
        try:
            local = self.engine.ask(q, stats, mode)
        except Exception as e:                   # 知识库有误也不该让面板崩掉
            local = Answer("unknown", _t("出错了：{0}", e))
        p = theme.current()
        self._html.append(f"<p style='margin:14px 0 4px 0; text-align:right'><span style='background:{p.sel};"
                          f" color:{p.ink}'>&nbsp;{html.escape(q)}&nbsp;</span></p>")
        pid = self.cfg.get("provider")
        if pid not in llm.BY_PID:
            self._html.append(self._render(local))
            self._remember(q, local)
            self._refresh()
            return
        image = None
        if self.cfg.get("send_image") and mode == 1 and self._editor() is not None:
            try:
                image = thumbnail_jpeg(self._editor().canvas.display_rgba())
            except Exception:
                image = None
        name = llm.BY_PID[pid].name
        self._html.append(f"<p style='color:{p.ink3}'>{_t('正在问 {0}…', name)}"
                          f"{_t('（附带了缩略图）') if image else ''}</p>")
        slot = len(self._html) - 1
        self._refresh()
        cfg, key, turns = dict(self.cfg), secrets.get_key(pid), list(self.turns)
        job = self._job = Job(lambda: llm.ask(cfg, key, q, local, stats, mode, turns, image), self)

        def finish(ans, err=None):
            self._job = None
            self.input.setEnabled(True)
            if err is not None:
                fail = (f"<p style='margin:4px 0; color:{p.danger}'>{html.escape(_t('{0} 没有回答成功：', name) + err)}"
                        f"</p><p style='margin:2px 0; color:{p.ink3}'>{_t('先看本地助手的回答：')}</p>")
                self._html[slot] = fail + self._render(local)
                self._remember(q, local)
            else:
                self._html[slot] = self._render(ans)
                self._remember(q, ans)
            self._refresh()
            self.input.setFocus()
        job.ok.connect(lambda a: finish(a))
        job.failed.connect(lambda msg: finish(None, msg))
        self.input.setEnabled(False)
        job.start()

    def _remember(self, q: str, a: Answer) -> None:
        """记一句回答摘要，下一轮带给大模型（“再暖一点”这类追问要知道上一轮说了什么）。"""
        bits = [a.intro] if a.intro else []
        if a.plan is not None:
            bits.append("参数：" + "；".join(f"{l.label} {l.value}" for l in a.plan.lines))
        if a.features:
            bits.append("功能：" + "、".join(f.title for f in a.features[:3]))
        self.turns = (self.turns + [(q, " ".join(bits)[:400])])[-3:]

    def _refresh(self) -> None:
        if self.isVisible():
            self.place()
        self.view.setHtml("".join(self._html))
        sb = self.view.verticalScrollBar()
        QTimer.singleShot(0, lambda: sb.setValue(sb.maximum()))

    def _editor(self):
        studio = getattr(self.main, "studio", None)
        return studio.current_editor() if studio is not None else None

    def _stats(self):
        """修图模式里有打开的照片时，按画布上看到的样子统计。"""
        ew = self._editor()
        if ew is None or self.main.mode() != 1:
            return None
        try:
            buf = ew.canvas.display_rgba()
            if buf is None:
                return None
            from ..assist.photo import analyze
            return analyze(buf)
        except Exception:
            return None

    # ---- 渲染 ----
    def _path(self, f: Feature) -> str:
        p = theme.current()
        parts = []
        for i, s in enumerate(f.path):
            if i == 0 and s in (PHOTOS, STUDIO):
                parts.append(f"<b>{html.escape(_mode_name(0 if s == PHOTOS else 1))}</b>")
            elif s in DESCRIPTIVE:
                parts.append(f"<span style='color:{p.ink3}'>{html.escape(_t(s))}</span>")
            else:
                parts.append(f"<b>{html.escape(_t(s))}</b>")
        return f" <span style='color:{p.ink3}'>›</span> ".join(parts)

    def _feature(self, f: Feature) -> str:
        p = theme.current()
        sc = f"&nbsp;&nbsp;<span style='color:{p.ink3}'>{html.escape(f.shortcut)}</span>" if f.shortcut else ""
        go = (f"<br><a href='go:{f.id}' style='color:{p.accent2}; text-decoration:none'>{_t('带我去')} →</a>"
              if f.go else "")
        return (f"<p style='margin:8px 0 2px 0'><b>{html.escape(f.title)}</b>{sc}<br>{self._path(f)}<br>"
                f"<span style='color:{p.ink2}'>{html.escape(f.how)}</span>{go}</p>")

    def _plan(self, plan: Plan) -> str:
        p = theme.current()
        idx = len(self.plans)
        self.plans.append(plan)
        rows = []
        for i, l in enumerate(plan.lines):
            rows.append(f"<tr><td style='color:{p.ink3}; padding:2px 6px 2px 0'>{html.escape(_t(l.where))}</td>"
                        f"<td style='padding:2px 8px 2px 0'>{html.escape(l.label)}</td>"
                        f"<td style='padding:2px 8px 2px 0' align='right'><b>{html.escape(l.value)}</b></td></tr>")
            nxt = plan.lines[i + 1].reason if i + 1 < len(plan.lines) else None
            if l.reason and l.reason != nxt:           # 同一个理由连着几行，只在最后一行下面写一次
                rows.append(f"<tr><td></td><td colspan='2' style='color:{p.ink2}; font-size:8.5pt; padding:0 0 6px 0'>"
                            f"{html.escape(l.reason)}</td></tr>")
        links = (f"<a href='apply:{idx}' style='color:{p.accent2}; text-decoration:none'><b>{_t('应用为调整图层')}</b></a>"
                 f"&nbsp;&nbsp;&nbsp;<a href='copy:{idx}' style='color:{p.ink2}; text-decoration:none'>{_t('复制参数')}</a>")
        return (f"<p style='margin:8px 0 4px 0'><b>{html.escape(plan.title)}</b></p>"
                f"<table cellspacing='0'>{''.join(rows)}</table>"
                f"<p style='margin:6px 0'>{links}</p>"
                + "".join(f"<p style='margin:2px 0; color:{p.ink2}'>· {html.escape(n)}</p>" for n in plan.notes))

    def _render(self, a: Answer, first: bool = False) -> str:
        p = theme.current()
        out = []
        if first:
            out.append(f"<p style='color:{p.ink2}'>{_t('问我某个功能在哪、怎么用，或者描述想要的画面，我告诉你调哪些参数。')}"
                       f"<br>{_t('修图里打开照片后，还可以问“这张照片怎么修”。')}</p>")
        elif a.intro:
            out.append(f"<p style='margin:4px 0'>{html.escape(a.intro)}</p>")
        if a.source:
            out.append(f"<p style='margin:0 0 4px 0; color:{p.ink3}; font-size:8.5pt'>"
                       f"{html.escape(_t('{0} 的回答 · 功能位置和参数范围已按影匣核对', a.source))}</p>")
        if a.plan is not None:
            out.append(self._plan(a.plan))
        for n in a.notes:
            out.append(f"<p style='margin:2px 0; color:{p.ink2}'>· {html.escape(n)}</p>")
        for r in a.recipes:
            steps = "".join(f"<li style='margin:2px 0'>{html.escape(s)}</li>" for s in r.steps)
            out.append(f"<p style='margin:8px 0 2px 0'><b>{html.escape(r.name)}</b></p><ol style='margin:0'>{steps}</ol>")
        if a.features:
            if a.kind == "effect":
                out.append(f"<p style='margin:10px 0 0 0; color:{p.ink3}'>{_t('相关功能')}</p>")
            out += [self._feature(f) for f in a.features]
        if a.suggestions:
            chips = "<br>".join(f"<a href='ask:{quote(s)}' style='color:{p.accent2}; text-decoration:none'>› "
                                f"{html.escape(s)}</a>" for s in a.suggestions)
            out.append(f"<p style='margin:6px 0'>{chips}</p>")
        return f"<div style='margin-bottom:6px'>{''.join(out)}</div>"

    # ---- 链接 ----
    def _link(self, url: QUrl) -> None:
        s = url.toString()
        kind, _, arg = s.partition(":")
        if kind == "ask":
            self.ask(unquote(arg))
        elif kind == "go" and arg in BY_ID:
            self._go(BY_ID[arg])
        elif kind == "apply":
            self._apply(self.plans[int(arg)])
        elif kind == "copy":
            plan = self.plans[int(arg)]
            QGuiApplication.clipboard().setText("\n".join(f"{l.where} · {l.label}  {l.value}" for l in plan.lines))
            self._say(_t("参数已复制。"))

    def _say(self, text: str) -> None:
        self._html.append(f"<p style='margin:4px 0; color:{theme.current().ink2}'>{html.escape(text)}</p>")
        self._refresh()

    def _need_editor(self):
        ew = self._editor()
        self.main.set_mode(1)
        if ew is None:
            self._say(_t("先在修图里打开一张照片（「打开图片…」或从相册「在修图中打开」）。"))
        return ew

    def _apply(self, plan: Plan) -> None:
        ew = self._need_editor()
        if ew is None:
            return
        import copy
        name = _t("助手") + "：" + (plan.title if len(plan.title) <= 18 else plan.title[:17] + "…")
        masked = ew.doc.selection is not None
        ew.new_adjust(name, copy.deepcopy(plan.params))
        self._say(_t("已新建调整图层「{0}」{1}。不满意可以在右侧继续拖滑块、降低不透明度，或 Ctrl+Z 撤销。", name,
                     _t("（有选区，只作用在选区里）") if masked else ""))

    def _go(self, f: Feature) -> None:
        kind, arg = f.go[0], f.go[1]
        if kind == "page":
            self.main.ctx.navigate.emit(arg)
        elif kind == "mode":
            self.main.set_mode(arg)
        elif kind == "tool":
            ew = self._need_editor()
            if ew is not None:
                ew.set_tool(arg)
        elif kind in ("menu", "show"):
            ew = self._need_editor()
            if ew is None:
                return
            a = find_action(ew.menuBar(), arg)
            if a is None:
                self._say(_t("没找到菜单「{0}」。", " → ".join(_t(x) for x in arg)))
            elif kind == "menu" and a.menu() is None:
                QTimer.singleShot(0, a.trigger)
            else:
                show_menu(ew.menuBar(), arg)


def find_action(menubar, path):
    """按显示文字逐级找菜单项（path 里是中文原文，比较时同样经过 _t）。"""
    actions = menubar.actions()
    a = None
    for i, name in enumerate(path):
        want = _t(name)
        a = next((x for x in actions if x.text().replace("&", "") == want), None)
        if a is None:
            return None
        if i < len(path) - 1:
            if a.menu() is None:
                return None
            actions = a.menu().actions()
    return a


def show_menu(menubar, path) -> None:
    """展开顶层菜单，并把高亮停在下一级（如果有）上，让用户看到它在哪。"""
    top = find_action(menubar, path[:1])
    if top is None or top.menu() is None:
        return
    menubar.setFocus()
    menubar.setActiveAction(top)
    if len(path) > 1:
        nxt = find_action(menubar, path[:2])
        if nxt is not None:
            QTimer.singleShot(60, lambda: top.menu().setActiveAction(nxt))


class AssistButton(QPushButton):
    def __init__(self, parent=None):
        super().__init__(_t("助手"), parent)
        p = theme.current()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_t("小匣助手：问功能在哪、想要的效果怎么调（{0}）", SHORTCUT))
        self.setFixedHeight(26)
        self.setStyleSheet(f"""
QPushButton {{ background: {p.slab2}; color: {p.slab_ink}; border: none; border-radius: 5px; padding: 0 12px;
    font-size: 9.5pt; font-weight: 600; }}
QPushButton:hover {{ background: {p.accent}; color: {p.accent_ink}; }}
""")


def install(main) -> AssistantPanel:
    """在主窗口顶栏放「助手」按钮，挂 Ctrl+K。"""
    panel = AssistantPanel(main)
    btn = AssistButton(main.top)
    lay = main.top.layout()
    lay.insertWidget(lay.indexOf(main.top.info), btn)
    btn.clicked.connect(panel.toggle)
    sc = QShortcut(QKeySequence(SHORTCUT), main)
    sc.setContext(Qt.ShortcutContext.WindowShortcut)
    sc.activated.connect(panel.toggle)
    main.top.mode.changed.connect(lambda _i: panel.isVisible() and QTimer.singleShot(0, panel.place))
    main.assistant = panel
    return panel
