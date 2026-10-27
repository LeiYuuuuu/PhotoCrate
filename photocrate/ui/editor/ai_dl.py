"""修图里用到抠图模型时：没下载就先问（说清大小、来源、许可），同意后带进度下载。"""
from __future__ import annotations

from ...ai import matting, models
from ...i18n import t as _t
from ..dialogs import ask, info, run_with_progress


class _Runner:
    """没有 AppContext 时（单独打开修图窗口）也能在后台下载。"""

    def __init__(self):
        self.workers = set()

    def run(self, fn, on_progress=None, on_done=None, on_error=None):
        from ..context import Worker
        w = Worker(fn)
        for sig, cb in ((w.progress, on_progress), (w.done, on_done), (w.error, on_error)):
            if cb:
                sig.connect(cb)
        self.workers.add(w)
        w.finished.connect(lambda: self.workers.discard(w))
        w.start()
        return w


_runner = _Runner()


def ensure(win, key: str, purpose: str, alt: str | None = None) -> bool:
    """模型 key 就绪返回 True。没有就问要不要下载：purpose 说明拿它做什么；alt 是“不下载”按钮的文字
    （比如“先用旧方法”），返回 False 时调用方自己决定是退回旧方法还是什么都不做。"""
    if matting.ready(key):
        return True
    if not models.onnxruntime_ok():
        info(win, _t("缺少 onnxruntime"), _t("AI 抠图需要 onnxruntime：请重新运行 run.bat 自动安装（或 pip install onnxruntime）。"))
        return False
    m = models.MODELS[key]
    size = models.download_size(key) / 1e6
    if not ask(win, _t("下载 AI 模型"),
               _t('{0}需要 <b>{1}</b>（约 {2:.0f} MB）。<br>下载到 {3}，只需下载一次，之后在本机离线运行，照片不会上传。'
                  '<br><br>来源：Hugging Face（失败时自动换国内镜像）；许可：{4}。',
                  purpose, m["label"], size, models.models_dir(), m["license"]),
               ok=_t("下载"), cancel=alt or _t("取消"), en="DOWNLOAD"):
        return False
    ctx, w = None, win
    while w is not None and ctx is None:               # 从对话框里调用时，ctx 在上层的修图窗口上
        ctx = getattr(w, "ctx", None)
        w = w.parent()
    mirror = ctx.settings.get("ai_mirror", "auto") if ctx is not None else "auto"

    def job(prog, cancel):
        return models.download(key, lambda d, t, c: prog((d, t, c)), cancel, mirror)

    _, tb = run_with_progress(ctx if ctx is not None else _runner, win, _t("正在下载模型"), "DOWNLOADING", job,
                              _t("取消后不会留下下载了一半的文件。"))
    matting.unload()
    if tb:
        last = tb.strip().splitlines()[-1]
        info(win, _t("下载失败"), _t('{0}<br><br>可以稍后再试；或手动下载后放进 {1}。', last, models.models_dir()), "ERROR")
        return False
    return matting.ready(key)
