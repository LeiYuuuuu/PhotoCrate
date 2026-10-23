# 影匣 UI 精修 · 2026-10-23

浅色使用灰白工作区、炭黑导航与工业黄；深色使用冷灰蓝面板、暖黄主操作与青色信息标识。减少重复角标，保留编号、切角和终端排版。照片颜色不作改变。

## 改动文件

- `photocrate/ui/theme.py`：两套配色、表单及表头样式、普通按钮焦点样式。
- `photocrate/ui/main_window.py`：导航滑动定位条、键盘导航、页面快照淡出、导航页脚布局。
- `photocrate/ui/widgets/deco.py`：面板角标、卡片层级、窄卡片标题避让、按钮动态反馈和进度扫光。
- `photocrate/ui/motion.py`：可即时关闭的短时动画，隐藏控件停止动画，快速切页清理旧快照。
- `photocrate/ui/pages/settings_page.py`、`photocrate/config.py`：持久化的界面动效开关。
- `photocrate/ui/pages/source_page.py`：小窗口资源接入区可滚动，统计卡片不再互相覆盖。
- `tests/test_ui_design.py`：4 项原生 Qt 交互测试（包含两套主题）。
- `scripts/preview_ui.py`：隔离数据的可重复本地预览。
- `README.md`、`ARCHITECTURE.md`：外观、动效与验证说明。

## 查看

- [浅色资源接入](source-light.png)
- [深色资源接入](source-dark.png)
- [浅色分区编排](partition-light.png)
- [深色分区编排](partition-dark.png)
- [浅色设置](settings-light.png)
- [深色设置](settings-dark.png)
- [小窗口浅色](source-small-light.png)
- [小窗口深色](source-small-dark.png)

截图由原生 Windows Qt 窗口生成并人工查看。图片均为标注 UI TEST 的合成测试图，不读取用户照片。

交互预览：在项目目录执行 `.venv\Scripts\python scripts\preview_ui.py --hold`。程序使用临时 PHOTOCRATE_HOME，预览中的扫描和后续手动操作只涉及测试数据。
正常使用仍双击 `run.bat`。动效开关在系统设置的外观区，立即生效；主题切换沿用原来的重启流程。

## 验证结果与限制

- 新增测试：`python -m pytest tests/test_ui_design.py -q`，4 passed。
- 全量：69 passed、9 skipped、2 failed、5 errors。
- 对照验证：非 UI 旧测试使用修改前的配置模块运行，65 passed、9 skipped、同样 2 failed 和 5 errors。
- 失败：移动后元数据缓存计数预期与实际不一致；Windows 路径分隔符与测试字符串不同。
- 报错：5 个旧用例使用 Linux 回收站实现，在 Windows 上访问不存在的 `os.environb`。
- 跳过项依赖额外的 ExifTool 测试配置或 AI 模型。没有为本次 UI 修改调整旧业务测试或照片处理逻辑。
- 检查了两套主题的全部 11 个页面切换；截图重点覆盖资源接入、分区编排和设置。未穷举多显示器、所有 DPI、第三方插件和每个对话框。
- 测试环境补装 pytest、piexif 到项目 `.venv`，运行依赖清单未修改。

修改前的文件另有本地备份；这次没有重新打包 exe。
