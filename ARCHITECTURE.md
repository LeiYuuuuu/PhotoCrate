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
│  pages/ 01资源 02时间与地点 03分区 04统计 05挑片 06旅行与地图 07AI搜图 08人物       │
│         09清晰度 10记录 11设置 (+插件页面)    widgets/mapview.py 瓦片地图（纯 Qt 绘制） │
│  viewer.py 大图查看 / 并排对比   culling_dialogs.py 清理 RAW、批量重命名   xmpflow.py │
│  editor/ 修图窗口：window（菜单 / 选项栏 / 对话框）canvas（画布）tools（16 个工具）panels │
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
│   ├ core/dates.py      EXIF 日期解析 + 文件名日期规则                                │
│   ├ core/pairing.py    跨文件夹 RAW+JPG 配对（文件名 + 拍摄时间 + 机型，一一对应）     │
│   ├ core/geotime.py    时区换算、GPX/KML 解析、按时间匹配轨迹 / 参考照片补 GPS          │
│   ├ core/places.py     用户标注库（手动地点 / 时间校正）+ 地名解析与候选               │
│   ├ core/daybook.py    按天汇总 + 行程文本解析（「时间与地点」页的数据）                │
│   └ core/trips.py      旅行自动分段 + 命名 + 常住地解析                                │
│ ai/  models（下载）clip（中文 CLIP + 分词）scenes（零样本场景）faces（YuNet+SFace）       │
│      visual（画面指纹：颜色布局 + 直方图，给“找相似”用）                                   │
│      people（人物聚类）service（向量索引 / 搜索 / 命名，挂在 Library.ai 上）            │
│ analyzers/  分析器插件：date geo camera shape rating（元数据） color blur phash faces（像素）│
│ partition/  维度插件：date region color camera lens source filetype orientation …     │
│   └ engine.py  条件链 / 模板 → 每组的目标文件夹；预览树；预设                          │
│ ops/planner.py  模拟运行：目标路径、重名编号、跨盘、空间检查                           │
│ ops/mover.py    执行 / 撤销 / 崩溃恢复          ops/journal.py  操作日志（SQLite）       │
│ ops/xmpwrite.py 写回 XMP 附属文件（只给 RAW；新建或更新已有并备份；记日志、可撤销）     │
│ ops/cleanup.py  清理 RAW 计划   ops/trashbin.py 回收站放入 / 找回   ops/renamer.py 改名 │
│ ops/importer.py 导入向导   ops/dedupe.py 重复照片   ops/exporter.py 导出 JPG / 网页相册   │
│ ops/pathsafe.py Windows 文件名清洗、长路径                                            │
│ edit/  修图核心（不依赖界面）：document 图层栈 / 渲染 / 选区 / 历史  adjust 调整参数      │
│        blend 混合模式  paint 画笔 / 修复 / 选区工具  filters 滤镜  text 文字  io 打开 / 保存 │
│ plugins.py      插件加载与注册接口                                                     │
└───────────────────────────────────────┬─────────────────────────────────────────┘
┌──────────────────────────────── 数据层 ──────────────────────────────────────────┐
│ %LOCALAPPDATA%\PhotoCrate\cache.db（元数据/特征/预览，WAL）  thumbs\（360px 缩略图）  │
│ %APPDATA%\PhotoCrate\journal.db（操作日志） annotations.db（地点/时间校正/星级）     │
│                     xmp_backups\（改写 .xmp 前的备份）  models\（AI 模型）           │
│ %LOCALAPPDATA%\PhotoCrate\tiles\（地图瓦片缓存）  cache.db 的 emb 表（CLIP 向量）   │
│                     settings.json  logs\  plugins\  screenshots\                     │
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
                              跨文件夹配对 RAW+JPG（core/pairing.py）→ 重新编号
                                   │
                              读取 GPS 补全的参考数据（GPX/KML + 参考照片文件夹，元数据有缓存）
                                   │
                              元数据分析器（日期 + 时间校正 / 地区 + 手动地点 / 相机 / 画幅）——不缓存
                                   │
                              GPS 补全：没有 GPS 的照片按拍摄时间匹配轨迹 / 参考照片（core/geotime.py）
                                   │
                              （可选）仍没有地点的照片按时间就近推测
