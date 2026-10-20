# 影匣 PhotoCrate · 架构与设计思路

> 写给想读懂、修改或扩展这个软件的人（包括让 AI 帮你写代码时，把这份文档一起发给它）。

## 1. 核心思路：三件事分开做

整个软件只做三件事，而且严格分开：

1. **理解照片**（扫描 + 分析）：读出每张照片的日期、地点、色调、相机……存成“特征”。这一步**只读不写**，结果有缓存。
2. **决定去哪**（分区 + 计划）：根据用户选的条件，给每张照片算出目标文件夹，生成“源 → 目标”清单。这一步**纯计算，不碰文件**。
3. **安全搬运**（执行 + 撤销）：照着清单移动文件，先写日志、成组移动、失败回退。只有这一步会改动磁盘。

好处：分析算法写错了，最多分错文件夹，预览里一眼能看出来；移动逻辑只有一处，单独测试、单独保证安全。

## 2. 分层结构

```
┌──────────────────────────────── UI 层（PySide6）────────────────────────────────┐
│  main_window.py  顶栏 / 导航 / 状态条        context.py  AppContext：共享对象 + 信号 │
│  pages/  01资源接入 02分区编排 03统计概览 04清晰度 05操作记录 06设置  (+插件页面)     │
│  widgets/ deco.py 自绘控件（面板/切角按钮/数据卡/图表）  thumbgrid.py 虚拟化缩略图网格 │
│  theme.py 两套配色 + QSS      dialogs.py 确认/进度/执行计划                         │
└───────────────────────────────────────┬─────────────────────────────────────────┘
                                        │ 只通过 Library / Planner / Mover 调用
┌──────────────────────────────── 服务层 ──────────────────────────────────────────┐
│ core/library.py   扫描总调度：枚举 → 元数据 ∥ 像素分析 → 元数据分析，全部带缓存       │
│   ├ core/scanner.py    遍历文件夹，建立“文件组”（RAW+JPG+xmp+aae+mov）               │
│   ├ core/metadata.py   ExifTool 常驻进程批量读取；找不到时退回 Pillow+exifread         │
│   ├ core/pixelwork.py  后台进程：解码一次预览 → 缩略图 + 所有像素分析器               │
│   │   └ core/imaging.py  JPG draft 缩小解码 / HEIC / RAW 内嵌预览（不解马赛克）       │
│   ├ core/geo.py        GPS → 国家/省/市/区县（GeoToolCN + GeoNames，离线）            │
│   └ core/dates.py      EXIF 日期解析 + 文件名日期规则                                │
│ analyzers/  分析器插件：date geo camera shape（元数据） color blur（像素）            │
│ partition/  维度插件：date region color camera lens source filetype orientation …     │
│   └ engine.py  条件链 / 模板 → 每组的目标文件夹；预览树；预设                          │
│ ops/planner.py  模拟运行：目标路径、重名编号、跨盘、空间检查                           │
│ ops/mover.py    执行 / 撤销 / 崩溃恢复          ops/journal.py  操作日志（SQLite）       │
│ ops/pathsafe.py Windows 文件名清洗、长路径                                            │
│ plugins.py      插件加载与注册接口                                                     │
└───────────────────────────────────────┬─────────────────────────────────────────┘
┌──────────────────────────────── 数据层 ──────────────────────────────────────────┐
│ %LOCALAPPDATA%\PhotoCrate\cache.db（元数据/特征/预览，WAL）  thumbs\（360px 缩略图）  │
│ %APPDATA%\PhotoCrate\journal.db（操作日志） settings.json  logs\  plugins\           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**依赖方向只能向下**：UI 依赖服务层，服务层不依赖 UI。所以核心逻辑可以脱离界面单独测试（`tests/test_core.py`），将来换界面框架也不用动核心。

## 3. 关键数据结构

### PhotoGroup（文件组）—— 软件里“一张照片”的单位

```python
PhotoGroup(
    gid=12,
    files=[MemberFile("DSC01.ARW","raw"), MemberFile("DSC01.JPG","jpeg"), MemberFile("DSC01.ARW.xmp","sidecar")],
    primary="DSC01.JPG",          # 显示与像素分析用 JPG（比 RAW 快）
    meta={...},                   # 归一化元数据（合并组内所有图片文件）
    feats={"date":{...}, "geo":{...}, "color":{...}, "blur":{...}, ...},  # 各分析器结果
    thumb=".../thumbs/ab/abcd.jpg",
)
```

为什么要“组”？因为 RAW 和同名 JPG、Lightroom 的 `.xmp`、iPhone 的 `.aae` 本质上是同一张照片。分开移动会让修图记录和照片分家——这是同类工具最常见的坑。

### 文件指纹（缓存键）

`sha1(文件名 + 大小 + 修改时间)`，**不含路径**。所以本软件移动过的照片，下次扫描依然命中缓存，几千张第二次扫描不到一秒。

## 4. 扫描流程（core/library.py）

```
枚举文件、建文件组
   │
   ├─▶ 像素任务（缓存未命中的）立刻提交到进程池 ─────────────┐   并行
   │      每个任务：解码一次预览(长边1024) → 存缩略图 → 色调/清晰度/插件分析器
   │                                                         │
   └─▶ ExifTool 常驻进程，每批 150 个文件读元数据（缓存未命中的）│
                                                             ▼
                              等像素任务完成，写入缓存
                                   │
                              元数据分析器（日期 / 地区 / 相机 / 画幅）——几毫秒，不缓存
                                   │
                              （可选）无 GPS 照片按时间就近推测地点
