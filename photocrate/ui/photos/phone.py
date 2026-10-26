"""手机互传窗口：开着时在局域网里提供一个网页（lan/server.py），手机扫二维码就能
① 把照片 / 视频传到电脑上的一个文件夹（这个文件夹在自动同步里，相册几秒后就出现）；
② 在手机 / 平板 / 电视的浏览器里看相册（只读，隐藏的照片不给看）。
关掉窗口就停止服务。第一次用时 Windows 防火墙可能会问要不要允许，选“专用网络”即可。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout)

from ..dialogs import _Base, info
from ..widgets.deco import TacButton, caption
from ...core import qr, smart
from ...i18n import t as _t

QR_SIDE = 260


def build_snapshot(lib, marks):
    """网页上能看到的照片（隐藏的不算，新的在前）+ 收藏与我的相簿。在界面线程调用。"""
    from ...lan.server import Item, Snapshot
    gs = smart.newest_first(smart.shown(lib.groups, marks))
    items = [Item(g.gid, g.name, g.date.isoformat() if g.date else "", bool(g.is_video), g.primary,
                  g.primary_file.role, getattr(g, "thumb", None)) for g in gs]
    albums = []
    fav = [g.gid for g in gs if marks.is_fav(g)]
    if fav:
        albums.append(("favorite", _t("收藏"), fav))
    for a in marks.album_list():
        ids = [g.gid for g in gs if marks.in_album(g, a.aid)]
        if ids:
            albums.append((f"user:{a.aid}", a.name, ids))
    return Snapshot(items, albums)


def default_upload_dir(settings) -> str:
    d = settings.get("lan_upload_dir")
    if d:
        return d
    roots = [r for r in settings.get("source_dirs") or [] if r and os.path.isdir(r)]
    base = roots[0] if roots else os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(base, _t("手机上传"))


def qr_pixmap(text: str, side: int = QR_SIDE) -> QPixmap:
    m = qr.encode(text, "M")
    scale = max(2, side // (len(m) + 8))
    img = qr.to_image(m, scale)
    h, w = img.shape
    q = QImage(img.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    return QPixmap.fromImage(q)


class _Bridge(QObject):
    uploaded = Signal(str)                    # 服务线程 → 界面线程


class PhoneDialog(_Base):
    def __init__(self, page):
        super().__init__(page, _t("手机互传"), "PHONE // LAN")
        self.setModal(False)
        self.page = page
        self.ctx = page.ctx
        self.server = None
        self.bridge = _Bridge()
        self.bridge.uploaded.connect(self._uploaded)
        s = self.ctx.settings
        main = QHBoxLayout()
        main.setSpacing(18)
        left = QVBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(QR_SIDE + 8, QR_SIDE + 8)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet("background: #FFFFFF;")
        left.addWidget(self.qr)
        self.ip = QComboBox()
        self.ip.currentIndexChanged.connect(lambda _: self._show_url())
        left.addWidget(self.ip)
        self.url = QLabel("")
        self.url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url.setWordWrap(True)
        left.addWidget(self.url)
        left.addStretch(1)
        main.addLayout(left)
        right = QVBoxLayout()
        right.addWidget(QLabel(_t("<b>手机和电脑连同一个 Wi-Fi</b>，用手机相机或微信扫左边的二维码：")))
        self.cb_up = QCheckBox(_t("允许从手机上传照片 / 视频到电脑"))
        self.cb_up.setChecked(bool(s.get("lan_allow_upload", True)))
        self.cb_browse = QCheckBox(_t("允许在手机 / 平板 / 电视上浏览相册（只读，隐藏的照片不显示）"))
        self.cb_browse.setChecked(bool(s.get("lan_allow_browse", True)))
        for cb in (self.cb_up, self.cb_browse):
            cb.toggled.connect(self._perm)
            right.addWidget(cb)
        right.addSpacing(6)
        right.addWidget(QLabel(_t("上传到：")))
        fr = QHBoxLayout()
        self.dir_lab = QLabel("")
        self.dir_lab.setWordWrap(True)
        self.dir_lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        b_dir = TacButton(_t("更改…"), "secondary", height=28)
        b_dir.clicked.connect(self._pick_dir)
        fr.addWidget(self.dir_lab, 1)
        fr.addWidget(b_dir)
        right.addLayout(fr)
        sr = QHBoxLayout()
        self.sync_lab = caption("", "dim", 8.5)
        self.sync_lab.setWordWrap(True)
        self.b_sync = TacButton(_t("加入自动同步"), "primary", height=28)
        self.b_sync.clicked.connect(self._enable_sync)
        sr.addWidget(self.sync_lab, 1)
        sr.addWidget(self.b_sync)
        right.addLayout(sr)
        right.addSpacing(6)
        self.recv = QLabel(_t("还没有收到文件"))
        self.recv.setWordWrap(True)
        right.addWidget(self.recv)
        right.addStretch(1)
        right.addWidget(caption(_t("只要这个窗口开着就能用，关掉就停止。网址里带一次性的口令，同一个 Wi-Fi 里的其他人猜不到；"
                                   "第一次使用时 Windows 防火墙可能会询问，请允许“专用网络”。"), "faint", 8))
        main.addLayout(right, 1)
        self.body.addLayout(main, 1)
        bc = TacButton(_t("停止并关闭"), "secondary")
        bc.clicked.connect(self.close)
        self.buttons.addWidget(bc)
        self.upload_dir = default_upload_dir(s)
        self.n_recv = 0
        self.resize(760, 420)

    # ---- 服务 ----
    def start(self) -> bool:
        from ...lan.server import LanServer, lan_addresses
        try:
            self.server = LanServer(self.upload_dir, self.cb_up.isChecked(), self.cb_browse.isChecked(),
                                    exiftool=self.ctx.library.exiftool_path,
                                    host=self.ctx.settings.get("lan_host") or "0.0.0.0")
        except OSError as e:
            info(self.page, _t("手机互传"), _t("开不了网页服务：{0}", e), "ERROR")
            return False
        self.server.on_upload = self.bridge.uploaded.emit
        self.refresh()
        self.server.start()
        for ip in lan_addresses():
            self.ip.addItem(ip, ip)
        self._show_url()
        self._update_dir()
        self.ctx.library_changed.connect(self.refresh)
        self.ctx.files_changed.connect(self.refresh)
        return True

    def refresh(self) -> None:
        if self.server is not None:
            self.server.set_snapshot(build_snapshot(self.ctx.library, self.page.marks))

    def _show_url(self) -> None:
        if self.server is None or self.ip.currentData() is None:
            return
        u = self.server.url(self.ip.currentData())
        self.url.setText(u)
        self.qr.setPixmap(qr_pixmap(u))

    def _perm(self) -> None:
        s = self.ctx.settings
        s.set("lan_allow_upload", self.cb_up.isChecked())
        s.set("lan_allow_browse", self.cb_browse.isChecked())
        if self.server is not None:
            self.server.allow_upload = self.cb_up.isChecked()
            self.server.allow_browse = self.cb_browse.isChecked()

    # ---- 上传的文件夹 ----
    def _covered(self) -> bool:
        from ..autosync import covered
        a = self.ctx.autosync
        return a.enabled and covered(self.upload_dir, a.roots(), bool(self.ctx.settings.get("recursive", True)))

    def _update_dir(self) -> None:
        self.dir_lab.setText(self.upload_dir)
        ok = self._covered()
        self.b_sync.setVisible(not ok)
        self.sync_lab.setText(_t("这个文件夹在自动同步里：传上来的照片几秒后就出现在相册。") if ok else
                              _t("这个文件夹还不在自动同步里，传上来的照片不会自动出现在相册。"))

    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, _t("手机上传的照片放到哪个文件夹？"), self.upload_dir)
        if not d:
            return
        self.upload_dir = os.path.normpath(d)
        self.ctx.settings.set("lan_upload_dir", self.upload_dir)
        if self.server is not None:
            self.server.upload_dir = self.upload_dir
        self._update_dir()

    def _enable_sync(self) -> None:
        """把上传文件夹加进照片库的数据源并打开自动同步（和「相册 → 自动同步 → 添加文件夹」一样）。"""
        from ..autosync import covered
        os.makedirs(self.upload_dir, exist_ok=True)
        s = self.ctx.settings
        dirs = [os.path.normpath(x) for x in s.get("source_dirs") or [] if x]
        changed = []
        if not covered(self.upload_dir, dirs, bool(s.get("recursive", True))):
            dirs.append(self.upload_dir)
            s.set("source_dirs", dirs)
            changed.append("source_dirs")
        if not s.get("auto_sync"):
            s.set("auto_sync", True)
            changed.append("auto_sync")
        for k in changed:
            self.ctx.settings_changed.emit(k)
        self._update_dir()

    def _uploaded(self, path: str) -> None:
        self.n_recv += 1
        names = [os.path.basename(p) for p in (self.server.received[-3:] if self.server else [])]
        self.recv.setText(_t("已收到 {0} 个文件：{1}", self.n_recv, _t("、").join(names)))
        self.ctx.status.emit(_t("手机互传：收到 {0}", os.path.basename(path)))

    def closeEvent(self, e) -> None:
        self.stop()
        super().closeEvent(e)

    def done(self, r) -> None:
        self.stop()
        super().done(r)

    def stop(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None
            for sig in (self.ctx.library_changed, self.ctx.files_changed):
                try:
                    sig.disconnect(self.refresh)
                except (RuntimeError, TypeError):
                    pass
            if self.n_recv:
                self.ctx.status.emit(_t("手机互传已停止：这次收到 {0} 个文件", self.n_recv))
                if self._covered():
                    self.ctx.autosync.check_now()


def open_phone(page) -> PhoneDialog | None:
    d = getattr(page, "_phone", None)
    if d is not None and d.server is not None:
        d.show()
        d.raise_()
        d.activateWindow()
        return d
    d = PhoneDialog(page)
    if not d.start():
        return None
    page._phone = d
    d.show()
    return d
