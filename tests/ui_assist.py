"""小匣助手的界面自动化测试（无界面模式）：在真实的主窗口 / 修图窗口里核对知识库，并走一遍面板。

用法：QT_QPA_PLATFORM=offscreen python tests/ui_assist.py <输出目录> [light|dark]
核对：每个修图菜单路径都能在菜单栏里逐级找到、每个工具 id 存在且提示文字对得上、每个页面 id 和页面标题对得上。
面板：打招呼 → 找功能（带我去：切页面 / 选工具 / 展开菜单）→ 调色方案「应用为调整图层」→ 诊断当前照片。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HOME = tempfile.mkdtemp(prefix="pc_ui_assist_")
os.environ["PHOTOCRATE_HOME"] = os.path.join(HOME, "home")

EDITOR_MENUS = ("文件", "编辑", "图像", "图层", "选择", "滤镜", "视图", "窗口", "帮助")


def sample(path: str) -> str:
    """偏暖、有点发灰的风景：天空渐变 + 地面。"""
    import numpy as np
    from PIL import Image
    h, w = 600, 900
    y = np.linspace(0, 1, h)[:, None, None]
    sky = np.array([0.70, 0.72, 0.74]) * (1 - 0.3 * y) + 0.12
    ground = np.array([0.42, 0.40, 0.22])
    img = np.where(y < 0.55, sky, ground) * np.ones((h, w, 3))
    img = img * np.array([1.0, 0.9, 0.72]) + np.random.default_rng(2).normal(0, 0.02, (h, w, 3))
    img = 0.25 + 0.55 * np.clip(img, 0, 1)                      # 压到 25%–80%：发灰
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(path, quality=92)
    return path


def main(out: str, theme_name: str = "light") -> int:
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication

    from photocrate import i18n
    i18n.set_language(os.environ.get("PHOTOCRATE_LANG"))
    from photocrate.i18n import t as _t
    from photocrate.assist.features import DESCRIPTIVE, FEATURES, PHOTOS, BY_ID
    from photocrate.bootstrap import bootstrap
    from photocrate.config import Settings
    from photocrate.core.library import Library
    from photocrate.ops.journal import Journal, journal_path
    from photocrate.ui import theme
    from photocrate.ui.assistant import find_action, show_menu
    from photocrate.ui.context import AppContext
    from photocrate.ui.main_window import MainWindow

    os.makedirs(out, exist_ok=True)
    img = sample(os.path.join(HOME, "风景.jpg"))
    app = QApplication(sys.argv)
    s = bootstrap(Settings())
    s.set("theme", theme_name)
    s.set("parallel_mode", "thread")
    s.set("editor_tour_done", True)
    theme.apply(app, theme_name)
    lib = Library(s)
    ctx = AppContext(s, lib, Journal(journal_path()))
    win = MainWindow(ctx)
    win.resize(1600, 980)
    win.show()
    panel = win.assistant
    result = {"ok": False}

    def log(msg):
        print("  ·", msg, flush=True)

    def shot(name, widget=None):
        app.processEvents()
        (widget or win).grab().save(os.path.join(out, f"{theme_name}_{name}.png"))

    def last_plan():
        return panel.plans[-1]

    def script():
        # ---- 相册模式：打开面板、打招呼、找功能 ----
        assert win.mode() == 0
        panel.toggle()
        yield 200
        assert panel.isVisible() and panel.geometry().right() <= win.width()
        shot("assist_01_open")
        panel.ask("批量调色怎么弄")
        panel.ask("怎么按日期整理文件夹")
        yield 150
        shot("assist_02_find")
        panel._go(BY_ID["partition"])
        yield 200
        assert win.mode() == 0 and win.page_ids[win.stack.currentIndex()] == "partition"
        log("带我去：切到「分区编排」页")

        # ---- 页面 id / 标题 ----
        titles = {pid: w.title for pid, w in zip(win.page_ids, win.pages) if hasattr(w, "title")}
        n = 0
        for f in FEATURES:
            if f.go and f.go[0] == "page":
                assert f.go[1] in win.page_ids, (f.id, f.go)
                if f.path[0] == PHOTOS and f.path[1] not in DESCRIPTIVE:
                    assert _t(f.path[1]) == titles[f.go[1]], (f.id, f.path[1], titles[f.go[1]])
                n += 1
        log(f"{n} 个相册功能的页面 id、页面标题都对得上")

        # ---- 修图：打开照片，核对菜单与工具 ----
        win.studio.open_path(img)
        yield 600
        ew = win.studio.current_editor()
        assert ew is not None
        win.set_mode(1)
        yield 300
        nm = nt = 0
        for f in FEATURES:
            if f.path[0] != PHOTOS and len(f.path) > 2 and f.path[1] in EDITOR_MENUS:
                assert find_action(ew.menuBar(), f.path[1:]) is not None, (f.id, f.path)
                nm += 1
            if f.go and f.go[0] in ("menu", "show"):
                a = find_action(ew.menuBar(), f.go[1])
                assert a is not None, (f.id, f.go)
                if f.go[0] == "menu":
                    assert a.menu() is None, (f.id, "menu 只能指向菜单项")
            if f.go and f.go[0] == "tool":
                assert f.go[1] in ew.tools, (f.id, f.go)
                assert ew.tools[f.go[1]].tip.startswith(_t(f.path[-1]).rstrip("）")), (f.id, ew.tools[f.go[1]].tip[:30])
                nt += 1
        log(f"{nm} 条修图菜单路径在菜单栏里逐级找到；{nt} 个工具 id 和提示文字对得上")

        # ---- 带我去：工具、菜单 ----
        panel._go(BY_ID["heal"])
        yield 100
        assert ew.tool.id == "heal", ew.tool.id
        n0 = len(ew.doc.layers)
        panel._go(BY_ID["curves"])                     # 执行菜单：新建「曲线」调整图层
        yield 200
        assert len(ew.doc.layers) == n0 + 1 and ew.doc.layers[-1].kind == "adjust"
        log("带我去：选中污点修复画笔；执行「图像 → 调整 → 曲线」新建了调整图层")
        show_menu(ew.menuBar(), ("选择",))
        yield 200
        m = find_action(ew.menuBar(), ("选择",)).menu()
        assert m.isVisible()
        shot("assist_03_show_menu")
        m.hide()
        yield 100

        # ---- 诊断（画布上看到的样子；先把刚才的曲线图层删掉）----
        ew.doc.delete_layer(ew.doc.layers[-1].id)
        yield 100
        panel.ask("这张照片怎么修")
        yield 200
        plan = last_plan()
        keys = [ln.key for ln in plan.lines]
        assert "lv_lo" in keys and "temp" in keys and plan.params["temp"] < 0, plan.lines
        shot("assist_05_diagnose")
        log("诊断：发现发灰（拉黑场 / 白场）和偏暖（色温 " + f"{plan.params['temp']:.0f}）")

        # ---- 调色方案：按这张照片收放，应用为调整图层 ----
        panel.ask("太灰了，想通透一些")
        yield 150
        plan = last_plan()
        assert plan.params["lv_lo"] > 0, plan.lines        # 这张最暗处只到 25%：建议拉黑场
        assert any("发灰" in n for n in plan.notes), plan.notes
        panel.ask("天空更蓝一点，不要太暖")
        yield 150
        plan = last_plan()
        n0 = len(ew.doc.layers)
        panel._link(QUrl(f"apply:{len(panel.plans) - 1}"))
        yield 300
        assert len(ew.doc.layers) == n0 + 1
        l = ew.doc.layers[-1]
        assert l.kind == "adjust" and l.params["temp"] < 0 and l.params["hsl"]["blue"][1] > 0, l.params
        shot("assist_04_apply")
        log(f"应用为调整图层「{l.name}」：色温 {l.params['temp']:.0f}，蓝色饱和度 +{l.params['hsl']['blue'][1]:.0f}")

        panel.ask("今天天气怎么样")
        yield 100
        shot("assist_06_unknown", panel)

        # ---- 大模型：本机假服务器扮演 Claude ----
        from fake_llm import Server
        from photocrate.assist import llm, secrets
        from photocrate.ui.assistant_llm import LLMDialog
        srv = Server()
        try:
            secrets.set_key("claude", "sk-test")
            s.set("assistant_llm", {"provider": "claude", "models": {"claude": "claude-opus-5-5"},
                                    "base_urls": {"claude": srv.base}, "send_image": True})
            dlg = LLMDialog(win, s)
            dlg.show()
            yield 200
            assert dlg.provider.currentData() == "claude" and dlg.model.currentText() == "claude-opus-5-5"
            assert "已保存" in dlg.key.placeholderText()
            shot("assist_07_llm_settings", dlg)
            dlg._save()
            yield 100
            panel.cfg = llm.load_config(s)
            panel.b_model.setText("Claude · claude-opus-5-5")
            n_plans = len(panel.plans)
            panel.ask("想要通透一点，曲线在哪")
            for _ in range(100):
                if panel._job is None:
                    break
                yield 100
            assert panel._job is None, "大模型请求没有结束"
            req = srv.requests[-1]["body"]
            assert req["messages"][0]["content"][0]["type"] == "image"          # 勾了附带缩略图
            assert len(panel.plans) == n_plans + 1 and panel.plans[-1].params["blacks"] == -100
            text = panel.view.toPlainText()
            assert "Claude · claude-opus-5-5 的回答" in text and "魔术橡皮" in text, text[-600:]
            shot("assist_08_llm_answer")
            n0 = len(ew.doc.layers)
            panel._link(QUrl(f"apply:{len(panel.plans) - 1}"))
            yield 200
            assert len(ew.doc.layers) == n0 + 1 and ew.doc.layers[-1].params["contrast"] == 18
            log("大模型（假服务器）：回答渲染、参数校验（越界夹回、编造的名字标出）、应用为调整图层")

            panel.cfg["models"]["claude"] = "bad-key"
            panel.ask("曲线在哪")
            for _ in range(100):
                if panel._job is None:
                    break
                yield 100
            text = panel.view.toPlainText()
            assert "没有回答成功" in text and "API Key 无效" in text and "先看本地助手的回答" in text, text[-400:]
            shot("assist_09_llm_fallback", panel)
            log("大模型失败（Key 无效）时显示原因，并退回本地回答")

            # ---- 订阅：用假的客户端状态走一遍设置界面和提问 ----
            from photocrate.assist import clients
            from photocrate.ui.assistant_llm import label as llm_label
            real = clients.status, clients.complete
            states = [(clients.Status(), "没找到 Claude Code"),
                      (clients.Status("claude.exe"), "还没登录"),
                      (clients.Status("claude.exe", True, False, "console"), "不是 Claude 订阅"),
                      (clients.Status("claude.exe", True, True, "max"), "已登录 Claude 订阅（max）")]
            try:
                dlg = LLMDialog(win, s)
                dlg.show()
                yield 100
                assert dlg.auth.isVisible() and dlg.auth.count() == 2 and not dlg.client_state.isVisible()
                dlg.provider.setCurrentIndex(dlg.provider.findData("deepseek"))
                yield 50
                assert not dlg.auth.isVisible() and dlg.key.isVisible()         # 其他服务商没有订阅连接
                dlg.provider.setCurrentIndex(dlg.provider.findData("claude"))
                yield 50
                for st, word in states:
                    clients.status = lambda *_a, st=st: st
                    dlg.auth.setCurrentIndex(0)
                    dlg.auth.setCurrentIndex(1)
                    for _ in range(50):
                        if word in dlg.client_state.text():
                            break
                        yield 20
                    assert word in dlg.client_state.text(), dlg.client_state.text()
                    assert not dlg.key.isVisible() and dlg.b_login.isVisible()
                    assert dlg.b_login.isEnabled() == (st.exe is not None)
                assert dlg.model.currentText() == "sonnet" and dlg.base_label.text() == "客户端路径"
                shot("assist_10_subscription", dlg)
                dlg._save()
                yield 100
                saved = s.get("assistant_llm")
                assert saved["auth"]["claude"] == "subscription" and saved["client_models"]["claude"] == "sonnet"
                assert saved["models"]["claude"]                               # API 那套设置还在
                panel.cfg = llm.load_config(s)
                assert llm_label(panel.cfg) == "Claude 订阅 · sonnet"
                clients.complete = lambda *_a, **_k: (
                    '{"reply": "曲线在调整图层里", "features": ["curves"], "params": [], "steps": []}', [])
                panel.ask("曲线在哪")
                for _ in range(100):
                    if panel._job is None:
                        break
                    yield 100
                text = panel.view.toPlainText()
                assert "曲线在调整图层里" in text and "订阅" in text, text[-400:]
                shot("assist_11_subscription_answer", panel)
                log("订阅：四种客户端状态的提示、保存设置、提问走客户端")
            finally:
                clients.status, clients.complete = real

            # ---- ChatGPT 账号：假的 OpenAI 登录服务，浏览器也是假的（真实登录由用户本人在浏览器里做）----
            from types import SimpleNamespace

            from fake_llm import ChatGPTServer
            from photocrate.assist import chatgpt
            from photocrate.ui import assistant_llm
            cg = ChatGPTServer()
            opened = []
            real_cg = (chatgpt.ENDPOINTS, chatgpt._open_browser, chatgpt.CALLBACK_PORT, assistant_llm.QDesktopServices)
            chatgpt.ENDPOINTS, chatgpt._open_browser, chatgpt.CALLBACK_PORT = cg.endpoints(), cg.browser, 0
            assistant_llm.QDesktopServices = SimpleNamespace(openUrl=lambda u: opened.append(u.toString()))

            def wait(cond):
                for _ in range(300):
                    if cond():
                        return True
                    yield 20
                return False

            try:
                s.set("assistant_llm", {"provider": "openai", "auth": {"openai": "chatgpt"}, "send_image": False})
                dlg = LLMDialog(win, s)
                dlg.show()
                dlg.resize(560, 680)                     # 无界面模式的“屏幕”只有 800 高，窗口被限制在 533
                yield 150
                assert dlg.auth.count() == 3 and dlg.auth.currentData() == "chatgpt" and dlg.cg_box.isVisible()
                assert dlg.b_cg_login.isVisible() and dlg.b_cg_login.text() == "Continue with ChatGPT"
                assert not dlg.b_cg_login.icon().isNull()
                assert not dlg.key.isVisible() and not dlg.base.isVisible() and not dlg.cg_plan.isVisible()
                assert "Use your ChatGPT plan" in dlg.cg_state.text()
                assert dlg.b_cg_login.height() == 45 and dlg.cg_box.height() >= dlg.cg_state.height() + 45    # 没有被挤扁
                assert not dlg.key_label.isVisible()
                shot("assist_12_chatgpt_signed_out", dlg)
                dlg.provider.setCurrentIndex(dlg.provider.findData("claude"))     # Claude 没有内置登录
                yield 50
                assert dlg.auth.findData("chatgpt") < 0 and not dlg.cg_box.isVisible()
                dlg.provider.setCurrentIndex(dlg.provider.findData("openai"))
                yield 50
                assert dlg.auth.currentData() == "chatgpt"

                cg.browser_opened = False                                       # 浏览器打不开时给出原因
                dlg.b_cg_login.click()
                yield from wait(lambda: dlg._login_job is None)
                assert "浏览器" in dlg.status.text() and dlg.b_cg_login.isVisible(), dlg.status.text()
                cg.browser_opened = True

                dlg.b_cg_login.click()
                assert dlg.b_cg_cancel.isVisible() or dlg._login_job is None
                yield from wait(lambda: dlg._login_job is None and dlg.cg_plan.isVisible())
                assert "已连接" in dlg.cg_state.text() and "alice@example.com" in dlg.cg_state.text(), dlg.cg_state.text()
                assert dlg.cg_plan.isVisible() and not dlg.b_cg_login.isVisible() and dlg.b_cg_out.isVisible()
                assert dlg.b_cg_usage.text() == "Manage usage"
                dlg.resize(560, 680)
                yield 50
                assert dlg.cg_box.height() >= dlg.cg_plan.height() + dlg.b_cg_out.height()
                assert dlg._welcome is not None and dlg._welcome.isVisible()    # 第一次登录说明一次
                assert dlg._welcome.windowTitle() == "You're using your ChatGPT plan"
                shot("assist_13_chatgpt_welcome", dlg._welcome)
                dlg._welcome.accept()
                yield from wait(lambda: dlg.model.findData("gpt-fake") >= 0)
                assert dlg.model.itemText(dlg.model.findData("gpt-fake")) == "GPT Fake"        # 显示名称，实际用 slug
                assert dlg.model.findData("gpt-hidden") < 0 and dlg.model.currentData() == "gpt-fake"
                dlg.model.setCurrentIndex(dlg.model.findData("gpt-fake"))
                shot("assist_14_chatgpt_connected", dlg)
                dlg.b_cg_usage.click()
                assert opened == ["https://chatgpt.com/settings/usage"], opened

                dlg.b_test.click()
                yield from wait(lambda: dlg._job is None and "连接正常" in dlg.status.text())
                assert "连接正常" in dlg.status.text(), dlg.status.text()
                req = [r for r in cg.requests if r["path"] == "/v1/responses"][-1]["body"]
                assert req["store"] is False and req["stream"] is True and req["model"] == "gpt-fake"
                dlg._save()
                yield 100
                saved = s.get("assistant_llm")
                assert saved["auth"]["openai"] == "chatgpt" and saved["chatgpt_model"] == "gpt-fake"
                assert "access-" not in str(saved) and "refresh-" not in str(saved)

                panel.cfg = llm.load_config(s)
                panel.update_plan_row()
                panel.b_model.setText(llm_label(panel.cfg))
                assert llm_label(panel.cfg) == "ChatGPT plan · gpt-fake" and panel.plan_row.isVisibleTo(panel)
                panel.ask("曲线在哪")
                yield from wait(lambda: panel._job is None)
                text = panel.view.toPlainText()
                assert "gpt-fake" in text and "ChatGPT plan" in text and "魔术橡皮" in text, text[-400:]
                shot("assist_15_chatgpt_answer", panel)

                panel.cfg["chatgpt_model"] = "usage-limit"
                panel.ask("色阶在哪")
                yield from wait(lambda: panel._job is None)
                text = panel.view.toPlainText()
                assert "Usage limit reached" in text and "Manage usage" in text and "先看本地助手的回答" in text, text[-400:]
                shot("assist_16_chatgpt_limit", panel)
                opened.clear()
                panel._link(QUrl("usage:"))
                panel.b_usage.click()
                assert opened == ["https://chatgpt.com/settings/usage"] * 2
                log("ChatGPT 账号：登录、已连接、Using ChatGPT plan、Manage usage、提问、额度用完")

                # 登录过期（refresh token 不能用了）：设置里回到 Continue with ChatGPT，client ID 留着
                st = chatgpt._load()
                st["accounts"][st["active"]]["expires_at"] = 0
                chatgpt._save(st)
                cg.refresh_error = (400, "refresh_token_expired")
                panel.cfg["chatgpt_model"] = "gpt-fake"
                panel.ask("色阶在哪")
                yield from wait(lambda: panel._job is None)
                assert "没有回答成功" in panel.view.toPlainText() and not panel.plan_row.isVisibleTo(panel)
                cg.refresh_error = None
                dlg = LLMDialog(win, s)
                dlg.show()
                yield 100
                assert dlg.b_cg_login.isVisible() and not dlg.cg_plan.isVisible() and dlg.b_cg_other.isVisible()
                dlg.b_cg_login.click()
                yield from wait(lambda: dlg._login_job is None and dlg.cg_plan.isVisible())
                assert dlg.cg_plan.isVisible() and dlg._welcome is None         # 第二次不再说明
                assert cg.authorize_queries[-1]["client_id"] == "oaiapp_fakeclient1"
                dlg._cg_cancel()

                # 没授权计划：不发送，按钮用来授权
                cg.grant_scope = "openid profile email offline_access"
                dlg.b_cg_other.click()
                yield from wait(lambda: dlg._login_job is None and "没有授权" in dlg.cg_state.text())
                assert "没有授权" in dlg.cg_state.text() and dlg.b_cg_login.isVisible() and not dlg.cg_plan.isVisible()
                assert dlg.cg_accounts.count() == 1 or dlg.cg_accounts.isVisible()
                cg.grant_scope = "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke"
                dlg.b_cg_login.click()
                yield from wait(lambda: dlg._login_job is None and dlg.cg_plan.isVisible())
                assert cg.authorize_queries[-1].get("prompt") == "consent"

                # 退出：本地令牌清掉，client ID 留着
                dlg.b_cg_out.click()
                yield from wait(lambda: dlg._out_job is None and dlg.b_cg_login.isVisible())
                assert dlg.b_cg_login.isVisible() and "已退出" in dlg.status.text(), dlg.status.text()
                dlg.resize(560, 680)
                yield 50
                shot("assist_17_chatgpt_signed_out_again", dlg)
                dlg.reject()
                panel.update_plan_row()
                assert not panel.plan_row.isVisibleTo(panel)
                log("ChatGPT 账号：过期重登、没授权计划、重新授权、退出登录")
            finally:
                chatgpt.ENDPOINTS, chatgpt._open_browser, chatgpt.CALLBACK_PORT, assistant_llm.QDesktopServices = real_cg
                cg.close()
        finally:
            srv.close()
            secrets.set_key("claude", "")
        panel.hide()
        ew.doc.modified = False
        ew.close()
        yield 300
        result["ok"] = True

    steps = script()

    def pump():
        try:
            delay = next(steps)
        except StopIteration:
            app.quit()
            return
        except Exception:
            traceback.print_exc()
            shot("assist_FAILED")
            app.quit()
            return
        QTimer.singleShot(delay or 50, pump)

    QTimer.singleShot(400, pump)
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    lib.close()
    print("RESULT:", "PASS" if result["ok"] else "FAIL", "· screenshots in", out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "light"))