```

- **为什么 RAW 很快**：只取相机写在 RAW 里的 JPEG 预览（几毫秒），而不是完整解码（几秒到一分钟）。取不到时依次回退到 ExifTool 提取预览、rawpy 半尺寸解码。
- **为什么用多进程**：图像解码是 CPU 密集型，Python 多线程受 GIL 限制。进程池起不来时自动降级为多线程（设置里也可手动切换）。
- **为什么元数据分析器不缓存**：它们依赖设置（文件名规则、GCJ-02 开关……），改设置后在内存里重算一遍，比缓存失效管理简单得多，而且几千张只要几十毫秒。

## 5. 分区引擎（partition/）

每个分区条件是一个 **Dimension（维度）**：

```python
class Dimension:
    id, label, en, description, unknown, order_by
    options = [Option(...)]                       # 界面根据它自动生成下拉框/勾选框
    def segments(self, group, opts, ctx) -> list[str]   # 返回一级或多级文件夹名
    def tokens(self) -> dict[str, fn]                    # 提供给模板的占位符
```

- **条件组合模式**：目标路径 = 目标根目录 / 维度1 的 segments / 维度2 的 segments / …
- **模板模式**：`{年}/{年}-{月} {市|省|未知地点}`，占位符由所有维度的 `tokens()` 汇总而来，所以插件加的维度也能直接在模板里用。
- 每个维度都有“未知”兜底名（未知日期 / 未知地点 / 未知相机…），永远不会出现“无处可放”。
- 文件夹名统一经过 `sanitize_component`：去掉 Windows 非法字符（镜头名里的 `/`）、保留名（CON、NUL）、超长截断。
- 色调等阈值类的分类放在维度里做（`classify_color`），而不是分析器里——这样调阈值不用重新分析，预览立即刷新。
- **按单个文件拆分**：绝大多数维度按"整组"决定位置；只有"格式"需要把一组里的 RAW 与 JPG 分开。维度返回占位符 `FORMAT_TOKEN`，预览树（`build_tree`）与计划（`build_plan`）会逐个文件把它替换成 ARW / JPG / HEIC…，附属文件（.xmp/.aae）跟随所属图片（`partition/base.py: member_format`）。模板里用 `{格式}`。
- 黑白判定只看画面中足够亮的像素（L≥18）的 99.5% 分位色度——很暗的彩色夜景、带一小块人脸的淡彩照片都不会被误判成黑白（`ColorAnalyzer` v3）。

## 6. 安全搬运（ops/）

| 机制 | 实现位置 |
|---|---|
| 模拟运行：完整的源→目标清单，确认才执行 | `planner.build_plan` + `dialogs.PlanDialog` |
| 重名自动编号，**整组用同一个编号**保持 RAW/JPG 配对 | `planner._renamed` |
| 先把整个批次写进日志（pending），每完成一组提交一次 | `journal.create_batch` / `mover._run_batch` |
| 同盘 `os.rename`；跨盘 复制到 `.pctmp` → 校验 → 改名 → 删源 | `mover.transfer` |
| 一组里任一文件失败，已移动的成员全部退回 | `mover._run_batch` 的 except 分支 |
| 撤销：按日志逆序移回；原位置被占用就改名恢复，不覆盖；清理本批新建的空文件夹 | `mover.undo` |
| 崩溃恢复：启动时发现 running 状态的批次 → 按磁盘实际状态修正日志 → 继续或回滚 | `mover.reconcile/resume/rollback` + `main_window._check_recovery` |
| 取消：只在两组之间响应，绝不留下复制一半的文件 | `mover._run_batch` |
| 删除一律进回收站 | `mover.trash`（send2trash） |

这些行为都有对应的单元测试（`tests/test_core.py`），包括模拟跨盘、模拟中途失败、模拟崩溃后恢复。

## 7. 界面设计

- **视觉语言**（`ui/theme.py` + `ui/widgets/deco.py`）：直角与切角、细线框 + 四角角标、编号章节（01–06）、中文标题 + 英文等宽小标签、黄色强调块、斜纹警示带（只用在“会改动文件”的确认窗口）、分段进度条。全部自绘，不用图片素材。
- **字体**：中文“微软雅黑”，数字与英文用 Windows 自带的 Bahnschrift（DIN 风格窄体），等宽用 Cascadia/Consolas。
- **两套主题**：浅色「终末地」（浅灰工业底 + 高可见度黄）/ 深色「方舟」（深色终端 + 金黄 + 青色信息色）。颜色只在 `Palette` 里定义一次，所有自绘控件在绘制时读取当前配色。
- **性能**：缩略图网格是虚拟化的 `QListView`（只绘制可见项），缩略图 LRU 缓存；预览树和统计都是内存计算，几千张即时刷新；所有耗时任务在 `QThread` 中运行，界面不卡。

## 8. 扩展：插件接口（保留给未来的新功能）

### 8.1 插件文件

放到 `plugins\`（程序目录）或 `%APPDATA%\PhotoCrate\plugins\`，重启生效：

```python
PLUGIN_INFO = {"name": "我的插件", "version": "1.0", "description": "..."}