```

- `.xmp` 附属文件也会读取元数据，但排在图片之后合并：只用来补全图片里没有的信息（例如写回的 GPS）。

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

## 6. 时间与地点（第一梯队功能）

所有修改默认**只存在软件里**（`annotations.db`），照片文件不变；需要给 Lightroom 等软件用时再“写回 XMP”。

| 功能 | 实现 |
|---|---|
| 标注存储 | `AnnotationStore`：`places`（手动地点）与 `timeshift`（`{seconds, tz}`）两张表，键是**组内每个图片文件**的指纹 → 移动、RAW/JPG 拆开存放后都还能找到（`PhotoGroup.annotation()`） |
| 时间校正 | `DateAnalyzer` 读到校正后输出 `dt`（校正后）、`orig_dt`、`shift`、`tz`；新校正**替换**旧校正，不叠加；“相机时区设错了 +08:00 → +09:00”= 推后 1 小时并把时区改为 +09:00 |
| 手动 / 按行程指定地点 | `Library.set_manual_place(gids, place, override_gps, override_track)`；地名经 `PlaceIndex.resolve` 规范成 国家/省/市（如“京都”→ 日本/京都府/京都）；只有一个不认识的词时要求加国家；行程文本由 `daybook.parse_itinerary` 解析 |
| GPS 补全 | 参考点 = GPX/KML（UTC）+ 参考照片文件夹 + 同批自带 GPS 的照片。每个点有 `utc` 与 `local` 两种时间：照片时区已知 → 按 UTC 比对（相机与手机时区设置不同也对得上），再与“时区未知”的参考点比当地时间；照片时区未知 → 按当地时间比对。两侧参考点都在窗口内且相距 <50 km 时线性插值。手机照片没有时区时用 EXIF 的 GPS 时间（UTC） |
| 优先级 | 照片自带 GPS ＞ 手动指定（可选覆盖 GPS）＞ 轨迹 / 参考照片匹配 ＞ 按时间推测 |
| 跨文件夹配对 | `pairing.pair_across_folders`：同主文件名、一边只有 RAW 一边只有 JPG/HEIC、拍摄时间差 ≤2 秒、机型一致、一一对应才合并 |
| 写回 XMP | `ops/xmpwrite.py`：只给 RAW 新建“主文件名.xmp”（已有 .xmp 跳过，绝不改写）；内容 = 匹配到的 GPS、地名（photoshop:Country/State/City）、校正后的 DateTimeOriginal；批次写进操作日志（operation = `xmp`），撤销 = 新建文件进回收站（.xmp 之后跟着照片被分区移动过也能顺着日志找到；先撤销 XMP、再撤销移动时，已进回收站的 .xmp 不算失败）；崩溃恢复对 `xmp` 批次只做“保留已生成 / 回滚”，绝不按移动方式执行 |

日本等地的 GeoNames 地名会去掉 `-shi / -cho` 后缀、政令市的区（Kamigyo-ku）归到所属城市，并补充了约 280 个日本城市的中文名，保证“手动指定的京都”和“GPS 识别出的京都”落在同一个文件夹（`日本/京都府/京都`）。

## 7. 挑片与清理（第二梯队功能）

| 功能 | 实现 |
|---|---|
| 星级 / 色标 / 留弃 | `AnnotationStore` 新表 `rating`（`{rating 0–5, label, flag pick/reject}`，同样按组内每个图片文件的指纹存）；`RatingAnalyzer` 合并照片 / .xmp 里原有的 `Rating`（-1 = 拒绝）与 `Label`，本软件打的优先，输出 `feats["rating"] = {rating, label, flag, manual}`。`Library.set_rating` 立即更新，不需要重新分析；分区维度「星级 / 标记」（模板 `{星级}` `{标记}` `{色标}`） |
| 键盘打分 | `rating_change_for_key`（viewer.py）统一翻译按键：0–5 星、6–9 红黄绿蓝（再按一次取消）、P / X / U；缩略图网格通过 `thumbgrid.HOOKS`（主窗口安装：`rate` → `AppContext.rate`，`open` → 大图查看器）在所有页面生效，`ratings_changed` 信号只触发重绘 |
| 大图查看 | `ViewerWindow`：照片左右两侧是翻页区（宽为窗口的 10%，56–120 像素），鼠标移进窗口时显示箭头，点一下翻页、拖动放大的照片不算点击、在翻页区双击 = 翻两张；对比模式下左右两张各自翻页；后台线程解码（JPG 全尺寸 + EXIF 方向；只有 RAW 时取相机内嵌大预览），LRU 留 8 张并预读前后几张；`ImagePane` 记录 `zoom`（None = 适合窗口）与视图中心 `cx, cy`（图像比例坐标），缩放时保持鼠标下那一点不动、平移时不让图像边缘离开窗口；对比模式两个 Pane 互相转发 `view_changed`，缩放与位置同步 |
| 相似 / 连拍分组 | `HashAnalyzer`（像素阶段，32×32 DCT 取低频 8×8 → 64 位 pHash，带缓存）；`group_similar`：按拍摄时间排序，与上一张相差 ≤ N 秒、且与上一张或本组第一张汉明距离 ≤ 阈值就并入，组内按清晰度分数排名，最高的标 `best`。只用缓存的指纹，调参数时几毫秒重算 |
| 星级写入 XMP | `xmpwrite.tags_for` 写 `XMP-xmp:Rating`（弃用 = -1）与 `XMP-xmp:Label`，只写本软件改过的。**已有 .xmp 时可更新**：先完整备份到 `xmp_backups\时间戳\`，ExifTool `-overwrite_original` 只改这几个标签，日志里记 `xmp_edit`（src = 备份），撤销 = 当前文件进回收站、备份拷回；失败时立即换回备份 |
| 清理 RAW | `cleanup.build_cleanup_plan`：`keep_jpg` / `unrated_raw` / `rejected` → operation = `delete` 的计划（RAW 连同它的 .xmp / .pp3 / .dop）；`Mover.delete` 逐组放入回收站（一组里失败就把已删的放回），批次记下开始时间 `t0` |
| 从回收站找回 | `trashbin.find_in_trash(path, t0)`：Windows 解析 `$Recycle.Bin\<SID>\$I*`（v1/v2 格式：原路径 + 删除时间 + 大小），freedesktop 读 `.trashinfo`；按“原路径相同、删除时间 ≥ t0 − 2 秒”取最新一个，`os.rename` 放回并删掉索引文件。撤销清理后自动重新扫描 |
| 批量重命名 | `renamer.build_rename_plan`：模板与分区模板相同，另有 `{序号}` `{序号:4}` `{当日序号}` `{原名}`；同组成员同一主文件名、保留双扩展名（`.ARW.xmp`）、与已有文件重名加“ (n)”。执行走 `Mover` 的 `rename` 批次（复用移动的日志 / 撤销 / 崩溃恢复）；`Library.apply_moves` 发现文件名变化时把标注和缓存**移到**新指纹 |

## 8. 旅行、地图与 AI（第三梯队功能）

AI 全部在本机离线运行；模型首次使用时下载到用户数据目录 `models\`，结果按文件指纹缓存。

| 功能 | 实现 |
|---|---|
| 旅行自动分段 | `core/trips.segment_trips`：每张照片先判断 home（在任一常住地半径 50 km 内；没有坐标时按地名比较）/ away / unknown（没有地点）；只用 away 照片按时间切段——相邻两张间隔 > `trip_gap_hours`（默认 36；前后两张在同一个非常住国家时放宽到 2 倍）或中间出现 home 照片就断开，少于 `trip_min_photos` 张的段丢弃；unknown 照片落在某段时间范围内（前后放宽 12 小时、期间没有 home 照片）就并入。命名 = 起始日期 + 国家（国内用省份）+ 出现最多的 3 个城市；用户改名按起始日期记在 `trip_names`。结果写进 `feats["trip"]`，分区维度「旅行」、模板 `{旅行}` `{第几天}`；作为 `run_meta_analyzers` 的全库后处理步骤运行 |
| 常住地 | `resolve_home`：支持“纬度,经度”、中文地名（经 `PlaceIndex`）、英文城市名（`Boca Raton, US`，在 GeoNames 表里查坐标，标签转成中文）；“从照片识别”= 拍摄天数最多、且占全部天数足够比例的城市 |
| 地图 | `ui/widgets/mapview.py`：Web 墨卡托瓦片、`QNetworkAccessManager` 异步取瓦片并存成 PNG（`tiles\` 文件夹，看过的区域离线也能看），新瓦片未到时用上一级瓦片放大补位，完全离线时画经纬网格；照片按屏幕网格聚合，画代表缩略图 + 数量，点击发出 `cluster_clicked`；旅行路线 = 每天的位置中心按时间连线；高德底图时把中国境内的点从 WGS-84 转成 GCJ-02。手动指定的地点没有坐标，按城市中心（`pin_lat/pin_lon`）显示 |
| 地名就近取大城市 | `geo._WorldIndex.nearest`：GeoNames 只有城市中心点，大城市市区里常常离卫星城更近（浅草 → 草加 / 浦安，伏见稻荷 → 向日）。在“最近距离 + 12 km、≤ 30 km、同一国家”的候选里按 `距离 − 4 × log10(人口)` 取最小 |
| 模型下载 | `ai/models.py`：中文 CLIP（Xenova 导出的 Chinese-CLIP ViT-B/16 int8 ONNX，约 190 MB，Hugging Face，失败自动换 hf-mirror.com）；YuNet + SFace（OpenCV Zoo，GitHub）。先下到 `.part` 再改名，校验大小，拒绝网页 / LFS 指针；也可手动放文件 |
| CLIP 推理 | `ai/clip.py`：onnxruntime CPU；自带 BERT 分词（中文按字、英文 WordPiece，词表 `data/clip_vocab_zh.txt`，最长 52）；预处理 = 直接缩放到 224×224 + CLIP 均值方差（与 Chinese-CLIP 一致）。Xenova 的模型图文合在一张图里：只编码图片时喂一句占位文字，只编码文字时喂一张空白图；加载时检查输入输出名 |
| 向量索引 | `ai/service.AIService.index`：用缩略图（长边 360）算图片向量，按主图指纹存进 `cache.db` 的 `emb` 表（float16），移动 / 改名后照样命中；扫描末尾的“AI”阶段只算新照片 |
| 场景识别 | `ai/scenes.py`：零样本。21 个细分场景，归为 7 个大类（自然风光、城市建筑、人物、美食、动物植物、室内、文档截图）；每个场景几句中文描述取平均向量，与图片向量做 softmax（温度 100），取最高的场景与把握度。维度「场景」（细分 / 大类，可设最低把握度） |
| 文字搜图 | 查询文字（短查询再加一句“一张…的照片”取平均）编码后与全部图片向量点积。CLIP 的文字-图片分数挤在很窄的区间（相关约 0.40、无关约 0.37），只按分数排，特写人像也会混进“街道”的前几十名，所以再做**主体对比**：从互相排斥的主体场景（`scenes.SUBJECT_SCENES`：人像 / 自拍 / 合影、美食、饮品、文档截图、室内、猫狗）里取和搜索词无关的几类（文字相似度低于中位数），照片像其中之一比像搜索词还高出 `SEARCH_MARGIN`（0.03）就去掉，略高的降低排名；夜景、雪景、日落等时间 / 天气场景能和任何主体同时出现，不参与比较；另外去掉分数低于全库中位数的 |
| 以图搜图（找相似） | 综合相似度 = 内容（CLIP 图片向量，55%）+ 画面（`ai/visual.py`：8×8 YCbCr 颜色布局 + 64 格颜色直方图，30%）+ 结构（pHash，15%）。每一项先减去全库中位数（“随便两张”的水平）再缩放到 0–1，缺哪项按其余几项算；低于 `SIMILAR_MIN`（0.30）不显示。画面指纹从缩略图计算（每张约 1 毫秒），缓存在 emb 表（键 `vis-v1`），以前分析过的照片在下次扫描或第一次找相似时补算。结果上方显示目标照片，「大图对比」把目标和结果放进查看器 |
| 人脸 | `ai/faces.FaceAnalyzer`（像素阶段，在扫描的后台进程里运行）：YuNet 在长边 1024 的预览上检测，SFace 对齐后提 128 维特征（float16、base64）并存一张 112×112 小头像；小于 `face_min_size` 的脸忽略。模型不在时 `available()` 为 False，不会写入空结果 |
| 人物聚类 | `ai/people.cluster_people`：先应用用户更正（face_fix：指定归属 / “不是他”），再按已命名的人的特征中心认人（余弦 ≥ `face_threshold`，默认 0.42），其余脸按大小做贪心中心聚类并合并相近的组；只在一张照片里出现的脸归“单次出现”。命名 = 存 {名字, 特征中心}（annotations.db 的 people 表）；同名 = 合并。维度「人物」按照片里最大的已命名脸归档 |
| 排除不是真人的脸 | YuNet 只认“脸的形状”，鹿、猫、玩偶、手办、动漫人物、雕像都会被框出来，SFace 还会把同一个玩偶的几张照片聚成一个“人”。`AIService.screen_faces` 用 CLIP 比较每张 112×112 人脸小图与两组描述（`HUMAN_PROMPTS` 真人的脸 / `OTHER_PROMPTS` 动物、动漫、玩偶、手办、雕像、面具），softmax（×100）后“真人”几项的概率和 < `HUMAN_MIN`（0.5）即排除；结果按人脸键缓存在 emb 表（键 `facekind-v1`）。公开测试图上真人 > 0.9、猫脸 0.02、卡通脸 0.26。描述向量分不开（模型异常）或没有 CLIP 时不判断。用户更正存 face_fix：`notface` 强制排除、`human` 覆盖 AI 判断；被排除的脸在「人物」页的「不是人脸」组里，可恢复 |

## 9. 补全工作流：视频、导入、去重、导出、AI 关键词

| 功能 | 实现 |
|---|---|
| 视频 | `formats.VIDEO_EXTS`；`scanner.group_directory`：与照片同名的 .mov/.mp4 仍是实况照片的视频部分，其余视频各成一组（角色 `video`，`PhotoGroup.is_video`），索尼 `C0001M01.XML`、佳能 `.THM` 作为附属文件跟随。元数据：带时区的 CreationDate（苹果 Keys、索尼 XML）直接用；只有 QuickTime CreateDate（UTC）时 `core/videos.align_video_times` 找 24 小时内按 UTC 最接近、时区已知的照片，用它的时区换算（`tz_from`），找不到标 `tz_guess`。像素阶段用 OpenCV 取 12% 处的一帧做缩略图（CLIP / 人脸照常），清晰度不评分；XMP 不写视频；大图查看器显示该帧 + 播放按钮，双击 / 回车交给系统播放器 |
| 导入向导 | `ops/importer.py`：`detect_sources`（Windows：GetLogicalDrives + GetDriveType，可移动盘或带 DCIM 的盘）；`card_roots`（DCIM、PRIVATE/M4ROOT/CLIP、AVCHD…）；用一个临时 `Library` 以 `scan(pixels=False)` 只读元数据；`classify` 按导入记录（`imports.db`：卡上文件指纹 → 导入后的路径，目标文件仍在才算）或照片库已有同一指纹判断“已导入”；`build_import_plan` = 分区模板定文件夹 + 可选改名模板（与批量重命名同一套）→ operation `copy` 的计划，目标已有同名同大小文件也算已导入；执行走 `Mover.execute`（先写日志、成组复制、可撤销），完成后 `record_import` 写导入记录。复制保留修改时间，所以指纹不变，读卡时的元数据缓存直接命中 |
| 去重 | `ops/dedupe.find_duplicates`：只看主体文件（照片 / 视频）；按大小分桶 → 头尾各 1 MB 的 BLAKE2b → 全文件 BLAKE2b；并查集把共享内容的照片组连成簇；一组的全部内容都包含在另一组里才算多余副本（RAW+JPG 优先于单独 JPG，默认按扫描顺序保留「资源接入」靠前文件夹里的）；副本带原件没有的修图记录时跳过。`build_dedupe_plan` → operation `delete`，复用清理 RAW 的回收站删除 / 撤销 |
| 导出 | `ops/exporter.py`：`render_one` = 选最佳源（JPG/HEIC/PNG，只有 RAW 时用内嵌预览）→ EXIF 转正 → 缩小（不放大）→ 水印（白字 + 半透明阴影，字号随长边缩放）→ EXIF：默认去掉 GPS IFD，或全部去掉；`export_jpegs` 文件名沿用原名或用改名模板，重名加编号；`export_album` 写 `index.html`（数据内嵌 JSON、Leaflet 从 CDN 加载、坐标四舍五入到 0.01°）+ `photos/` + `thumbs/`。导出只写新文件，不进操作日志 |
| AI 关键词 | `xmpwrite.ai_keywords`：场景（把握 ≥ 0.3）、已命名的人物、旅行名 → `XMP-dc:Subject` 与 `XMP-lr:HierarchicalSubject`（“场景|大类|场景”“人物|名字”“旅行|名称”）；列表值用 ExifTool 的 `-TAG-=v -TAG+=v` 写入，已有关键词不动、不会重复；和星级一样只写 RAW 的 .xmp，可更新已有 .xmp（先备份、可撤销） |

## 9.5 修图（edit/ + ui/editor/）

和软件其他部分同样分两层：`photocrate/edit` 是纯 numpy / OpenCV / Pillow 的核心（单元测试 `tests/test_editor.py`），`photocrate/ui/editor` 只负责界面。不新增依赖。原片只读：导出写新文件（`io.export_image` 拒绝覆盖原片路径），工程存 `.pcedit`。

| 部分 | 实现 |
|---|---|
| 图层 | `Layer`：`pixel`（RGBA uint8 / uint16 + 左上角位置，可以比画布大 / 小）、`text`（参数 + 按参数用 Pillow 画出的像素，改参数就重画）、`adjust`（`adjust.py` 的参数，作用于下面所有图层合成的结果）。蒙版 uint8：像素 / 文字图层与像素同尺寸、跟着图层移动；调整图层与画布同尺寸 |
| 混合 | `blend.composite`：W3C Compositing 公式（带底层 alpha），18 种模式含色相 / 饱和度 / 颜色 / 明度（SetLum / SetSat） |
| 渲染 | `Document.render(level, rect)`：在 2^-level 级别上只合成一块。每层按级别缓存代理图——宽高补齐到 2^level 的整数倍再整数倍盒式平均（有透明时先预乘），所以画笔只改一小块时可以只更新代理图的这一块，结果与整张重算完全一致。调整图层里有半径的操作（高光阴影分区、清晰度、锐化、降噪）按 `RenderCtx.scale` 换算，且渲染一块时四周多算 `adjust.margin()`；大半径模糊先缩小再模糊。拖滑块时缓存“这一层下面所有图层的合成结果”（`hot`，签名 = 下面每层的版本号 / 属性） |
| 画布 | `CanvasView`：缩放 < 100% 时选不小于屏幕像素的级别；整张代理图 ≤ 600 万像素就整张算（平移不重算），否则只算可见区域外加 30%。连续操作时上一帧 > 90 ms 就先用低一级出图、停手 250 ms 后补清晰的。画笔每一笔只重算改动的矩形并直接写进显示缓冲。选区蚂蚁线 = `cv2.findContours` 的轮廓 |
| 调整 | `adjust.develop`：白平衡 + 曝光（线性空间，每通道一张查找表）→ 色阶 → 高光 / 阴影（按模糊后的亮度分区，逐像素幂次）→ 白 / 黑色阶、对比度、褪色（查表）→ 清晰度 → 曲线（Fritsch–Carlson 单调三次插值）→ HSL（8 个色带，相邻色带线性分配权重）→ 饱和度 / 自然饱和度 → 分离色调 → 反相 → 暗角（按画布位置）→ 锐化（只锐化亮度）→ 降噪（双边滤波）→ 颗粒。14 个预设就是几组参数 |
| 历史 | 两种步骤：画笔一笔 = 局部步骤（改动矩形改前 / 改后两块像素）；其他一切 = 状态步骤（图层列表、属性、像素数组的**引用**）。约定除画笔外的改动都换新数组、不原地改，所以记引用就够。连续拖同一个滑块（同一个 key）合并成一步；最多 60 步 / 1.5 GB |
| 工具 | 选框 / 套索（自由、多边形）/ 魔棒（`cv2.floodFill` FIXED_RANGE 或全图同色）/ 对象选择（GrabCut，先双边滤波去颗粒，在长边约 900 px 的合成图上算再放大）/ 裁剪（可旋转的裁剪框，拉直时自动缩到不露透明角，`cv2.warpAffine` 的逆映射一次完成旋转 + 裁剪）/ 画笔（覆盖度取最大值，同一笔不会越涂越深）/ 仿制图章（从这一笔开始时的快照里按偏移取）/ 污点修复与内容识别填充（`cv2.inpaint` Telea，大区域先缩小补再放大，并按周围高频细节的强度补回噪点）/ 油漆桶 / 渐变 / 文字 / 自由变换（预览时只给图层挂一个仿射矩阵，渲染代理图时 warp；提交时按原尺寸 warp，缩小很多时先盒式缩小防锯齿） |
| 滤镜 | `filters.py`：每个滤镜 `fn(rgba, scale, **参数)`；对话框里改参数时只在当前显示级别的代理图上算（`Document.previews`），确定后按原尺寸分条应用（每条上下多算一圈），有选区时按选区混合 |
| 打开 / 保存 | 与「导出分享」共用读图（RAW 用内嵌全尺寸预览）；导出带原片 EXIF（方向改为 1、更新宽高、可去 GPS）与 ICC；整张合成分 512 行一条、多线程并行。`.pcedit` = zip（document.json + 每层 PNG + exif.bin / icc.icc），扩展名加进 `formats.SIDECAR_EXTS`，与原片同名时作为附属文件跟着照片移动 / 改名 |

## 9.55 两个主界面：相册 / 修图（ui/photos/ + ui/studio.py）

主窗口顶栏中间是 `ModeSwitch`，下面一个 `QStackedWidget`：0 = 相册（原来的导航 + 页面，最前面加了「00 照片」），1 = 修图（`StudioMode`）。

| 部分 | 实现 |
|---|---|
| 数据层 `core/smart.py` | 纯计算、不碰 Qt。`timeline(groups, level)` 按天 / 月 / 年分段（新的在前，带地点摘要与封面：打星 / 留用 > 清晰 > 中间那张）；`shelves()` 生成智能相簿（媒体类型、我的相簿、人物、地点、旅行、场景）；`Marks` 管收藏 ♥ 与手动相簿，存在 annotations.db 的 `favorite` / `album_items`（按图片文件指纹，移动 / 改名后有效）/ `albums`；`SearchIndex` + `search()`：每张照片准备好各类可搜索文字（归一化：NFKC、小写、去空白），一句话按空格拆词，每个词在 文字 / 地点 / 人物 / 场景 / 日期与时段（`date_matcher`：年、年月、月、今天 / 去年 / 上个月、季节、早上 / 晚上…）/ 相机 / 旅行 / 相簿 / 类型词 / 文件名 里找，词与词取交集；整句去掉空格再在照片文字里找一遍（一句话被拆成几行也能命中）；`matched_lines()` 给出要高亮的行 |
| 文字识别 `ai/ocr.py` + `ai/ocrindex.py` | PaddleOCR PP-OCRv4 移动版两个 ONNX（RapidOCR 转换，检测 4.7 MB + 识别 10.9 MB，字表在识别模型的元数据里），onnxruntime 运行，不依赖 paddle / pyclipper：DB 概率图 → 轮廓 → 最小外接矩形，按“面积 × 1.6 / 周长”外扩（矩形的 unclip）→ 透视拉正每行 → 高 48 分批识别 → CTC 贪心解码，丢掉分数 < 0.5 的行。用 1600 px 长边的预览识别（检测缩到 960），一张约 0.1–0.4 秒。结果（每行文字、分数、四角坐标 0–1）按主图指纹存 cache.db 的 `ocr` 表；没有文字也存空列表，不重复识别。扫描时在 CLIP 之后自动跑（`ocr_enabled`），旧照片由相册页在后台识别，扫描开始时让路 |
| 相册界面 `ui/photos/` | `grid.py`：`PhotoGrid`（分段正方形缩略图）与 `CardGrid`（封面卡片，年 / 月视图与相簿页）都是自绘的 `QAbstractScrollArea`，按段记录纵坐标、只画可见行，几万张也流畅。`page.py`：`PhotosPage`，视图栈 + 返回历史；搜索 280 ms 防抖，精确结果立即出，CLIP 画面搜索在后台线程补到下面一段。`detail.py`：单张页（缩略图先显示，后台读 1600 px 预览）+ 信息栏；`canvas.py`：大图画布，叠加文字框（悬停显示、单击复制）与裁剪框 |
| 基础编辑 `edit/quick.py` | `QuickState` = 滑块 + 自动增强参数 + 滤镜（按强度与“不调整”插值：数值、曲线偏离对角线的部分、HSL 都乘强度）+ 转 90° / 翻转 / 拉直（自动放大到没有空角）/ 裁剪框。像素全部交给 `adjust.develop`（与调整图层同一套算法）；预览在 ≤ 1100 px 的代理图上算，停手后用 1600 px 补一帧；存储时按原尺寸重新算，`io.export_image` 写「原名_编辑.jpg」（保留 EXIF），然后重新扫描并打开新照片 |
| 修图模式 `ui/studio.py` | `open_editor()` 建好 `EditorWindow` 后，如果 `ctx.studio` 存在就交给 `StudioMode.adopt()`：去掉窗口标志、放进标签页，并把窗口里所有 `WindowShortcut` 的 QAction 改成 `WidgetWithChildrenShortcut`——单键快捷键（V、B、X…）只在这个标签页里生效，不会在相册或别的标签里误触发。关闭标签 = 调用修图窗口自己的 `close()`（会问要不要保存），窗口销毁后标签自动去掉；退出程序前逐个关闭，有一个取消就不退出 |

## 9.56 自动同步（core/watch.py + Library.sync + ui/autosync.py）

设置 `auto_sync` 打开时，一直监视「资源接入」里的文件夹（`source_dirs`，按 `recursive`），照片新增 / 删除 / 改动后增量更新照片库，不用重新扫描。

| 部分 | 实现 |
|---|---|
| 快照 `watch.snapshot` | {normcase 路径: `Entry(路径, 大小, 修改时间)`}，与扫描同一套规则（`scanner._iter_dir`、`file_kind`、跳过 .pctmp / ~$）。根文件夹本身不在了（拔掉移动硬盘、网络盘断开）时沿用上一份快照里它的记录——离线不算删除 |
| 监视 `FolderWatcher` | 后台线程。Windows：每个根文件夹一个 `ReadDirectoryChangesW`（ctypes，含子文件夹，`_Notifier` 线程只负责叫醒，停止时 `CancelIoEx`），平时每 60 秒兜底比较一次；其他系统 / 不支持通知的文件夹按 3 秒（快照很慢时 × 5）轮询。醒来后先清标志再拍快照，拍的过程中的新变化会再叫醒一次 |
| 稳定才算数 `_settle` | 与已提交状态不同的文件，要在相隔 1 秒的两次快照里一样（删除 = 连续两次都不在），并且 `_ready`：`os.stat` 与目录列表一致（NTFS 的目录列表在文件关闭前可能是旧的）、`still_writing` 以“不许别人写”的共享方式打开不报共享冲突——正在复制的文件不会被读成半张图。一部分稳定了就先回调（大批拷贝时边拷边出现），只剩被占用的文件时间隔逐步放长到 30 秒。开始监视时 2 分钟内新建 / 改过的文件先当作“未稳定” |
| 回调 `Change` | `files`（已提交的完整快照）+ added / removed / modified + `unsettled`（还在变的文件）。`poke(full=True)`：没变化也回调一次当前快照 |
| 增量同步 `Library.sync` | 与 `scan` 共用 `Library._lock` 互斥。`build_groups` 按快照分组（`_FileEntry` 冒充 `os.DirEntry` 交给 `scanner.group_directory`，规则与扫描完全一致），与照片库对账：一组的文件全部在快照里且大小 / 修改时间不变、快照里对应的组恰好就是这些文件（跨文件夹配对的 RAW + JPG 在快照里是两组）→ 原样保留（同一个对象）；含 `unsettled` 文件的组也不动。其余快照组要分析：主图（其次任一文件）原来属于哪组就沿用那组的 gid，否则追加到末尾；对不上的旧组换成空组占位——**gid 永远等于下标且不挪动**，详情页、选择、AI 向量（按 gid 存）都不受影响。新 / 变了的组走 `_read_files`（扫描的 1–3 步，元数据与像素都先查缓存；任务按列表下标 `idx` 回填），之后跨文件夹配对（不 renumber）、`run_meta_analyzers`（只传有文件的组）、换上新列表，progress 发 `stage="ready"`，最后 `run_ai` 只算新照片（`SyncReport.ai_new`）。OCR 只载入缓存，没识别过的交给相册页在后台识别 |
| 界面 `ui/autosync.AutoSync` | 挂在 `ctx.autosync`，主窗口启动 600 ms 后 `restart()`；`auto_sync` / `source_dirs` / `recursive` 等设置变化（`ctx.set_setting`，同一轮事件合并成一次）时重启监视，开始时按现状同步一次（照片库已是最新则什么都不变）。监视线程的 `Change` 经信号回到界面线程，只留最新一份；`ctx.busy` 或开着模态对话框时不同步、记 `_dirty`，空下来后丢掉旧快照、`poke(full=True)` 按现状补同步——本软件自己移动 / 改名的文件 `apply_moves` 早已更新，补同步时什么都不变。`Progress` 是同一个对象反复改写，跨线程前复制一份；收到 ready 就发 `library_changed`，AI 补完（`ai_new`）再发一次。全量扫描开始前 `cancel()` 正在进行的同步 |
| 入口 | 资源接入页“自动同步”勾选框 + 一行状态；相册顶栏右侧「自动同步 ▾」菜单（添加要同步的文件夹…、开关、立即检查、文件夹列表）；相册空状态「选择照片文件夹…」= 加入数据源并开启自动同步 |

测试：`tests/test_autosync.py`（快照与分组和扫描一致、离线根文件夹、从空库同步 = 扫描、增删改后 gid 与对象保留、unsettled、移除数据源、跨文件夹配对、监视线程的增删改 / 刚拷进的文件 / 写完才报 / Windows 变化通知、界面控制器的忙时推迟与开关）、`tests/ui_autosync.py`（真实主窗口走一遍并截图）。

## 9.57 v2.3：闭眼检查、隐藏相簿、批量编辑、隐私体检、手机互传

| 部分 | 实现 |
|---|---|
| 闭眼 / 表情 `ai/faceq.py` | 只看连拍组（`feats["similar"]`）里扫描时有脸的照片：1600 px 预览上重新跑 YuNet（要五点关键点），Umeyama 相似变换按 ArcFace 模板对齐成 224（眼睛）/ 112（表情）。闭眼分 = 中文 CLIP 对整张脸的“闭眼”把握（8 条描述 softmax）− 像素睁眼程度（眼睛中心几列里穿过中线的连续暗像素高 ÷ 两眼间距）；在本机约 300 张真实人脸上人工标注：单用像素 AUC 0.78、CLIP 0.94、合起来 0.95，模拟连拍（和自己比）阈值 0.70 / 高出 0.15 时精确率约 92%。表情 = OpenCV Zoo MobileFaceNet（cv2.dnn，输出 7 类 logits，RGB、(x/255−0.5)/0.5）；模型常把严肃 / 低头 / 闭眼判成 sad，所以“表情不佳”要求同一人睁眼的几张里最好的那张 happy ≥ 0.5，这张负面 ≥ 0.5 且好的程度低 0.3 以上。同组里按位置 / 大小把脸连成“同一个人”（`_tracks`；每张只有一张脸、组里也只有一个人时拉近拉远也算同一人），只和自己比，只出现一次不判断。结果按主图指纹借 cache.db 的 `ocr` 表（版本键 `faceq-v1`）存 JSON；CLIP / 表情模型后来才有时重查。`Library.regroup_similar` 之后 `faceq.apply` 载入缓存、判断、重排每组 rank / best（先清晰度够不够，再闭眼人数、表情不佳人数，最后清晰度） |
| 隐藏 `core/smart.Marks` | annotations.db 新表 `hidden`（按指纹）与 `lock`（`hidden` → 加盐 PBKDF2-SHA256 20 万次）。`smart.shown()` 过滤掉隐藏的，`shelves` / `SearchIndex` / 相册页 `groups()` / 手机互传快照都用它；「已隐藏」= `media:hidden`，锁着时不给 gids 和封面，`Marks.unlocked` 只在「已隐藏」相簿和它的单张页里为真 |
| 批量编辑 `edit/quick.py` + `ui/photos/batch.py` | `QuickState` 加 `negative`、`match`（参考照片的 Lab 均值 / 标准差 + 强度）、`reauto`；处理顺序 几何 → `pre_color`（`edit/negative.invert` → `edit/colormatch.transfer`）→ 调整。`paste_state` 只留勾选的部分，`for_photo` 按每张重新算自动增强。负片：sRGB 解码 → 每通道密度 −log10 → 中间 90% 区域 0.5% / 99.5% 分位拉伸（片基橙色和偏色一起去掉）→ 轻 S 曲线；也注册成修图窗口的滤镜 `negative`（`whole=True`） |
| 隐私体检 `ops/privacy.py` + `ui/privacy_dialog.py` | 文字规则（手机 / 座机、身份证含校验位、银行卡 Luhn、车牌、快递面单关键词 / 单号、门牌地址、邮箱）作用在 OCR 行上，命中整行；`cv2.QRCodeDetector.detectMulti` + 条形码检测器；人脸：已命名或出现 ≥ 5 张的人、面积 ≥ 最大脸 40% 的算主要人物（默认不打），其余是路人。`ExportOptions.redact`（gid → 0–1 框）在 `render_one` 里原尺寸打码后再缩小，网页相册同样生效；导出窗口的开关 `export_privacy_check` 默认关 |
| 手机互传 `lan/` + `core/qr.py` + `ui/photos/phone.py` | `ThreadingHTTPServer`，窗口开着才运行；所有路径在 `/<随机口令>/` 下（`secrets.compare_digest`），否则 404。上传 = 网页 PUT 原始字节（不解析 multipart），只收照片 / 视频扩展名、`sanitize_component` 后的文件名，先写 `.pcupload` 再 `os.replace`，同名同大小跳过、重名加编号，`os.utime` 设成手机上的文件时间；上传文件夹默认是第一个数据源下的「手机上传」，由自动同步接进相册。浏览 = 界面线程整理的只读 `Snapshot`（不含隐藏的），缩略图用缩略图缓存，大图按需生成（LRU），原文件支持 Range，RAW 给 JPEG。二维码：自己写的 QR 编码（字节模式、版本 1–10、RS 纠错、8 种掩模扣分），测试里用 OpenCV 解码核对 |

测试：`tests/test_extras.py`、`tests/ui_extras.py`。

## 9.6 合并曝光（edit/hdr.py + ui/editor/merge.py）

同一场景、不同曝光的 2–9 张 → 一张。只用 numpy / OpenCV，不新增依赖。目标是**不损失清晰度**，所以每一步都围绕“少重新取样、对得准”来做：

| 步骤 | 实现 |
|---|---|
| 参考帧 | 曝光居中的一张（有 EXIF 按 log2(快门 × ISO / 光圈²)，没有按画面亮度；偶数张时中间两张里挑过曝 / 欠曝少的）。参考帧**不做任何重新取样**；移动的物体以它为准 |
| 粗对齐 | 灰度图按直方图匹配到参考帧的亮度 → SIFT（长边 2400）→ RANSAC 单应矩阵 → 用全部内点最小二乘重拟合。特征太少时退到相位相关（只求平移），再不行用单位矩阵交给精修 |
| 精修 | `_Tracker`：两张都做**局部对比度归一化**（减去 σ=5 邻域均值、除以邻域标准差），在参考帧上取几千个角点（两张都没过曝 / 欠曝的地方），金字塔 LK 光流跟踪，来回各跟一次、往返误差 > 0.08 px 的扔掉，RANSAC + 最小二乘拟合剩余修正，迭代 3 次（长边 4000）。曝光不同时直接比亮度（ECC、未归一化的 LK、相位相关）都会被色调曲线带偏 0.3–3 px；归一化后，在 4 EV 曝光差、起点偏 12 px 的合成测试里四角误差 ≤ 0.12 px |
| 串联 | 按 EV 从参考帧往外处理；每张既直接对参考帧，也先对曝光相邻、已对齐的那张再串起来，两个候选都精修，用“对齐后再跟踪一遍的剩余错位中位数”挑小的。对不上、而且 70% 以上都过曝 / 死黑的那张不参与（它能贡献的很少，对不准反而添重影） |
| 变换 | 位移 < 0.05 px（三脚架）原样使用；否则 `warpPerspective` + Lanczos4，边缘几像素和没画面的地方权重为 0 |
| 去重影 | 长边 1600 上比较。从曝光离参考帧最近的一张开始，与已确认的几张（参考帧优先）逐通道比：每个通道只用两边都可靠的像素学一张直方图匹配表（单调映射）。两边都没削平：两个方向的差取较小的（暗图提亮会放大噪点，只有两边都对不上才算），超过阈值；一边削平（≥248 或 ≤6）：只在映射学到过的范围内单向比较，“另一张映射过来明显不该被削平”也算——这样参考帧过曝的天空里走过的人也查得出。开运算去噪点、去掉小连通域、闭运算、膨胀、羽化，得到每张的“不参与”蒙版 |
| 融合 | Mertens：权重 = 拉普拉斯绝对值 × 通道标准差 × Π exp(−(v−0.5)²/0.08)，再乘有效区与去重影蒙版；拉普拉斯金字塔逐级累加（逐级算完就丢、原地累加；5 张 2400 万像素峰值约 2 GB、约 15 秒）。对比度项让更清晰的那张权重更高 |
| 文档 | `to_document`：参考帧（可见，“看原图”看到的就是它）、其余源图（隐藏，`params.merge_source`）、「合并结果」（`params.merge_result`）。`SourceInfo.suffix = "_HDR"`：导出「参考帧名_HDR.jpg」、工程「参考帧名_HDR.pcedit」，不和参考帧自己的工程混在一起 |
| 修补重影 | `ghost_patch`：补丁是对齐后源图的原样像素（不再重新取样），只取选区外接矩形；亮度用选区周围一圈（排除羽化带）逐通道直方图匹配到合并结果；蒙版 = 选区羽化，选区内恒为 255。放在合并结果上方的新图层（`params.ghost_fix`），可撤销、可在蒙版上修边 |

测试：`tests/test_hdr.py`（合成的手持包围曝光：对齐误差、锐度不低于参考帧、移动物体处与参考帧一致、静止场景不误判、修补的亮度匹配、工程往返）、`tests/ui_merge.py`（挑片页 → 对话框 → 修图窗口 → 修补重影 → 保存工程）。

## 9.7 修图进阶：16 位、RAW、图层组、变换、修饰工具（v1.8.0）

| 部分 | 实现 |
|---|---|
| 16 位 | `Document.depth`（8 / 16）决定新建、合并、拼合出来的像素类型；每个图层按自己数组的类型换算到 0–1（`to_float` / `from_float` / `convert`，8→16 = ×257），所以同一文档里可以混用（文字图层总是 8 位渲染）。代理图、缩放、预乘、滤镜分条、画笔、仿制、修复、合并都按数组自己的满值算。蒙版和选区仍是 uint8。OpenCV 修复只收 8 位彩色图：在 8 位上补好，只把“补上的差值”×257 加回 16 位原图。调整的查找表改为线性插值，否则 16 位拉伸后仍只有 4096 级 |
| 16 位读写 | 16 位 PNG / TIFF 用 `cv2.imdecode`（Pillow 读不了 48 位 RGB），EXIF 方向自己转；导出 16 位 PNG = `cv2.imencode` 再插入 iCCP / eXIf 块；16 位 TIFF 自己写（Deflate + 水平差分预测，ICC 标签 34675，相机 / 拍摄时间写成 IFD0 文字标签）。工程里每层记 `depth`，16 位图层存 16 位 PNG |
| RAW | `edit/raw.py`：rawpy（LibRaw）`postprocess(output_bps=16, sRGB, BT.709 伽马)`；白平衡 = 相机记录 / 自动 / 日光系数 / 自定义四个系数。吸管：在按当前系数显影的预览上取一小块，去掉 BT.709 伽马后按 G/R、G/B 修正系数（合成 DNG 上一次就收敛到 <0.5%）。`ui/editor/rawdlg.py` 预览用半尺寸 + 线性解马赛克。测试用的 RAW 由 `tests/raw_fixture.py` 现写一个未压缩 Bayer DNG（颜色矩阵 = diag(增益)·XYZ→sRGB，AsShotNeutral = 增益） |
| 图层组 | 不改成树：组（`kind="group"`，像调整图层一样没有像素、蒙版与画布同尺寸）和子图层仍在同一个平铺列表里，子图层用 `parent` 指向组，且连续排在组的正下方（“单元” = 组 + 全部后代，`Document.unit()`）。渲染时临时建 `父 id → 子图层` 表递归合成：穿透 = 子图层直接叠到下面的结果上，再按组的不透明度 × 蒙版与之前的结果插值；其他模式 = 先在透明底上单独合成再按组的模式叠上去。上移 / 下移按 Photoshop 的规则进出组；复制组时重新映射 `parent` |
| 剪贴蒙版 | `Layer.clip`：同一层级里，基底 = 下面最近的非剪贴图层。合成时基底和它上面连续的剪贴图层作为一组：基底按“正常、100%”单独画出来，剪贴的像素图层按自己的模式在 alpha = 1 的底上合成后只保留颜色（相当于 source-atop），剪贴的调整图层直接调这一组的颜色；最后按基底的模式和不透明度叠上去 |
| 拖滑块缓存 | `hot` 缓存的是“热图层所在的顶层单元下面”的合成结果（剪贴图层退到它的基底），签名包含 `clip` / `parent` |
| 透视 / 变形 | `edit/warp.py`：图层的 `xform` 可以是 2×3 仿射、3×3 单应矩阵或 `{"mesh": 4×4 控制点}`。预览时在代理图级别直接 warp（矩阵按 T·S⁻¹·H·S 换算到级别）。变形网格 = 双三次贝塞尔曲面：切成 n×n 个四边形、每个两个三角形，用 `fillConvexPoly` 画“像素属于哪个三角形”的编号图，再用每个三角形的仿射逆变换一次算出 remap 用的源坐标（全向量化）；提交时按 1024 行一条分条算。自由变换工具以 3×3 矩阵为状态：缩放在图层自己的坐标系里做（透视后也成立），旋转 / 移动左乘画布变换，扭曲 / 斜切 / 透视改四个角再求单应矩阵（角拖到“地平线”后面时拒绝） |
| 修饰工具 | `paint.Stroke` 新模式：减淡 / 加深 / 海绵按 `tone_target`（亮度权重：暗部 (1−L)²、中间调 4L(1−L)、高光 L²）算目标色，按笔刷覆盖度混合（同一笔不叠加）；模糊 / 锐化的目标从笔触开始时的快照里按块现算（四周多取 3σ、图层边缘复制边缘）；涂抹逐个笔触点混合当前像素和“手指上的颜色”，手指再沾上混合结果 |
| 工具栏 | `TOOL_SLOTS`：一格可以放几个工具（右键 / 长按弹菜单）；共用快捷键的工具注册成一个动作，Shift + 键在格里轮换 |

测试：`tests/test_editor_pro.py`（16 位精度与各操作保持位深、16 位 PNG / TIFF / 工程往返、RAW 白平衡与吸管、剪贴与组的合成结果、组的移动 / 复制 / 合并 / 取消、hot 缓存、透视与变形的预览 = 提交、六种修饰工具）、`tests/ui_editor_pro.py`。

## 9.8 修图进阶第二档（v1.9.0）

| 部分 | 实现 |
|---|---|
| 新调整 | `edit/coloradj.py`：调整参数里的 6 个子字典（`{"on": …}`），在分离色调之后、反相之前按 色彩平衡 → 可选颜色 → 通道混合器 → 照片滤镜 → 黑白 → 渐变映射 应用。黑白 / 可选颜色用同一种颜色分解：最小通道 = 白色成分，次大 − 最小 = 二次色（黄 / 青 / 洋红，由最小通道决定），最大 − 次大 = 主色；黑白默认权重 40/60/40/60/20/80 与 Photoshop 一致。色彩平衡用 GIMP 的三段平滑权重 |
| 选区 | `edit/select.py`：色彩范围 = Lab 距离（取样）或色相 / 亮度权重（预设）；选择并遮住 = 在选区边缘带（膨胀 − 腐蚀，半径 r）里做导向滤波（灰度引导图，ε = 1e-4，大图降采样），带外保持原选区，再平滑 / 羽化 / 对比度 / 移动边缘。存储的选区 = `Document.channels`（名称 → uint8），进历史状态，裁剪 / 旋转 / 翻转 / 改尺寸时一起变换，工程里存 `channels/NNN.png`。快速蒙版：画笔对一个临时的“蒙版图层”（内容 = 选区）画，松开时 `set_selection` 记一步历史；画布在没选中的地方叠红色 |
| 滤镜 | `edit/filters2.py`：`FilterSpec` 加 `whole`（扭曲类按整个图层的几何中心算，原尺寸应用时不分条）、`margin`（分条时每条多算几像素）、`choices`（下拉选项）。扭曲 = 算好源坐标后一次 `remap`（预乘 alpha、三次插值）；镜头模糊 = 圆盘卷积（伽马 2.2 线性化、亮部按“镜面高光”放大） |
| 液化 | `edit/liquify.py`：预览（长边 ≤1400）上的位移场 D；每一笔 D'(x) = D(x + δ(x)) + δ(x)（对 D 自己 remap，来回推不会越推越糊）；重建 = D 按笔刷衰减。应用时把 D 放大到原尺寸、分 1024 行一条 remap 原图——只重新取样一次 |
| 修补 / 红眼 | `paint.patch`：`cv2.seamlessClone`（泊松融合）把拖到的那块融进选区；16 位在 8 位上算、只加回差值。`paint.red_eye`：圆内“红色程度”=(R − max(G,B))/R 作权重，红通道换成 (G+B)/2 并压暗 |
| 图层样式 | `edit/styles.py`：由图层 alpha（含蒙版）加边距算出一串平面（颜色 + alpha + 混合模式）：投影 / 外发光在图层下面，图层本身（× 填充），然后渐变 / 颜色叠加、内发光、内阴影、斜面浮雕（高度图 = 模糊后的 alpha，按光源方向打光，高光滤色、阴影正片叠底），最后描边（距离变换）。每个级别按 (版本, 样式) 缓存，按区域叠加；`_pixel_bounds` 把样式边距算进合并范围；向下合并后样式清空（已经合进像素） |
| 锁定 / 填充 | `Layer.lock_alpha / lock_pixels / lock_pos / lock_all`：锁像素时 `_editable()` 返回空（填充、滤镜、修复都不动），锁位置时 `translate()` 和变换工具拒绝，锁透明像素时画笔 / 填充 / `replace_pixels` 保留原 alpha。`Layer.fill` 只乘在图层自己的像素上，不乘样式 |

测试：`tests/test_editor_pro2.py`、`tests/ui_editor_pro2.py`。

## 9.9 修图进阶第三档（v2.0.0）

| 部分 | 实现 |
|---|---|
| 钢笔 / 形状 | `edit/vector.py`：路径 = 子路径列表，锚点带进 / 出控制柄（三次贝塞尔），展平成折线；区域按非零环绕填充，4×4 超采样算覆盖率（画布像素 i 占 [i, i+1)）。形状图层 `kind="shape"`：参数（矩形 / 圆角矩形 / 椭圆 / 多边形 / 直线 / 路径 + 填充 / 描边）→ 像素，像文字图层一样随参数重画；图层被移动后靠参数里的 `rx / ry`（上次渲染位置）把位移并进几何。自由变换 / 改尺寸时变换几何（转成路径），保持矢量 |
| 智能对象 | `edit/smart.py`：`Layer.source` 存原始像素，`params.H` = source → 画布的 3×3 矩阵，`params.filters` = 智能滤镜列表。渲染总是从 source 一次重新取样（纯平移不重采样、缩小先区域平均），再依次跑启用的滤镜；自由变换把矩阵左乘进 H，改尺寸把缩放并进 H。source 进历史快照、工程里存 `smart/NNN.png` |
| 参考线 / 吸附 | `Document.guides`（进历史、随工程保存、随裁剪 / 翻转 / 改尺寸变换）。画布在左上画标尺（刻度按缩放取 1/2/5 序列），从标尺拖出参考线、移动工具拖动或拖回标尺删除。`snap_value` 在屏幕 6 像素内吸附到参考线 / 网格 / 画布边和中线；移动图层时取外接框的左中右 / 上中下三条边里离目标最近的那条 |
| 动作 / 批处理 | `edit/actions.py`：步骤是和界面无关的字典（滤镜、调整图层、尺寸按比例、旋转、翻转、位深、拼合、盖印），`play()` 直接对 Document 执行，所以批处理不需要打开窗口。窗口在执行这些操作时 `_rec()`；停止录制时调整图层换成它最终的参数。动作存在设置里（`editor_actions`） |
| 全景 / 景深 | `edit/stitch.py`：OpenCV Stitcher（PANORAMA / SCANS）+ 最大连通区域 + 逐边收缩的内接矩形自动裁剪。景深合成：复用合并曝光的对齐，清晰度 = 模糊后的拉普拉斯能量，权重 = 归一化清晰度的 4 次方，拉普拉斯金字塔融合 |
| 色彩管理 | `edit/color.py`：Pillow 的 LittleCMS。校样 = proof transform（sRGB → 校样配置文件 → sRGB），色域警告 = 往返误差 > 阈值标灰，只在画布显示缓冲上做。转换配置文件：8 位 33³ 网格算查找表，三线性插值作用于 8 / 16 位像素。CMYK 导出 = `profileToProfile(…, outputMode="CMYK")` 并嵌入配置文件 |
| PSD | `edit/psd.py`：自己写 / 读文件结构（头、ICC 资源 1039、图层记录、通道数据、合成图）。写：通道不压缩；组 = `lsct` 分隔层（组结束分隔层排在子图层下面）；剪贴、填充（`iOpa`）、Unicode 名（`luni`）、蒙版矩形。读：RAW / RLE（借 Pillow 的 packbits 解码器）/ ZIP / 带预测的 ZIP，8 / 16 位 |
| 画板 / 切片 / 时间轴 | `Document.regions`（画板、切片矩形，进历史、随工程保存），导出时从合成结果裁出；`Document.frames`（每帧显示哪些图层 + 停留时间），`edit/anim.py` 按帧合成（用视图对象，不改真实的显示状态）导出 GIF（Pillow）/ MP4（OpenCV mp4v），视频帧导入用 `cv2.VideoCapture` |
| 天空替换 / 皮肤平滑 | `edit/sky.py`：缩小图上按“亮 + 低梯度 + 偏蓝 / 偏白 / 暖色”找候选，只留和上边相连的大块，闭运算、外扩后放大，再用选择并遮住的导向滤波精修；替换结果是组（新天空 + 蒙版、照片滤镜只作用于前景）。皮肤平滑 = 肤色范围蒙版里的两次双边滤波 + 按比例加回高频细节 |

没有做的：3D（Photoshop 已移除）、需要大模型的生成式功能、视频剪辑（只做了帧动画）、CMYK / Lab 编辑模式。

测试：`tests/test_editor_pro3.py`、`tests/ui_editor_pro3.py`。

## 10. 安全搬运（ops/）

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
| 再次分区：移动到数据源以外的文件夹后，数据源自动加上目标（目标包含旧数据源时合并） | `planner.sources_after_move` + `partition_page._done` → `source_page.set_dirs` |
| 搬空的旧文件夹自动清理：只删数据源 / 目标文件夹**里面**真正空了的子文件夹，根文件夹本身不删；撤销时 `makedirs` 重建 | `mover.execute(keep_roots=…)` → `mover.prune_emptied` |
| 撤销依赖链：批次 A 的照片又被后来的批次 B 移动 / 改名过时，撤销 A 先提示，再按从新到旧依次撤销 B…A | `journal.later_dependents` + `history_page._undo` |
| 删除一律进回收站；清理的撤销从回收站放回 | `mover.trash`（send2trash）、`mover.delete`、`trashbin.restore_from_trash` |

这些行为都有对应的单元测试（`tests/test_core.py`、`tests/test_repartition.py`），包括模拟跨盘、模拟中途失败、模拟崩溃后恢复。

## 11. 界面设计

- **视觉语言**（`ui/theme.py` + `ui/widgets/deco.py`）：直角与切角、细线框 + 单侧定位角标、编号章节（01–08）、中文标题 + 英文等宽小标签、黄色强调块、斜纹警示带（只用在“会改动文件”的确认窗口）、分段进度条。全部自绘，不用图片素材。
- **字体**：中文“微软雅黑”，数字与英文用 Windows 自带的 Bahnschrift（DIN 风格窄体），等宽用 Cascadia/Consolas。
- **两套主题**：浅色「终末地」（浅灰工业底 + 高可见度黄）/ 深色「方舟」（深色终端 + 金黄 + 青色信息色）。颜色只在 `Palette` 里定义一次，所有自绘控件在绘制时读取当前配色。
- **性能**：缩略图网格是虚拟化的 `QListView`（只绘制可见项），缩略图 LRU 缓存；预览树和统计都是内存计算，几千张即时刷新；所有耗时任务在 `QThread` 中运行，界面不卡。

- **轻量动效**（`ui/motion.py`）：原生 `QVariantAnimation` 驱动导航定位条（220ms）、按钮反馈（150ms）与旧页面快照淡出（160ms）；不在照片画面上叠加特效。隐藏时动画直接结束；`ui_motion` 偏好由设置页即时应用并持久化。分段进度条只在显示、动效开启且任务进行中响应现有计时器，完成后不扫光。
- **原生预览与回归**：`scripts/preview_ui.py` 使用临时 `PHOTOCRATE_HOME` 和合成测试照片；`tests/test_ui_design.py` 验证快速切页、键盘导航、隐藏后停止、开关持久化及两套主题在 1480×920 / 1180×720 下的页面切换。

## 11.5 界面语言（i18n.py + locale_en.py）

- **写法**：界面文字在代码里写中文原文，用 `t()`（各模块里导入为 `_t`）包起来；带变量的写成 `_t("已选 {0} 张", n)`。英文在 `locale_en.EN` 表里，以中文原文为键；查不到就显示原文，所以漏翻只会露出中文，不会出错。
- **启动时确定**：`app.run()` 在导入界面 / 分析器模块之前调用 `i18n.set_language(i18n.resolve(settings))`（模块级常量在导入时就翻译了），所以切换语言要重启。优先级：环境变量 `PHOTOCRATE_LANG` → 设置 `ui_language` → 新用户跟随 Windows 显示语言，老用户保持中文。
- **数据与语言无关**：缓存、标注库、分析结果、地名、场景名一律存中文，只在显示和生成文件夹名 / XMP / 相册时翻译。判断“未知 / 其余”这类兜底条目用 `i18n.is_unknown()`，比对按模板生成的文字用 `i18n.matches()`，不要直接比较中文字面量。
- **地名**：国家用 Qt 的英文国家名；海外州 / 城市由 `data/place_names_zh.json` 反查英文原名；中国省级行政区在 `locale_en.PROVINCES`；中国的市、区县没有英文数据，保持中文。
- **模板占位符**：`partition/base.TOKEN_ALIASES` 把 `{year}` `{city}` `{seq}` 等英文写法映射到中文占位符，两种写法等价。
- **测试**：`tests/test_i18n.py`；界面脚本可设 `PHOTOCRATE_LANG=en` 跑英文界面。

## 12. 扩展：插件接口（保留给未来的新功能）

### 12.1 插件文件

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

### 12.2 新增一个像素分析器（例如 AI）

```python
class SceneAnalyzer(Analyzer):
    id = "scene"; stage = "pixel"; version = 1
    def analyze_pixels(self, px, settings):
        rgb224 = px.small(224)             # PixelInput 自带缩放/灰度缓存
        ...                                # 跑模型
        return {"label": "风景", "score": 0.83}
