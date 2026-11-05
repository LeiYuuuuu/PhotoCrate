"""小匣助手的大模型设置：选服务商（本地 / Claude / DeepSeek / GPT / Grok / Kimi）、连接方式（API Key / 订阅 / ChatGPT 账号）、
模型、接口地址或客户端路径、是否附带缩略图。

网络请求都在后台线程（Job）里做，界面不卡。Key 存在 assist/secrets.py 管的单独文件里（Windows 上 DPAPI 加密）。
订阅走用户自己的官方客户端（assist/clients.py），影匣不接触订阅账号的登录信息。
GPT 还可以直接用 ChatGPT 账号登录（assist/chatgpt.py）：Continue with ChatGPT、Using ChatGPT plan、Manage usage 等字样
是 OpenAI 界面规范（https://developers.openai.com/siwc/ui-ux-guidelines）规定的，中英文界面都原样显示，不翻译。
"""
from __future__ import annotations

import io
import threading

from PySide6.QtCore import QSize, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                               QWidget)

from ..assist import chatgpt, clients, llm, secrets
from ..config import find_resource
from . import theme
from .dialogs import _Base
from .widgets.deco import TacButton
from ..i18n import t as _t


_LIVE: set = set()


class Job(QThread):
    """在后台跑一个函数：成功发 ok(结果)，失败发 failed(给用户看的说明)。
    不挂父对象、自己留引用直到跑完：面板或对话框先关了也不会把还在跑的线程销毁掉。"""
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, _parent=None):
        super().__init__(None)
        self.fn = fn
        self.error = None                                # 失败时的异常：界面可以按 action 显示主操作
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self):
        try:
            self.ok.emit(self.fn())
        except llm.LLMError as e:
            self.error = e
            self.failed.emit(str(e))
        except Exception as e:                           # 不让意外错误留在后台线程里
            self.error = e
            self.failed.emit(f"{type(e).__name__}: {e}")


def orphan(job: QThread) -> None:
    """不再关心结果的请求：断开回调，让它自己跑完。"""
    for sig in (job.ok, job.failed):
        try:
            sig.disconnect()
        except (RuntimeError, TypeError):
            pass