def register(api):
    api.register_analyzer(MyAnalyzer())                      # 新特征
    api.register_dimension(MyDimension())                    # 新分区条件（自动出现在下拉菜单和模板占位符里）
    api.register_preset("名称", rules=[{"dim": "my", "opts": {}}])   # 新预设
    api.register_page("页面标题", "ENGLISH", lambda ctx: MyPage(ctx))  # 新页面（自动出现在左侧导航）
```

后台分析进程也会加载插件（此时 `api.headless=True`，页面注册被忽略），所以像素分析器写在插件里也能并行运行。

### 8.2 新增一个像素分析器（例如 AI）

```python
class SceneAnalyzer(Analyzer):
    id = "scene"; stage = "pixel"; version = 1
    def analyze_pixels(self, px, settings):
        rgb224 = px.small(224)             # PixelInput 自带缩放/灰度缓存
        ...                                # 跑模型
        return {"label": "风景", "score": 0.83}
```

结果自动按文件指纹缓存；改了算法把 `version` 加 1，旧缓存自动失效。分区维度里用 `group.feats["scene"]` 读取。完整骨架见 `plugins_examples/example_ai_scene_stub.py`。

### 8.3 未来功能路线（接口都已就位）

| 功能 | 做法 |
|---|---|
| AI 场景分类 / 以文搜图 | Chinese-CLIP 导出 ONNX → 像素分析器存向量与标签 → “场景”维度；搜索页作为插件页面 |
| 人脸 / 人物分组 | OpenCV YuNet + SFace（Apache 2.0）→ 像素分析器存人脸向量 → 全库聚类（可在 `run_meta_analyzers` 之后加一个“全库后处理”步骤，参照 `infer_missing_locations`）→ “人物”维度 |
| 重复 / 相似照片 | 像素分析器算 pHash → 全库比较 → “重复组”维度，移入「待确认重复」 |
| 地图视图 | 插件页面：`QWebEngineView` + Leaflet，坐标来自 `feats["geo"]`（高德底图需 WGS-84→GCJ-02，GeoToolCN 自带转换） |
| 星级 / 标签写入 XMP | 新增 ops 模块调用 ExifTool `-XMP:Rating=` 写附属文件 |
| 手动指定地点（无 GPS 的相机照片） | **后端已完成**：`core/places.py`（`AnnotationStore` 标注库 + `PlaceIndex` 地名解析，如“日本/京都”“湖南/邵阳”）、`Library.set_manual_place()`、`GeoAnalyzer` 读取手动地点；只差一个界面入口（选中照片 → 输入地点） |
| 跨文件夹 RAW/JPG 配对 | 目前只在同一文件夹内按同名配对；可在 `scanner.group_directory` 之后加一步全库按“主文件名+拍摄时间”配对 |

> 注意：打包好的 exe 只包含已有的依赖。需要新第三方库（如 onnxruntime）的插件，用 `run.bat` 源码模式运行（`.venv\Scripts\pip install onnxruntime`），或安装后重新打包。

## 9. 文件地图

```
PhotoCrate/
├─ run.bat / build_exe.bat / build_exe_nuitka.bat   双击入口（实际逻辑在 scripts/*.ps1）
├─ launcher.py                 打包入口（含 multiprocessing.freeze_support）
├─ requirements.txt  installer.iss
├─ photocrate/
│  ├─ app.py  bootstrap.py  config.py  plugins.py
│  ├─ core/        models formats scanner metadata imaging pixelwork geo dates cache library stats
│  ├─ analyzers/   base builtin
│  ├─ partition/   base dimensions engine
│  ├─ ops/         planner mover journal pathsafe
│  └─ ui/          theme context dialogs main_window  pages/  widgets/
├─ data/           geonames_cities1000.csv.gz  place_names_zh.json
├─ assets/         app.ico app.png
├─ plugins/        用户插件放这里        plugins_examples/  示例插件
├─ tools/          get_exiftool.ps1  make_icon.py  （exiftool/ 运行后自动下载到这里）
└─ tests/          test_core.py  make_samples.py  ui_smoke.py
```

## 10. 界面细节与调试辅助

- **F12 截图**：`MainWindow.eventFilter` 在应用级拦截 F12，用 `QScreen.grabWindow` 截取窗口区域（包括弹出的对话框），存到 `screenshots_dir()`。
- **窗口位置记忆**：关闭时保存 `window_state`（几何 + 是否最大化）；设置项 `window_screen` 可指定下次在哪台显示器打开（`primary` / `secondary` / `#2` / 显示器名称片段），只生效一次。
- **大列表一律用 Model/View**：操作记录的文件清单可能上千行，用 `QAbstractTableModel` 按需绘制；不要对大表使用 `ResizeToContents`（每次数据变化都会扫描全部行，904 行时曾导致约 20 秒卡顿）。

## 11. 已知局限

- 海外地点用“最近的城市点”判断，边境/海边可能归到邻近城市；国内用真实行政边界，准确度高。
- 色调与清晰度阈值没有放之四海皆准的值，默认值偏保守，建议用自己的照片在界面里调。
- 清晰度：夜景高 ISO 噪点会让分数偏高（被判为清晰）；预览太小的 RAW（个别老机型）标为“无法判断”。
- 视频文件（除实况照片外）不在处理范围内。
- RAW 与 JPG 的配对只在同一文件夹内进行；按格式分开存放后再扫描，会各算一张。
- 没有代码签名的 exe 可能被杀毒软件误报。
