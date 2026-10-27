"""小匣助手的大模型设置：选服务商（本地 / Claude / DeepSeek / GPT / Grok / Kimi）、API Key、模型、接口地址、是否附带缩略图。

网络请求都在后台线程（Job）里做，界面不卡。Key 存在 assist/secrets.py 管的单独文件里（Windows 上 DPAPI 加密）。
"""
from __future__ import annotations

import io

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QLabel, QLineEdit, QPushButton

from ..assist import llm, secrets
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
        _LIVE.add(self)
        self.finished.connect(lambda: _LIVE.discard(self))

    def run(self):
        try:
            self.ok.emit(self.fn())
        except llm.LLMError as e:
            self.failed.emit(str(e))
        except Exception as e:                           # 不让意外错误留在后台线程里
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


def label(cfg: dict) -> str:
    pid = cfg.get("provider", "local")
    if pid not in llm.BY_PID:
        return _t("本地 · 不联网")
    return f"{llm.BY_PID[pid].name} · {llm.model_of(cfg, pid)}"


class LLMDialog(_Base):
    def __init__(self, parent, settings):
        super().__init__(parent, _t("助手用的模型"), "ASSISTANT MODEL")
        self.settings = settings
        self.cfg = llm.load_config(settings)
        self.cfg["models"] = dict(self.cfg.get("models") or {})
        self.cfg["base_urls"] = dict(self.cfg.get("base_urls") or {})
        self.new_keys: dict[str, str] = {}
        self._job = None
        self.setMinimumWidth(560)
        p = theme.current()

        intro = QLabel(_t("默认用本地助手：不联网、回答都来自核对过的功能目录。选一个大模型后，它能理解更随意的说法、"
                          "回答知识库里没有的问题；它给出的功能和参数仍会按影匣的功能目录和滑块范围校验，失败时自动退回本地回答。"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {p.ink2};")
        self.body.addWidget(intro)

        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel(_t("服务商")), 0, 0)
        self.provider = QComboBox()
        self.provider.addItem(_t("本地（不联网）"), "local")
        for pv in llm.PROVIDERS:
            self.provider.addItem(pv.name, pv.id)
        g.addWidget(self.provider, 0, 1, 1, 2)

        g.addWidget(QLabel("API Key"), 1, 0)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        g.addWidget(self.key, 1, 1)
        self.b_clear_key = QPushButton(_t("清除"))
        self.b_clear_key.clicked.connect(self._clear_key)
        g.addWidget(self.b_clear_key, 1, 2)
        self.key_hint = QLabel()
        self.key_hint.setOpenExternalLinks(True)
        self.key_hint.setStyleSheet(f"color: {p.ink3}; font-size: 8.5pt;")
        self.key_hint.setWordWrap(True)
        g.addWidget(self.key_hint, 2, 1, 1, 2)

        g.addWidget(QLabel(_t("模型")), 3, 0)
        self.model = QComboBox()
        self.model.setEditable(True)
        g.addWidget(self.model, 3, 1)
        self.b_refresh = QPushButton(_t("刷新"))
        self.b_refresh.setToolTip(_t("从服务商获取可用的模型列表"))
        self.b_refresh.clicked.connect(self._refresh_models)
        g.addWidget(self.b_refresh, 3, 2)

        g.addWidget(QLabel(_t("接口地址")), 4, 0)
        self.base = QLineEdit()
        g.addWidget(self.base, 4, 1, 1, 2)
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
        self.provider.currentIndexChanged.connect(self._switch)
        i = self.provider.findData(self.cfg.get("provider", "local"))
        self.provider.setCurrentIndex(max(0, i))
        self._switch()

    # ---- 每个服务商一套设置，切换时先记下当前这套 ----
    def _stash(self) -> None:
        pid = self._pid
        if pid in llm.BY_PID:
            self.cfg["models"][pid] = self.model.currentText().strip()
            b = self.base.text().strip()
            if b:
                self.cfg["base_urls"][pid] = b
            else:
                self.cfg["base_urls"].pop(pid, None)
            if self.key.text().strip():
                self.new_keys[pid] = self.key.text().strip()

    def _switch(self, *_):
        self._stash()
        pid = self.provider.currentData()
        self._pid = pid
        on = pid in llm.BY_PID
        for w in (self.key, self.b_clear_key, self.model, self.b_refresh, self.base, self.send_image, self.b_test):
            w.setEnabled(on)
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
        src = "saved" if pid in self.new_keys else secrets.key_source(pid)
        self.key.setPlaceholderText({"saved": _t("已保存（不显示）"), "env": _t("使用环境变量 {0}", secrets.ENV[pid])}
                                    .get(src, _t("粘贴 API Key")))
        where = _t("（Windows 加密保存，只有当前用户能读）") if secrets.encrypted() else _t("（保存在本机，未加密）")
        self.key_hint.setText(_t("在 <a href='{0}'>{0}</a> 获取。", pv.key_url) + where)
        self.model.addItems(list(pv.models))
        cur = self.cfg["models"].get(pid) or pv.models[0]
        if self.model.findText(cur) < 0:
            self.model.addItem(cur)
        self.model.setCurrentText(cur)
        self.base.setPlaceholderText(pv.base)
        self.base.setText(self.cfg["base_urls"].get(pid, ""))
        self.privacy.setText(_t("会发送给 {0}：你的问题、当前界面、照片的统计数字（亮度、饱和度、偏色）和最近几轮对话；"
                                "勾选上面一项时还有缩略图。不会发送文件名、位置或照片库信息。", pv.name))

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
        job.failed.connect(lambda msg: (done(), self.status.setText("⚠ " + msg)))
        job.start()

    def _refresh_models(self) -> None:
        pid, key, base = self._pid, self._key_now(), self.base.text().strip() or None

        def ok(ids):
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(ids or list(llm.BY_PID[pid].models))
            self.model.setCurrentText(cur)
            self.status.setText(_t("找到 {0} 个模型。", len(ids)))
        self._run(lambda: llm.list_models(pid, key, base), ok, _t("正在获取模型列表…"))

    def _test(self) -> None:
        pid, key, base, model = self._pid, self._key_now(), self.base.text().strip() or None, self.model.currentText().strip()
        name = llm.BY_PID[pid].name

        def fn():
            import time
            t0 = time.monotonic()
            text, _notes = llm.complete(pid, model, key, llm.system_prompt(),
                                        llm.user_prompt("曲线在哪？", None, None, 1), None, base, 60)
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
        self.cfg["send_image"] = self.send_image.isChecked()
        self.settings.set("assistant_llm", self.cfg)
        self.accept()

    def done(self, r):
        if self._job is not None:                          # 后台请求还在跑：不等它，断开回调即可
            orphan(self._job)
        super().done(r)