def thumbnail_jpeg(rgba, long_side: int = 768) -> bytes | None:
    """画布显示缓冲 → 长边 ≤ long_side 的 JPEG。"""
    if rgba is None:
        return None
    from PIL import Image
    im = Image.fromarray(rgba[..., :3] if rgba.shape[2] == 4 else rgba)
    im.thumbnail((long_side, long_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def open_usage() -> None:
    QDesktopServices.openUrl(QUrl(chatgpt.USAGE_URL))


def chatgpt_logo(color: str = "white") -> QIcon:
    """OpenAI 提供的 ChatGPT 标志（assets/chatgpt-logo-white.svg）。浅色界面上按文字颜色重新着色。"""
    path = find_resource("assets", "chatgpt-logo-white.svg")
    if path is None:
        return QIcon()
    if color == "white":
        return QIcon(str(path))
    svg = path.read_text("utf-8").replace('fill="white"', f'fill="{color}"')
    return QIcon(theme._svg(f"chatgpt_{color.strip('#')}.svg", svg))


def label(cfg: dict) -> str:
    pid = cfg.get("provider", "local")
    if pid not in llm.BY_PID:
        return _t("本地 · 不联网")
    if llm.uses_chatgpt(cfg, pid):
        return f"ChatGPT plan · {chatgpt.model_of(cfg)}"
    if llm.uses_subscription(cfg, pid):
        return _t("{0} 订阅 · {1}", llm.BY_PID[pid].name, clients.model_of(cfg, pid))
    return f"{llm.BY_PID[pid].name} · {llm.model_of(cfg, pid)}"


class LLMDialog(_Base):
    def __init__(self, parent, settings):
        super().__init__(parent, _t("助手用的模型"), "ASSISTANT MODEL")
        self.settings = settings
        self.cfg = llm.load_config(settings)
        self.cfg["models"] = dict(self.cfg.get("models") or {})
        self.cfg["base_urls"] = dict(self.cfg.get("base_urls") or {})
        for k in ("auth", "client_models", "client_paths"):
            self.cfg[k] = dict(self.cfg.get(k) or {})
        self.new_keys: dict[str, str] = {}
        self._job = None
        self._status_job = None
        self._login_job = None
        self._login_cancel = None
        self._out_job = None
        self._welcome = None
        self.cfg_chatgpt_model = chatgpt.model_of(self.cfg)
        self.setMinimumWidth(560)
        p = theme.current()

        intro = QLabel(_t("默认用本地助手：不联网、回答都来自核对过的功能目录。选一个大模型后，它能理解更随意的说法、"
                          "回答知识库里没有的问题；它给出的功能和参数仍会按影匣的功能目录和滑块范围校验，失败时自动退回本地回答。"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {p.ink2};")
        self.body.addWidget(intro)

        def grid() -> QGridLayout:
            lay = QGridLayout()
            lay.setHorizontalSpacing(10)
            lay.setVerticalSpacing(8)
            lay.setColumnMinimumWidth(0, 76)                # 上下两个网格的标签列对齐
            lay.setColumnStretch(1, 1)
            return lay

        g = grid()
        g.addWidget(QLabel(_t("服务商")), 0, 0)
        self.provider = QComboBox()
        self.provider.addItem(_t("本地（不联网）"), "local")
        for pv in llm.PROVIDERS:
            self.provider.addItem(pv.name, pv.id)
        g.addWidget(self.provider, 0, 1, 1, 2)

        self.auth_label = QLabel(_t("连接方式"))
        g.addWidget(self.auth_label, 1, 0)
        self.auth = QComboBox()
        g.addWidget(self.auth, 1, 1, 1, 2)

        self.key_label = QLabel("API Key")
        g.addWidget(self.key_label, 2, 0)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        g.addWidget(self.key, 2, 1)
        self.b_clear_key = QPushButton(_t("清除"))
        self.b_clear_key.clicked.connect(self._clear_key)
        g.addWidget(self.b_clear_key, 2, 2)
        self.client_state = QLabel()                        # 订阅模式：和 Key 输入框同一格，二选一显示
        self.client_state.setWordWrap(True)
        self.client_state.setOpenExternalLinks(True)
        g.addWidget(self.client_state, 2, 1)
        self.b_login = QPushButton(_t("登录"))
        self.b_login.setToolTip(_t("打开官方客户端的登录窗口"))
        self.b_login.clicked.connect(self._login)
        g.addWidget(self.b_login, 2, 2)
        self.body.addLayout(g)
        self.cg_box = self._chatgpt_box(p)                  # ChatGPT 账号模式：单独一块，取代上面的 Key 输入框
        self.body.addWidget(self.cg_box)
        g = grid()
        self.key_hint = QLabel()
        self.key_hint.setOpenExternalLinks(True)
        self.key_hint.setStyleSheet(f"color: {p.ink3}; font-size: 8.5pt;")
        self.key_hint.setWordWrap(True)
        g.addWidget(self.key_hint, 0, 1, 1, 2)

        g.addWidget(QLabel(_t("模型")), 1, 0)
        self.model = QComboBox()
        self.model.setEditable(True)
        g.addWidget(self.model, 1, 1)
        self.b_refresh = QPushButton(_t("刷新"))
        self.b_refresh.setToolTip(_t("从服务商获取可用的模型列表"))
        self.b_refresh.clicked.connect(self._refresh_models)
        g.addWidget(self.b_refresh, 1, 2)

        self.base_label = QLabel(_t("接口地址"))
        g.addWidget(self.base_label, 2, 0)
        self.base = QLineEdit()
        g.addWidget(self.base, 2, 1, 1, 2)
        self.body.addLayout(g)

        self.send_image = QCheckBox(_t("提问时附带当前照片的缩略图（长边 768 像素），让模型看着照片给建议"))
        self.send_image.setChecked(bool(self.cfg.get("send_image")))
        self.body.addWidget(self.send_image)
        self.privacy = QLabel()
        self.privacy.setWordWrap(True)
        self.privacy.setStyleSheet(f"color: {p.ink3}; font-size: 8.5pt;")
        self.body.addWidget(self.privacy)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(self.status)

        self.b_test = TacButton(_t("测试连接"), "secondary", height=32)
        self.b_test.clicked.connect(self._test)
        self.buttons.insertWidget(0, self.b_test)
        b_cancel = TacButton(_t("取消"), "secondary", height=32)
        b_cancel.clicked.connect(self.reject)
        self.buttons.addWidget(b_cancel)
        b_ok = TacButton(_t("保存"), "primary", height=32)
        b_ok.clicked.connect(self._save)
        self.buttons.addWidget(b_ok)

        self._pid = None
        self._sub = False
        self._cg = False
        self.provider.currentIndexChanged.connect(self._switch)
        self.auth.currentIndexChanged.connect(self._auth_changed)
        self.base.editingFinished.connect(lambda: self._check_client() if self._sub else None)
        i = self.provider.findData(self.cfg.get("provider", "local"))
        self.provider.setCurrentIndex(max(0, i))
        self._switch()

    def _chatgpt_box(self, p) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.cg_state = QLabel()
        self.cg_state.setWordWrap(True)
        v.addWidget(self.cg_state)
        self.cg_plan = QWidget()                            # Using ChatGPT plan · Manage usage
        h = QHBoxLayout(self.cg_plan)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        logo = QLabel()
        logo.setPixmap(chatgpt_logo(p.ink).pixmap(QSize(16, 16)))
        h.addWidget(logo)
        h.addWidget(QLabel("Using ChatGPT plan"))
        self.b_cg_usage = QPushButton("Manage usage")
        self.b_cg_usage.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_cg_usage.setToolTip(chatgpt.USAGE_URL)
        self.b_cg_usage.clicked.connect(lambda: open_usage())
        h.addWidget(self.b_cg_usage)
        h.addStretch(1)
        v.addWidget(self.cg_plan)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.b_cg_login = QPushButton(chatgpt_logo(), "Continue with ChatGPT")    # 官方按钮样式：黑底白字带标志
        self.b_cg_login.setIconSize(QSize(21, 21))
        self.b_cg_login.setFixedHeight(45)
        self.b_cg_login.setMinimumWidth(242)
        self.b_cg_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.b_cg_login.setStyleSheet("QPushButton { background: #000; color: #fff; border: none; border-radius: 12px;"
                                      " padding: 0 20px; font-size: 15px; font-weight: 500;"
                                      " min-height: 45px; max-height: 45px; }"
                                      "QPushButton:hover { background: #2a2a2a; }"
                                      "QPushButton:disabled { background: #555; color: #ddd; }")
        self.b_cg_login.clicked.connect(lambda: self._cg_sign_in())
        row.addWidget(self.b_cg_login)
        self.b_cg_cancel = QPushButton(_t("取消"))
        self.b_cg_cancel.clicked.connect(self._cg_cancel)
        row.addWidget(self.b_cg_cancel)
        self.b_cg_other = QPushButton(_t("使用其他账号"))
        self.b_cg_other.setToolTip(_t("登录另一个 ChatGPT 账号或工作区，现在的连接会保留"))
        self.b_cg_other.clicked.connect(lambda: self._cg_sign_in(new_account=True))
        row.addWidget(self.b_cg_other)
        self.b_cg_out = QPushButton(_t("退出登录"))
        self.b_cg_out.clicked.connect(self._cg_sign_out)
        row.addWidget(self.b_cg_out)
        row.addStretch(1)
        v.addLayout(row)
        self.cg_accounts = QComboBox()                      # 保存了两个以上账号时才显示
        self.cg_accounts.activated.connect(self._cg_pick)
        v.addWidget(self.cg_accounts)
        return box

    def _model_now(self) -> str:
        """ChatGPT 模式下列表显示 display_name，实际用的是 slug（放在 itemData 里）。"""
        if self._cg:
            return str(self.model.currentData() or self.model.currentText()).strip()
        return self.model.currentText().strip()

    # ---- 每个服务商一套设置，切换时先记下当前这套 ----
    def _stash(self) -> None:
        pid = self._pid
        if pid not in llm.BY_PID:
            return
        if self._cg:
            self.cfg_chatgpt_model = self._model_now() or self.cfg_chatgpt_model
            return
        model, field = self.model.currentText().strip(), self.base.text().strip()
        models, fields = ("client_models", "client_paths") if self._sub else ("models", "base_urls")
        self.cfg[models][pid] = model
        if field:
            self.cfg[fields][pid] = field
        else:
            self.cfg[fields].pop(pid, None)
        if not self._sub and self.key.text().strip():
            self.new_keys[pid] = self.key.text().strip()

    def _switch(self, *_):
        self._stash()
        pid = self.provider.currentData()
        self._pid = pid
        can_sub = pid in llm.SUBSCRIPTION_PIDS
        self.auth.blockSignals(True)
        self.auth.clear()
        if can_sub:
            self.auth.addItem(_t("API Key（按用量付费）"), "api")
            self.auth.addItem(_t("订阅（用本机的 {0} 客户端）", clients.CLIENTS[pid].name), "subscription")
            if pid in llm.CHATGPT_PIDS:
                self.auth.addItem(_t("ChatGPT 账号（在影匣里登录，用你的计划额度）"), "chatgpt")
            self.auth.setCurrentIndex(max(0, self.auth.findData((self.cfg.get("auth") or {}).get(pid, "api"))))
        self.auth.blockSignals(False)
        self.auth_label.setVisible(can_sub)
        self.auth.setVisible(can_sub)
        self._sub = can_sub and self.auth.currentData() == "subscription"
        self._cg = can_sub and self.auth.currentData() == "chatgpt"
        self._load()

    def _auth_changed(self, *_):
        self._stash()
        mode = self.auth.currentData()
        self._sub, self._cg = mode == "subscription", mode == "chatgpt"
        self.cfg["auth"][self._pid] = mode
        self._load()

    def _load(self) -> None:
        """按当前的服务商 + 连接方式填界面。"""
        if self._status_job is not None:
            orphan(self._status_job)
            self._status_job = None
        pid, sub, cg = self._pid, self._sub, self._cg
        on = pid in llm.BY_PID
        for w in (self.key, self.b_clear_key, self.model, self.b_refresh, self.base, self.send_image, self.b_test):
            w.setEnabled(on)
        self.key_label.setText(_t("客户端") if sub else "API Key")
        self.key_label.setVisible(not cg)
        self.base_label.setText(_t("客户端路径") if sub else _t("接口地址"))
        self.b_refresh.setToolTip(_t("重新检查客户端的登录状态，并更新模型列表") if sub else _t("从服务商获取可用的模型列表"))
        if cg:
            self.b_refresh.setToolTip(_t("向 ChatGPT 查询这个账号能用的模型"))
        for w in (self.key, self.b_clear_key):
            w.setVisible(not sub and not cg)
        for w in (self.client_state, self.b_login):
            w.setVisible(sub)
        self.cg_box.setVisible(cg)
        for w in (self.base_label, self.base):
            w.setVisible(not cg)                            # 令牌只发给 OpenAI 官方地址，不能填中转
        self.model.setEditable(not cg)
        self.status.setText("")
        self.key.clear()
        self.model.clear()
        self.base.clear()
        if not on:
            self.key.setPlaceholderText("")
            self.key_hint.setText("")
            self.base.setPlaceholderText("")
            self.privacy.setText(_t("本地助手完全在这台电脑上运行，不会发送任何内容。"))
            return
        pv = llm.BY_PID[pid]
        if cg:
            self._load_chatgpt(pv)
            return
        if sub:
            self._load_client(pid)
            return
        src = "saved" if pid in self.new_keys else secrets.key_source(pid)
        self.key.setPlaceholderText({"saved": _t("已保存（不显示）"), "env": _t("使用环境变量 {0}", secrets.ENV[pid])}
                                    .get(src, _t("粘贴 API Key")))
        where = _t("（Windows 加密保存，只有当前用户能读）") if secrets.encrypted() else _t("（保存在本机，未加密）")
        self.key_hint.setText(_t("在 <a href='{0}'>{0}</a> 获取。", pv.key_url) + where)
        self._fill_models(pv.models, self.cfg["models"].get(pid) or pv.models[0])
        self.base.setPlaceholderText(pv.base)
        self.base.setText(self.cfg["base_urls"].get(pid, ""))
        self.privacy.setText(_t("会发送给 {0}：你的问题、当前界面、照片的统计数字（亮度、饱和度、偏色）和最近几轮对话；"
                                "勾选上面一项时还有缩略图。不会发送文件名、位置或照片库信息。", pv.name))

    def _fill_models(self, models, cur: str) -> None:
        self.model.clear()
        self.model.addItems(list(models))
        if self.model.findText(cur) < 0:
            self.model.addItem(cur)
        self.model.setCurrentText(cur)

    # ---- ChatGPT 账号：影匣自己走官方登录，用户在系统浏览器里操作 ----
    def _load_chatgpt(self, pv) -> None:
        self.key_hint.setText(_t("只在你的系统浏览器里登录，影匣看不到密码。登录令牌只发给 OpenAI，保存在本机") +
                              (_t("（Windows 加密保存，只有当前用户能读）") if secrets.encrypted() else _t("（保存在本机，未加密）")) +
                              _t("。这些请求消耗 ChatGPT 计划的用量，和影匣无关，影匣不收费。"))
        self.privacy.setText(_t("会发送给 {0}：你的问题、当前界面、照片的统计数字（亮度、饱和度、偏色）和最近几轮对话；"
                                "勾选上面一项时还有缩略图。不会发送文件名、位置或照片库信息。", pv.name))
        self._fill_chatgpt_models([(m, m) for m in chatgpt.MODELS], self.cfg_chatgpt_model)
        self._cg_show()
        acc = chatgpt.current()
        if acc is not None and acc.sharing:
            self._refresh_models()

    def _fill_chatgpt_models(self, models, cur: str) -> None:
        self.model.clear()
        for shown, slug in models:
            self.model.addItem(shown, slug)
        i = self.model.findData(cur)
        if i < 0:
            self.model.addItem(cur, cur)
            i = self.model.count() - 1
        self.model.setCurrentIndex(i)

    def _cg_show(self) -> None:
        """按当前账号状态显示：还没连接 / 已连接但没授权计划 / 已连接在用计划。"""
        busy = self._login_job is not None
        acc = chatgpt.current()
        signed, sharing = bool(acc and acc.signed_in), bool(acc and acc.sharing)
        if busy:
            text = _t("请在打开的浏览器里完成登录…")
        elif sharing:
            text = _t("✓ 已连接：{0}", acc.email or acc.label)
        elif signed:
            text = _t("⚠ 已连接 {0}，但没有授权使用 ChatGPT 计划，不会发送。点下面的按钮授权，或改用 API Key。", acc.email or acc.label)
        else:
            text = _t("<b>Use your ChatGPT plan</b><br>用 ChatGPT 计划里包含的用量或点数余额完成助手的请求，不用安装 Codex，也不用 API Key。")
        self.cg_state.setText(text)
        self.cg_plan.setVisible(sharing and not busy)
        self.b_cg_login.setVisible(not sharing and not busy)
        self.b_cg_cancel.setVisible(busy)
        self.b_cg_other.setVisible(not busy and acc is not None)
        self.b_cg_out.setVisible(signed and not busy)
        saved = [a for a in chatgpt.accounts() if a.subject]
        self.cg_accounts.blockSignals(True)
        self.cg_accounts.clear()
        for a in saved:
            self.cg_accounts.addItem(a.label, a.client_id)
        if acc is not None:
            self.cg_accounts.setCurrentIndex(max(0, self.cg_accounts.findData(acc.client_id)))
        self.cg_accounts.blockSignals(False)
        self.cg_accounts.setVisible(len(saved) > 1 and not busy)
        # 自动换行的文字不算进窗口的最小高度：明确给足，屏幕矮时按钮也不会被压扁；窗口大小随状态变化
        lay = self.cg_box.layout()
        lay.invalidate()
        self.cg_box.setMinimumHeight(lay.totalHeightForWidth(self.cg_box.width() if self.cg_box.width() > 300 else 524))
        self.layout().activate()
        self.adjustSize()

    def _cg_sign_in(self, new_account: bool = False) -> None:
        if self._login_job is not None:
            return
        acc = chatgpt.current()
        reconsent = bool(not new_account and acc and acc.signed_in and not acc.sharing)    # 用户这次主动要开启计划用量
        cancel = self._login_cancel = threading.Event()
        job = self._login_job = Job(lambda: chatgpt.sign_in(new_account=new_account, reconsent=reconsent, cancel=cancel), self)
        self.status.setText("")

        def finish(msg=None):
            self._login_job = self._login_cancel = None
            if not self._cg:
                return
            self._cg_show()
            if msg:
                self.status.setText("⚠ " + msg)
                return
            acc = chatgpt.current()
            if acc is not None and acc.sharing:
                self.status.setText(_t("✓ 已连接 ChatGPT。"))
                self._cg_welcome()
                self._refresh_models()
        job.ok.connect(lambda _a: finish())
        job.failed.connect(finish)
        self._cg_show()
        job.start()

    def _cg_cancel(self) -> None:
        if self._login_cancel is not None:
            self._login_cancel.set()

    def _cg_pick(self, _i: int) -> None:
        cid = self.cg_accounts.currentData()
        if cid:
            chatgpt.select(cid)
            self.status.setText("")
            self._cg_show()
            acc = chatgpt.current()
            if acc is not None and acc.sharing:
                self._refresh_models()

    def _cg_sign_out(self) -> None:
        self.b_cg_out.setEnabled(False)
        job = self._out_job = Job(chatgpt.sign_out, self)

        def finish(confirmed):
            self._out_job = None
            self.b_cg_out.setEnabled(True)
            if self._cg:
                self._cg_show()
                self.status.setText(_t("已退出。") if confirmed else
                                    _t("本机的登录信息已清除，但没能确认 OpenAI 那边已经断开。可以在 ChatGPT 设置里断开这个应用。"))

        def failed(msg):
            self._out_job = None
            self.b_cg_out.setEnabled(True)
            self.status.setText("⚠ " + msg)
        job.ok.connect(finish)
        job.failed.connect(failed)
        job.start()

    def _cg_welcome(self) -> None:
        """第一次用 ChatGPT 计划登录后说明一次，以后不再显示。"""
        if chatgpt.welcomed():
            return
        chatgpt.set_welcomed()
        d = self._welcome = _Base(self, "You're using your ChatGPT plan", "")
        text = QLabel(_t("助手里符合条件的 AI 请求会使用你的 ChatGPT 计划。用量可以在 ChatGPT 设置里管理。"))
        text.setWordWrap(True)
        d.body.addWidget(text)
        got = TacButton("Got it", "primary", height=32)
        got.clicked.connect(d.accept)
        d.buttons.addWidget(got)
        d.open()

    def _limit_reached(self, message: str) -> None:
        """额度用完：主操作是 Manage usage（OpenAI 的界面要求）。不猜恢复时间，也不改走别的计费方式。"""
        self.status.setText(message)
        self.b_cg_usage.setDefault(True)
        self.b_cg_usage.setFocus()

    # ---- 订阅：只调用用户自己的官方客户端 ----
    def _load_client(self, pid: str) -> None:
        c = clients.CLIENTS[pid]
        self.key_hint.setText(_t("影匣只启动这个客户端，不读取也不保存你的账号和登录信息；回答消耗你的 {0} 订阅额度。", c.plan))
        self._fill_models(c.models, clients.model_of(self.cfg, pid))
        self.base.setPlaceholderText(_t("自动查找；找不到时填 {0} 程序的完整路径", c.name))
        self.base.setText(clients.path_of(self.cfg, pid))
        self.privacy.setText(_t("由本机的 {0} 发送给 {1}：你的问题、当前界面、照片的统计数字（亮度、饱和度、偏色）和最近几轮对话；"
                                "勾选上面一项时还有缩略图。不会发送文件名、位置或照片库信息。数据按你 {1} 账户的设置处理。",
                                c.name, llm.BY_PID[pid].name))
        self._check_client()

    def _watch_client(self, fn) -> None:
        if self._status_job is not None:
            orphan(self._status_job)
        self.b_login.setEnabled(False)
        job = self._status_job = Job(fn, self)
        job.ok.connect(self._show_client)
        job.failed.connect(lambda msg: (self.client_state.setText("⚠ " + msg), self.b_login.setEnabled(True)))
        job.start()

    def _check_client(self) -> None:
        pid, path = self._pid, self.base.text().strip()
        self.client_state.setText(_t("正在检查…"))
        self._watch_client(lambda: clients.status(pid, path))

    def _show_client(self, st) -> None:
        c = clients.CLIENTS[self._pid]
        self.b_login.setEnabled(st.exe is not None)
        if st.exe is None:
            text = _t("没找到 {0}。先<a href='{1}'>安装它</a>，或在下面填路径。", c.name, c.setup_url)
        elif st.subscription:
            text = _t("✓ 已登录 {0} 订阅{1}", c.plan, f"（{st.detail}）" if st.detail else "")
        elif st.signed_in:
            text = _t("⚠ 登录的不是 {0} 订阅，不会发送。点「登录」换成订阅账号。", c.plan)
        else:
            text = _t("已安装，还没登录。点「登录」，在官方窗口里登录 {0} 账号。", c.plan)
        self.client_state.setText(text)

    def _login(self) -> None:
        pid, path = self._pid, self.base.text().strip()
        proc = clients.open_login(pid, path)
        if proc is None:
            self.status.setText(_t("请在终端运行：{0}", clients.login_command(pid)))
            return
        self.client_state.setText(_t("请在弹出的窗口里完成登录…"))
        self._watch_client(lambda: clients.wait_login(pid, proc, path))

    def _key_now(self) -> str:
        pid = self._pid
        return self.key.text().strip() or self.new_keys.get(pid) or secrets.get_key(pid)

    def _clear_key(self) -> None:
        pid = self._pid
        self.new_keys[pid] = ""
        self.key.clear()
        self.key.setPlaceholderText(_t("保存后清除"))

    def _run(self, fn, ok, busy: str) -> None:
        if self._job is not None:
            return
        self.status.setText(busy)
        self.b_test.setEnabled(False)
        self.b_refresh.setEnabled(False)
        job = self._job = Job(fn, self)

        def done():
            self._job = None
            self.b_test.setEnabled(True)
            self.b_refresh.setEnabled(True)
        job.ok.connect(lambda r: (done(), ok(r)))

        def failed(msg):
            done()
            action = getattr(job.error, "action", "")
            if self._cg and action == "manage_usage":
                self._limit_reached(msg)
                return
            self.status.setText("⚠ " + msg)
            if self._cg and action == "sign_in":
                self._cg_show()
        job.failed.connect(failed)
        job.start()

    def _refresh_models(self) -> None:
        pid, key, base = self._pid, self._key_now(), self.base.text().strip() or None
        if self._cg:
            def ok_chatgpt(models):
                if not self._cg:
                    return
                if models:
                    cur = self._model_now()             # 保存的模型这个账号用不了：换成服务器排在第一的
                    self._fill_chatgpt_models(models, cur if cur in [m[1] for m in models] else models[0][1])
                self.status.setText(_t("找到 {0} 个模型。", len(models)))
            self._run(chatgpt.list_models, ok_chatgpt, _t("正在获取模型列表…"))
            return
        if self._sub:
            self._check_client()

            def ok_client(ids):
                self._fill_models(ids, self.model.currentText())
                self.status.setText(_t("找到 {0} 个模型。", len(ids)))
            self._run(lambda: clients.list_models(pid, base or ""), ok_client, _t("正在获取模型列表…"))
            return

        def ok(ids):
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(ids or list(llm.BY_PID[pid].models))
            self.model.setCurrentText(cur)
            self.status.setText(_t("找到 {0} 个模型。", len(ids)))
        self._run(lambda: llm.list_models(pid, key, base), ok, _t("正在获取模型列表…"))

    def _test(self) -> None:
        pid, key, base, model = self._pid, self._key_now(), self.base.text().strip() or None, self._model_now()
        name = llm.BY_PID[pid].name
        sub, cg = self._sub, self._cg

        def fn():
            import time
            t0 = time.monotonic()
            user = llm.user_prompt("曲线在哪？", None, None, 1)
            if cg:
                text, _notes = chatgpt.complete(model, llm.system_prompt(), user)
            elif sub:
                text, _notes = clients.complete(pid, model, base or "", llm.system_prompt(), user)
            else:
                text, _notes = llm.complete(pid, model, key, llm.system_prompt(), user, None, base, 60)
            a = llm.ground(llm.parse_json(text), name)
            return time.monotonic() - t0, a
        self._run(fn, lambda r: self.status.setText(
            _t("✓ 连接正常：{0:.1f} 秒，回答「{1}」", r[0], (r[1].features[0].title if r[1].features else r[1].intro[:30]))),
            _t("正在测试 {0}…", name))

    def _save(self) -> None:
        self._stash()
        for pid, k in self.new_keys.items():
            secrets.set_key(pid, k)
        self.cfg["provider"] = self.provider.currentData()
        if self._pid in llm.SUBSCRIPTION_PIDS:
            self.cfg["auth"][self._pid] = self.auth.currentData()
        self.cfg["chatgpt_model"] = self.cfg_chatgpt_model
        self.cfg["send_image"] = self.send_image.isChecked()
        self.settings.set("assistant_llm", self.cfg)
        self.accept()

    def done(self, r):
        self._cg_cancel()                                  # 正在等浏览器登录：停下来，释放回调端口
        for job in (self._job, self._status_job, self._login_job, self._out_job):      # 后台请求还在跑：不等它，断开回调即可
            if job is not None:
                orphan(job)
        super().done(r)