```

结果自动按文件指纹缓存；改了算法把 `version` 加 1，旧缓存自动失效。分区维度里用 `group.feats["scene"]` 读取。完整骨架见 `plugins_examples/example_ai_scene_stub.py`。

### 12.3 未来功能路线（接口都已就位）

| 功能 | 做法 |
|---|---|
| 更大的 AI 模型 / GPU | `ai/models.FILES` 换成更大的 Chinese-CLIP 导出；`ClipEngine` 的 providers 加 `DmlExecutionProvider`（onnxruntime-directml）即可用显卡 |
| 关键词 / 场景标签写入 XMP | 在 `ops/xmpwrite.tags_for` 里加 `XMP-dc:Subject` 等标签即可复用整套“预览 → 日志 → 撤销（含更新已有 .xmp 的备份）”流程 |
| 照片内嵌写入（JPG/HEIC） | 目前只写 RAW 的附属文件；写进原图需要 ExifTool `-overwrite_original` 并先备份，风险较高，暂未开放 |

> 注意：打包好的 exe 只包含已有的依赖（已含 onnxruntime）。需要其他第三方库的插件，用 `run.bat` 源码模式运行（`.venv\Scripts\pip install 包名`），或安装后重新打包。

## 13. 文件地图

```
PhotoCrate/
├─ run.bat / build_exe.bat / build_exe_nuitka.bat   双击入口（实际逻辑在 scripts/*.ps1）
├─ launcher.py                 打包入口（含 multiprocessing.freeze_support）
├─ requirements.txt  installer.iss
├─ photocrate/
│  ├─ app.py  bootstrap.py  config.py  plugins.py  i18n.py  locale_en.py（英文界面文字）
│  ├─ core/        models formats scanner metadata imaging pixelwork geo dates cache library stats
│  │               pairing geotime places daybook trips videos smart（相册：时间线 / 智能相簿 / 搜索）watch（自动同步：文件夹快照 / 监视）
│  │               qr（二维码编码）
│  ├─ ai/          models clip scenes faces people service visual ocr ocrindex（ocr = 照片里的文字）faceq（连拍里的闭眼 / 表情）
│  ├─ lan/         server page（手机互传：局域网网页服务）
│  ├─ analyzers/   base builtin
│  ├─ partition/   base dimensions engine
│  ├─ ops/         planner mover journal pathsafe xmpwrite cleanup trashbin renamer
│  │               importer dedupe exporter privacy（分享前隐私体检）
│  ├─ edit/        document adjust blend paint filters text io hdr（修图核心；hdr = 合并曝光）
│  └─ ui/          theme context dialogs timeplace_dialogs culling_dialogs workflow_dialogs xmpflow viewer
│                  main_window  pages/  widgets/（deco thumbgrid mapview）  editor/（window canvas tools panels icons tutorial merge）
│                  photos/（相册：page grid detail canvas）  studio（修图模式：标签页）  autosync（自动同步）
├─ data/           geonames_cities1000.csv.gz  place_names_zh.json  clip_vocab_zh.txt
├─ assets/         app.ico app.png
├─ plugins/        用户插件放这里        plugins_examples/  示例插件
├─ tools/          get_exiftool.ps1  make_icon.py  （exiftool/ 运行后自动下载到这里）
└─ tests/          test_core.py  test_timeplace.py  test_culling.py  test_trips.py  test_ai.py
                   test_workflow.py  test_repartition.py  test_search_rank.py  test_faces_screen.py  test_i18n.py
                   test_editor.py  test_editor_pro.py  test_editor_pro2.py  test_editor_pro3.py  test_hdr.py
                   make_samples.py  ui_smoke.py  ui_timeplace.py
                   ui_culling.py  ui_tier3.py  ui_workflow.py  ui_repartition.py  ui_editor.py  ui_merge.py
```

## 14. 界面细节与调试辅助

- **F12 截图**：`MainWindow.eventFilter` 在应用级拦截 F12，用 `QScreen.grabWindow` 截取窗口区域（包括弹出的对话框），存到 `screenshots_dir()`。
- **窗口位置记忆**：关闭时保存 `window_state`（几何 + 是否最大化）；设置项 `window_screen` 可指定下次在哪台显示器打开（`primary` / `secondary` / `#2` / 显示器名称片段），只生效一次。
- **结果对话框等进度窗口关闭后再弹**：`dialogs.run_with_progress` 统一处理“后台任务 + 进度窗口 + 结果”。任务很快完成时，如果在进度窗口还开着时就弹结果框，Windows 会把焦点交给别的程序，结果框落到后面、看不见却挡住整个界面（端到端测试时实际遇到过）。
- **大列表一律用 Model/View**：操作记录的文件清单可能上千行，用 `QAbstractTableModel` 按需绘制；不要对大表使用 `ResizeToContents`（每次数据变化都会扫描全部行，904 行时曾导致约 20 秒卡顿）。

## 15. 已知局限

- 海外地点用“最近的城市点（附近有明显更大的城市时取大城市）”判断，边境/海边可能归到邻近城市；国内用真实行政边界，准确度高。
- 色调与清晰度阈值没有放之四海皆准的值，默认值偏保守，建议用自己的照片在界面里调。
- 清晰度：夜景高 ISO 噪点会让分数偏高（被判为清晰）；预览太小的 RAW（个别老机型）标为“无法判断”。
- 视频只做整理（日期、旅行、分区、去重、导入）：不评清晰度、不导出、不写 XMP；画面分析只看一帧。
- 导入向导读不了以 MTP 方式连接、没有盘符的设备（iPhone、部分安卓）：先用手机自带方式导出到文件夹再导入。
- 网页相册的地图要联网加载（Leaflet + OpenStreetMap）；照片和列表离线可看。
- 跨文件夹配对需要两边都有 EXIF 拍摄时间；没有拍摄时间的 RAW/JPG 分开存放后会各算一张。
- GPS 补全的精度取决于参考数据：手机照片之间间隔很久时只能取最近的一张（时间窗口内）；GPX 轨迹没有时区问题，最准确。
- 写回 XMP 只针对 RAW；JPG/HEIC 的地点、校正时间与星级只保存在软件里。
- “留用”在 XMP 里没有标准字段（Lightroom 的旗标存在目录里），只保存在软件里。
- 相似分组只比较时间上相邻的照片（默认 10 秒内），不同时间拍的同一场景不会归为一组；pHash 对大幅裁切 / 旋转不敏感。
- 从回收站找回只支持 Windows 与 Linux（freedesktop）；macOS 的废纸篓没有可读的原路径记录。回收站被清空后就无法撤销清理。
- 没有代码签名的 exe 可能被杀毒软件误报。
- 修图：RAW 用内嵌预览（不是 RAW 解码）；8 位精度（没有 16 位 / 32 位通道）；没有图层组、路径 / 矢量形状、图层样式（描边 / 投影只对文字图层）；`.pcedit` 不是 PSD，Photoshop 打不开（可以解压取出每层 PNG）；内容识别填充用的是 OpenCV 修复，大块复杂纹理的效果不如 Photoshop 的 PatchMatch。
- 场景识别与文字搜图用的是 ViT-B/16 量化模型：常见场景、物体、氛围很准，细分品类（具体的花名、菜名）、文字内容、人名店名不认识；搜索分数只能在同一次搜索里比较。
- 人物分组依赖正脸：侧脸、遮挡、很小的脸会漏；同一个人跨很多年（小孩长大）可能分成几组，起同一个名字即可合并。人脸特征只保存在本机。
- 旅行分段需要地点：没有 GPS 的照片要先在「时间与地点」补全地点，否则只能按时间间隔分段；在常住地附近的一日游（半径 50 km 内）不算旅行，可调小半径。
- 地图底图在线加载（OpenStreetMap / 高德），首次查看需要联网；OpenStreetMap 官方瓦片服务器不适合大量请求，只建议偶尔浏览使用。
