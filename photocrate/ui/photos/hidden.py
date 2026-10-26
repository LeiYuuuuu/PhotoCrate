"""「已隐藏」相簿的密码：打开时输入、设置 / 修改 / 去掉。

密码只存加盐的 PBKDF2 哈希（core/smart.Marks）。这只是本软件里的“不显示”：照片文件还在原来的文件夹，
用资源管理器照样能看到——对话框里如实说明，免得误以为加密了。
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QLabel, QLineEdit

from ..dialogs import _Base
from ..widgets.deco import TacButton, caption
from ...i18n import t as _t

NOTE = _t("隐藏只是让照片不出现在图库、相簿、搜索和局域网相册里，照片文件还在原来的文件夹，没有加密。")


def _pw_field(placeholder: str) -> QLineEdit:
    e = QLineEdit()
    e.setEchoMode(QLineEdit.EchoMode.Password)
    e.setPlaceholderText(placeholder)
    e.setMinimumWidth(320)
    return e


def unlock(parent, marks) -> bool:
    """没设密码直接通过；设了就要求输入。成功后 marks.unlocked = True。"""
    if not marks.has_password():
        marks.unlocked = True
        return True
    d = _Base(parent, _t("已隐藏的照片"), "LOCKED")
    d.body.addWidget(QLabel(_t("输入密码查看已隐藏的照片")))
    e = _pw_field(_t("密码"))
    d.body.addWidget(e)
    err = caption("", "dim", 8.5)
    err.setStyleSheet("color: #D9534F;")
    d.body.addWidget(err)
    d.body.addWidget(caption(_t("忘记密码：把照片数据文件夹里 annotations.db 的 lock 表清空即可（照片不受影响）。"), "faint", 8))
    bc = TacButton(_t("取消"), "secondary")
    bo = TacButton(_t("打开"), "primary", en="UNLOCK")
    bc.clicked.connect(d.reject)

    def go():
        if marks.check_password(e.text()):
            d.accept()
        else:
            err.setText(_t("密码不对"))
            e.selectAll()
            e.setFocus()
    bo.clicked.connect(go)
    e.returnPressed.connect(go)
    d.buttons.addWidget(bc)
    d.buttons.addWidget(bo)
    e.setFocus()
    if d.exec() != QDialog.DialogCode.Accepted:
        return False
    marks.unlocked = True
    return True


def set_password(parent, marks) -> bool:
    """设置或修改密码（留空 = 不用密码）。改之前已经解锁过，所以不再问旧密码。返回是否改了。"""
    has = marks.has_password()
    d = _Base(parent, _t("修改密码") if has else _t("给「已隐藏」设密码"), "PASSWORD")
    d.body.addWidget(QLabel(_t("以后打开「已隐藏」相簿时要输入这个密码。两栏都留空 = 不用密码。")))
    e1 = _pw_field(_t("新密码"))
    e2 = _pw_field(_t("再输一次"))
    d.body.addWidget(e1)
    d.body.addWidget(e2)
    err = caption("", "dim", 8.5)
    err.setStyleSheet("color: #D9534F;")
    d.body.addWidget(err)
    d.body.addWidget(caption(NOTE, "faint", 8))
    bc = TacButton(_t("取消"), "secondary")
    bo = TacButton(_t("确定"), "primary")
    bc.clicked.connect(d.reject)

    def go():
        if e1.text() != e2.text():
            err.setText(_t("两次输入的不一样"))
            return
        d.accept()
    bo.clicked.connect(go)
    e2.returnPressed.connect(go)
    d.buttons.addWidget(bc)
    d.buttons.addWidget(bo)
    e1.setFocus()
    if d.exec() != QDialog.DialogCode.Accepted:
        return False
    marks.set_password(e1.text() or None)
    return True
