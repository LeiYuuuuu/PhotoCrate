# PhotoCrate (影匣)

[中文](README.md) | **English**

A local photo-organizing workstation for Windows. Choose how you want your photos sorted, preview the resulting folder tree, and only then are the files **actually moved** into place. Every operation is journaled and can be undone with one click.

> Current version **v1.5.0**, which adds a Photoshop-style editor. Detailed notes for every release are in **[CHANGELOG.md](CHANGELOG.md)** (in Chinese) and on the [Releases](https://github.com/LeiYuuuuu/PhotoCrate/releases) page.
>
> **Language note:** the app's interface is in Simplified Chinese, and AI text search takes Chinese queries, because it uses the Chinese CLIP model. Folder names it creates for unknowns are also Chinese, for example `未知地点` for "unknown place". Names in square brackets below, such as 「修图…」, are the labels you'll see on screen.

## Features

- **Sort by almost anything.** Nest up to 4 levels, or write a free-form template: capture date · region (country / province / city / district) · color tone (warm / cool, dominant hue, brightness) · camera · lens · file type (RAW and JPG can go into separate `ARW/` and `JPG/` folders) · orientation · source (camera / phone / screenshot) · sharpness · focal length · ISO · resolution · original folder name.
- **Formats:** JPG, PNG, HEIC, TIFF, WebP and nearly every camera RAW format (CR2 / CR3 / NEF / ARW / RAF / ORF / RW2 / DNG / PEF…). Videos (MP4 / MOV / MTS / M2TS / AVI / MKV…) are filed by date and trip along with the photos.
- **Time & place:** see capture times and places day by day. Assign places by hand, or for a whole itinerary at once. Fill in missing GPS from phone photos or GPX / KML tracks, matched by capture time with automatic time-zone conversion. Correct a wrong camera clock or time zone, with a preview first. Optionally write results back to RAW `.xmp` sidecars.
- **RAW and JPG kept in different folders are still recognized as one photo** (matched by file name and capture time).
- **Culling:** full-screen viewer with 100% zoom to check focus and side-by-side compare with synced zoom. Rate with the keyboard: 1–5 stars, P pick, X reject, 6–9 color labels. Ratings can be written to `.xmp` for Lightroom / Bridge. Bursts and near-duplicates are grouped automatically, the sharpest shot is marked, and the rest can be moved to a review folder in one click.
- **RAW cleanup:** "keep only the JPG" or "delete unrated RAWs". Everything goes to the Recycle Bin and can be restored from the operation log. **Batch rename** with templates like `{日期}_{城市}_{序号}` (date_city_number). RAW and JPG get the same name, and the rename can be undone.
- **Trips & map:** trips are detected automatically (away from home and continuous in time) and named, for example "2026-01-01 Japan Tokyo·Kyoto·Osaka". You can rename them and sort into `trip / day N` folders. A built-in map clusters photos by location and draws each trip's route.
- **AI search (fully offline):** Chinese CLIP recognizes scenes (mountains, beach, night, food, portrait…). Find photos with a Chinese description such as 铁塔夜景 ("tower at night") or 一碗拉面 ("a bowl of ramen"), or pick one photo and find similar ones.
- **People:** faces of the same person are grouped automatically. Name a group once and later photos are recognized. You can sort by person.
- **Import wizard:** insert a memory card or connect a phone, then copy to the PC (anything imported before is skipped), into folders built from rules like `year / date`, with optional renaming. The new photos join the library automatically.
- **Duplicate cleanup:** finds copies by file content, even when renamed. One copy per group is kept; the rest go to the Recycle Bin, and the cleanup can be undone.
- **Export & share:** one-click JPGs sized for social media, with a watermark and GPS removed. Turn a trip into a web album, grouped by day with a route map, that opens with a double-click. Scenes, people and trip names can be written as `.xmp` keywords that Lightroom can search.
- **Photo editor (Photoshop-style):**
  - Layers (pixel / text / adjustment), 18 blend modes, opacity, layer masks.
  - Selection tools: marquee, lasso, magic wand, and object select, which cuts out the subject automatically.
  - Crop with straighten, and free transform.
  - Painting and retouching: brush, eraser, clone stamp, spot healing brush, content-aware fill, paint bucket, gradient.
  - Adjustments: exposure, contrast, highlights, shadows, white balance, curves, levels, HSL, split toning, clarity, sharpen, denoise, vignette and grain, plus 14 one-click presets (film, Japanese airy, cinematic…).
  - Filters with live preview: Gaussian and motion blur, unsharp mask, high pass and more.
  - Unlimited undo with a history panel.
  - **Originals are read-only:** export writes a new file (EXIF kept), or you can save a `.pcedit` project to continue later.
- **Also:** statistics overview (click any bar to see its photos), blur detection, operation log with undo, crash recovery, plugins, **F12 screenshot**, and window position memory across multiple monitors.
- **Interface:** industrial sci-fi terminal style with two themes, light 「终末地」 and dark 「方舟」. Short UI animations can be switched off in Settings → Appearance.

![Partition matrix](docs/screenshots/partition_light.png)
![Sources (dark)](docs/screenshots/source_dark.png)
![Time & place](docs/screenshots/timeplace_light.png)
![Culling](docs/screenshots/culling_dark.png)
![Side-by-side compare](docs/screenshots/viewer_compare.png)
![Trips & map](docs/screenshots/travel_map.png)
![AI search](docs/screenshots/ai_search.png)
![Editor: adjustment layer + curves](docs/screenshots/editor_adjust.png)
![Editor: free transform (dark)](docs/screenshots/editor_dark.png)

---

## 1. First run (3 steps)

1. Install **Python 3.12** from <https://www.python.org/downloads/windows/>. On the last installer screen, tick **Add python.exe to PATH**.
2. Put the whole `PhotoCrate` folder anywhere. Non-ASCII characters in the path are fine.
3. Double-click **`run.bat`**.

The first launch creates a private environment `.venv`, installs dependencies (a few minutes), downloads ExifTool, and starts the app. After that, `run.bat` opens it in seconds.

> If PowerShell says scripts are blocked: right-click `run.bat` → Run as administrator once, or run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` in PowerShell.

## 2. Build an .exe (optional)

Run `run.bat` at least once, then double-click **`build_exe.bat`**:

- It produces `dist\PhotoCrate\PhotoCrate.exe`. The whole folder can be copied to another PC and run without Python.
- If [Inno Setup 6](https://jrsoftware.org/isdl.php) is installed, you also get an installer, `dist\PhotoCrate-Setup.exe`.
- `build_exe_nuitka.bat` is an alternative compiler. It starts faster but needs Python 3.12 or older, and the first build takes 10–20 minutes.
- Unsigned executables are occasionally flagged by Windows Defender; choose "Run anyway". For personal use, `run.bat` is recommended.

## 3. Pages

| Page | What it does |
|---|---|
| **01 Sources** 「资源接入」 | Add photo folders → Scan. After a trip, use **Import card…** 「导入存储卡…」 first. Pick photos in the thumbnail grid below (none selected = process all). An orange 「日期推测」 tag means the photo has no EXIF time, so it was inferred from the file name or modification time. |
| **02 Time & place** 「时间与地点」 | Days are listed on the left with photo count, time span, place and place source (GPS / track / manual / none). Select days, or a few photos below, then: **assign a place** (typing 日本/京都 suggests Kyoto, Japan; "assign by itinerary" fills many days at once); **fill GPS** (add a phone-photo folder or GPX / KML track, then re-match); **correct time** (e.g. camera set to Beijing time while in Japan → choose +08:00 → +09:00 and preview which photos change day before applying); **write to XMP** (optional). These edits live in the app only; photo files are untouched. |
| **03 Partition** 「分区编排」 | Stack conditions, or use a quick preset or advanced template. The middle shows the folder tree that will be created, with counts; click a folder to see its photos. Set the target folder, move or copy, name-collision handling and a separate RAW subfolder, then **Generate plan** 「生成执行计划」. |
| Confirm plan | A complete source → destination list (exportable as CSV). Files are only touched after you confirm. |
| **04 Overview** 「统计概览」 | Distributions by time, region, tone, camera and more; click any entry to list its photos. |
| **05 Culling** 「挑片」 | Select a photo and click **Edit…** 「修图…」 (or press **E** in the viewer, or right-click a thumbnail) to open the editor; see "Photo editor" below. Double-click or press Space for the viewer: Z / double-click toggles 100% zoom at the cursor, scroll to zoom, drag to pan, C for side-by-side compare (zoom and position synced), ← → to move. On thumbnails or in the viewer: **1–5 stars, 0 clear, P pick, X reject, U unflag, 6–9 red / yellow / green / blue labels** (works on multiple selections). "Bursts / similar photos" groups by time gap plus a perceptual fingerprint and marks the sharpest as best; the rest can be moved to 「待确认_相似」 in one click. Buttons: **Export & share**, **Write XMP** (ratings / labels, optionally AI keywords), **Batch rename**, **Clean up RAW**, **Find duplicates**, Clear marks. With a selection only the selection is processed, otherwise the current list (duplicate search always covers the whole library). Videos show ▶ and their duration; double-click or Enter plays them in the system player. |
| **06 Trips & map** 「旅行与地图」 | Set one or more home locations at the top left (e.g. `Boca Raton, US`), or detect them from photos. Photos within 50 km of home are not trips. Away photos are split into trips by time continuity: a gap over 36 hours starts a new trip, relaxed to 72 hours while abroad; adjustable. Photos without a place that fall within a trip are included. Double-click a trip to rename it; "Sort by trip…" jumps to the Partition page with a `trip / day N` layout; "Export album…" makes a web album. The map clusters photos by location (number = count); click a cluster to see its photos. Selecting a trip draws its route. Base map: OpenStreetMap (dimmed in the dark theme), AMap (with automatic China coordinate correction), or none. Viewed areas are cached for offline use. |
| **07 AI search** 「AI 搜图」 | First click Download next to both models (Chinese CLIP ≈ 190 MB, faces ≈ 39 MB; a mirror can be chosen), then "Analyze all photos". New photos are analyzed after each scan. Type a Chinese description in the search box; results are ranked by relevance. Select one photo and click "Find similar". Recognized scenes are listed on the left, and the Partition page gains a Scene condition. |
| **08 People** 「人物」 | Faces of the same person are grouped (avatar + count on the left). Type a name and click 命名 (Name). Giving another group the same name merges them. Select wrongly assigned photos and click "Not this person" so they are never assigned again. The Partition page gains a Person condition: the largest named face decides; unnamed faces go to 「其他人」 ("others"). |
| **09 Sharpness** 「清晰度检测」 | Drag the threshold to see in real time which photos count as blurry (red scores), and move them to 「待确认_模糊」 in one click. |
| **10 Operation log** 「操作记录」 | Every move / copy / XMP write / delete-to-Recycle-Bin / rename in full. Select a batch and click "Undo this batch" to restore: moved back, `.xmp` binned or restored from backup, files returned from the Recycle Bin, names reverted. |
| **11 Settings** 「系统设置」 | Theme, ExifTool, file-name date rules (testable), GPS datum, inferring places by time for photos without GPS, tone thresholds, plugins, cache. |

**Template examples.** `/` separates levels; `{市|省|未知地点}` means try city, then province, then "unknown place":

| Template | Result |
|---|---|
| `{年}/{年}-{月}` | `2026/2026-10` |
| `{年}/{年}-{月} {市\|省\|未知地点}` | `2026/2026-10 邵阳市` |
| `{国家}/{省}/{市}` | `中国/湖南省/邵阳市` |
| `{相机}/{年月}/{类型}` | `Sony ILCE-7M4/2025-10/RAW+JPG` |
| `{年}/{冷暖}/{主色}` | `2025/暖色调/橙` |

Placeholders are in Chinese: 年 year, 月 month, 年月 year-month, 市 city, 省 province, 国家 country, 相机 camera, 类型 type, 冷暖 warm/cool, 主色 dominant color.

**Import wizard** (Sources → Import card…):
1. Inserted memory cards and disks with a `DCIM` folder are listed automatically. For a phone, connect it and pick its `DCIM` folder.
2. **Read** only reads capture times, which takes seconds. It then shows how many photos and videos are new and how many were already imported.
3. Choose the destination and a folder rule such as `{年}/{年月日}` (same syntax as partition templates), optionally with renaming.
4. Preview the list, then import.

The card is only read, never modified. Anything imported before is skipped automatically: it's in the import record `imports.db`, or the same file is already in the library. So inserting the same card again never creates duplicates. Sony videos (`PRIVATE\M4ROOT\CLIP`) are imported with their `.XML` files. Imports are logged and can be undone.

**Photo editor** (Culling → Edit…, **E** in the viewer, or right-click a thumbnail). Tools and shortcuts follow Photoshop where possible; press F1 for the full list. The first time you open it, a **guided tour** points out the toolbar, options bar, properties, layers, history and saving; replay it from Help → 新手教程 (Beginner tutorial). An illustrated tutorial (in Chinese) is in **[docs/修图新手教程.md](docs/修图新手教程.md)**.

| To… | Do this |
|---|---|
| Adjust color (editable later) | Image → Adjust, or **◐ 调整** in the Layers panel: this creates an adjustment layer. Drag exposure, curves, HSL… in Properties on the right. 「套用预设」 at the top applies a preset such as film, Japanese airy or cinematic. Adjustment layers never change pixels: edit, hide or fade them at any time. |
| Adjust only part of the photo | Make a selection first (marquee M, lasso L, magic wand W, or object select Q around the subject), then create the adjustment layer; the selection becomes its mask. Or paint on the mask with a brush or gradient (white shows, black hides). |
| Remove spots, dust, wires | Paint over them with the spot healing brush J. For larger objects, select them and use Edit → Content-Aware Fill (Shift+Backspace). The clone stamp S (Alt-click to sample) suits regular textures. |
| Cut out / composite | Object select Q around the subject → Ctrl+J copies it to a new layer. Add another image with File → Place, or paste with Ctrl+V. Then use Ctrl+T to scale and rotate, and change the blend mode. |
| Crop and straighten | Crop C: pick a ratio and drag the box. Hold Ctrl and drag along the horizon to straighten automatically. Press Enter to apply. |
| Add text | Text T: click to place text. Font, size, color, stroke and shadow can all be changed later. |
| Save | **Export image** (Ctrl+Shift+S) flattens to JPG / PNG / TIFF / WebP, saved by default next to the original as 「原名_修图.jpg」. Capture time and other EXIF are kept (GPS can be removed), and exporting into a library folder triggers a rescan. **Save project** (Ctrl+S) keeps all layers as `<name>.pcedit`; next time you edit that photo you're asked whether to continue it. |

On large images the view is computed from scaled-down proxies. While you drag a slider a low-resolution preview appears first, and a sharp frame follows when you stop. Hold `\` to see the original (background layer only).

**Find duplicates** (Culling → Find duplicates…): candidates are filtered by file size, then compared by content hash; only exact matches count. Duplicates are handled per photo group: RAW+JPG is preferred over a lone JPG, and by default the copy in the folder listed first in Sources is kept. A copy that carries edit records (`.xmp`) the original lacks is not deleted.

**Export & share** (Culling → Export & share… / Trips & map → Export album…):
- **JPG for sharing:** long edge 1080 / 1440 / 2048 / 3840 px or original size (small images are never upscaled), quality, text watermark (position and opacity), GPS removed by default (or all EXIF), optional renaming by template, with a live preview. RAW-only photos use the camera's embedded full-size preview.
- **Web album:** a folder containing `index.html`, `photos\` and `thumbs\`. It's grouped by day, with click-to-zoom and keyboard navigation. When locations exist it shows a map and route; coordinates are rounded to about 1 km, and the map needs internet but the photos work offline. Send the folder as is or put it on a cloud drive.

Exports only write new files; originals are never touched.

## 4. Safety design (why moving is safe)

1. **Preview first, confirm second:** a full list is always shown before anything runs.
2. **Journal before moving:** every operation is recorded in `%APPDATA%\PhotoCrate\journal.db` and can be undone at any time.
3. **Move as a group:** RAW+JPG, `.xmp` edit records, iPhone `.aae` edits, Live Photo `.mov` and Google Takeout `.json` travel with the photo. If any file in a group fails, the whole group is rolled back.
4. **Never overwrite:** name collisions get a number (`IMG_0001 (1).JPG`, the same number for the whole group so pairs stay together), or can be skipped.
5. **Across drives:** copy to a temp file → verify size (optionally SHA-256) → rename → only then delete the source.
6. **Interruptions:** after a crash or power loss, the next launch offers to finish or roll everything back.
7. **Deletes go to the Recycle Bin only:** RAW cleanup, undoing a copy and undoing a new `.xmp` all use the system Recycle Bin. Undoing a cleanup in the operation log puts files back from the Recycle Bin, as long as it hasn't been emptied.
8. **Back up before editing `.xmp`:** when updating an existing `.xmp` (e.g. a Lightroom edit record), only a few fields such as rating and place are changed. A full backup is taken first and restored on undo.

## 5. FAQ

- **RAW dates / lenses are missing?** Check that EXIFTOOL in the top-left shows a green version number. If it says OFFLINE, run `tools\get_exiftool.ps1` (right-click → Run with PowerShell) or point Settings to `exiftool.exe`.
- **Foreign place names show in English?** Country names and common states / counties / cities are translated to Chinese; others stay in English. Add your own translations in `data\place_names_zh.json`.
- **Color tones sorted wrongly?** Adjust Settings → tone thresholds. The preview updates instantly without rescanning.
- **Want to re-analyze?** Settings → clear cache, then scan again.
- **Where do F12 screenshots go?** F12 on any page saves the app window, including open dialogs, as PNG to `%APPDATA%\PhotoCrate\screenshots` (portable mode: `userdata\data\screenshots`). The path is shown in the status bar.
- **RAW and JPG in two different folders — still one photo?** Yes. A RAW and a JPG with the same name, capture times within 2 seconds and the same camera model are paired into one group during the scan. This can be turned off in Settings → Files & safety; use the "cross-folder pairs" filter on the Sources page to view them.
- **No GPS in the camera — how to sort by region?** On Time & place: add a phone-photo folder as a reference (read only, never moved) and locations are matched by capture time; GPX / KML tracks work too. Otherwise assign places by day.
- **Camera clock wrong / wrong time zone?** Time & place → time correction: select days, enter the shift (or convert, e.g. +08:00 → +09:00), check which photos move to another day, then apply. It can be cleared at any time; photo files are not changed.
- **Where are manual places and time corrections stored?** In `%APPDATA%\PhotoCrate\annotations.db`, keyed by file fingerprint, so they survive moves. Clearing the cache does not delete them.
- **Where is data stored?** Settings, logs, the operation journal and manual places / time corrections are in `%APPDATA%\PhotoCrate`. Thumbnails and the feature cache are in `%LOCALAPPDATA%\PhotoCrate`. Put an empty `portable.flag` file in the program folder for portable mode (data in `userdata\`).
- **Rated photos, but Lightroom doesn't show the stars?** Ratings live in the app (`annotations.db`) until you use Culling → Write XMP, which writes them to the `.xmp` next to each RAW. In Lightroom, right-click → Metadata → Read Metadata from File; Bridge refreshes automatically. "Reject" is written as Rating = -1 (Bridge shows "Rejected"). "Pick" has no standard XMP field and stays in the app. JPGs get no `.xmp`.
- **Similar-photo groups too fine / too coarse?** On Culling, adjust "gap" (default 10 s) and "similarity" (default 12; smaller is stricter). Groups update instantly.
- **Regret a RAW cleanup?** In the operation log, select that "delete to Recycle Bin" batch → Undo this batch. Files return from the Recycle Bin and the library rescans. You can also restore them from the Windows Recycle Bin directly.
- **Does the AI upload my photos?** No. Models are downloaded once to `%APPDATA%\PhotoCrate\models` (portable: `userdata\data\models`) and run fully offline; only model downloads and map tiles need internet. You can also place the model files there by hand (file names are listed on the AI search page).
- **How long does AI analysis take?** Scenes and search use thumbnails, about 0.1 s per photo on a normal CPU. Face detection happens during scanning. Results are cached once and survive moves and renames.
- **Search results off?** Chinese CLIP is good at "scene + object + mood" (海边日落 "sunset at the beach", 下雪的神社 "snowy shrine", 红色的跑车 "red sports car") but doesn't know specific people or shop names; sentence-like descriptions work best. Scores are only comparable within one search. Only plausible results are shown: a photo clearly about a different subject (e.g. a close-up portrait when searching for 街道 "street") is dropped, so you may get fewer than "top N".
- **How does "find similar" compare?** It looks at both content (what the AI thinks is pictured) and appearance (color, brightness, composition). Bursts at the same spot in the same light rank first; similar content shot day vs. night ranks lower or is hidden. The percentage is the combined similarity; below 30% is hidden. The target photo is shown above the results, and 「大图对比」 compares them one by one in the viewer (press C for side by side).
- **How do I page in the viewer?** ← → on the keyboard, or move the mouse to the left / right edge and click the arrow.
- **People grouped wrongly?** Raise "similarity" at the bottom right of the People page to be stricter: different people mix less, but one person may split into two groups (give both the same name to merge). Lower it for the opposite. Profile faces and very small faces (under 40 px in the preview) are ignored.
- **Deer, cats, dolls or anime characters among People?** The face detector only checks whether something looks like a face. The app then asks the AI whether each face is a real human; non-human ones go to 「不是人脸」 ("not a face") at the end of the list and are excluded from grouping (requires the Chinese CLIP model). Missed one? Select photos and click 「不是人脸」 (no selection = whole group). Wrongly excluded? Select it in 「不是人脸」 and click 「是人脸（恢复）」 ("is a face, restore"). Real people on posters or billboards can be marked the same way.
- **Trips not detected / home photos counted as trips?** Set your home on Trips & map first. Photos without GPS need places filled in on Time & place (phone photos / tracks / manual); otherwise trips can only be split by time.
- **Map is blank?** Base maps need internet (OpenStreetMap can be slow in mainland China; switch to AMap). Photos need a location (GPS, track match or manual place) to appear; manually assigned places show at the city center.
- **How do I import from a phone?** Android: connect by USB in "File transfer" mode, then in the import wizard choose 选择文件夹… ("choose folder") and pick the phone's `DCIM`. iPhone: export to a folder first with the Windows Photos app or iCloud, then import from that folder (an iPhone can't be accessed as a drive).
- **Video times off by hours?** Phone and camera videos usually store UTC. The app finds the nearest photo with a known time zone and converts accordingly; hovering the thumbnail shows "time zone inferred from nearby photo". With no photo within 24 hours, the PC's time zone is used; correct it on Time & place if needed.
- **What do XMP keywords look like in Lightroom?** Plain keywords are scene names (e.g. 夜景 "night"), people and trip names. Hierarchical keywords are like 场景|城市建筑|夜景 (scene|city & architecture|night), 人物|<name> (people|name) and 旅行|2026-01-01 日本… (trip|name). Only names this app recognized or you assigned are written; existing keywords are kept and nothing is duplicated.
- **Want to re-sort differently later (date first, camera or rating later)?** Change the conditions on Partition and generate a new plan; the target can be the same folder as before. When you move into a folder outside your sources, it's added to Sources automatically, so photos don't "disappear" on the next scan. Empty folders left behind (like `2026-01-05`) are removed, but source folders themselves are kept, and photos already in place are skipped. Ratings, manual places, time corrections and AI results follow the photos, so nothing is recomputed.
- **Sorted twice and want to undo the first?** Select the first batch in the operation log → Undo. You'll be told it was moved again by batch #N; confirm, and both are undone newest to oldest. Photos return to where they were before the first sort, and removed empty folders are recreated.
- **Does editing change the original?** No. Everything in the editor happens in memory. Export image writes a new file and refuses to overwrite the original; Save project writes a `.pcedit`. A `.pcedit` with the same name as the original is treated as a sidecar and moves and renames with the photo.
- **What is a `.pcedit`?** A zip with `document.json` (layer structure, adjustment settings, text) plus one PNG per layer. Open it with any unzip tool to get individual layers.
- **Can I edit RAW?** RAW files open, but from the camera's embedded full-size JPEG preview (the same as Export & share), not a fresh RAW decode. Highlight / shadow recovery headroom is therefore the same as a JPG. For maximum RAW latitude, develop in Lightroom or similar first.
- **Is the editor fast?** For a 24-megapixel photo: about 30 ms per preview frame while dragging a slider (low resolution first), only the visible area is computed at 100% zoom, and a full export takes about 2–3 seconds. The magic wand samples the current layer by default ("sample all layers" is slower the first time).
- **Will batch rename break RAW+JPG pairs?** No. A whole group (RAW + JPG + `.xmp`) gets the same base name; only names change, not folders. Collisions get " (1)". Ratings, manual places and time corrections follow the new names.

## 6. Development & extension

- Architecture and plugin API: **[ARCHITECTURE.md](ARCHITECTURE.md)** (in Chinese).
- Example plugins: `plugins_examples\` (a file-size partition with a custom page, and an AI scene-classifier skeleton).
- Tests: `.venv\Scripts\python -m pytest tests -q`. XMP write-back cases need the environment variable `PHOTOCRATE_TEST_EXIFTOOL` pointing to `exiftool.exe`.
- UI automation: `tests\ui_timeplace.py`, `tests\ui_culling.py`, `tests\ui_tier3.py`, `tests\ui_workflow.py`, `tests\ui_repartition.py` and `tests\ui_editor.py` (the editor; its core unit tests are in `tests\test_editor.py`). Face cases need `PHOTOCRATE_TEST_MODELS` pointing to a folder with the YuNet / SFace models. The real-model "not a human face" case also needs `PHOTOCRATE_TEST_CLIP` pointing to a folder with the Chinese CLIP model.
- Generate test photos: `.venv\Scripts\python tests\make_samples.py D:\TestPhotos`

## 7. Data & third-party licenses

- ExifTool © Phil Harvey (Perl Artistic / GPL), downloaded at run time and not distributed with the source.
- Chinese administrative divisions come from GeoToolCN (code: MIT). Its boundary data is derived from DataV.GeoAtlas / AMap Open Platform and is marked "for learning and exchange only": fine for personal use, but **obtain permission before any commercial distribution**.
- Cities outside China: GeoNames (CC BY 4.0), in `data\geonames_cities1000.csv.gz`; the population field comes from geonamescache (MIT).
- AI models (not distributed with the source; downloaded on first use): a quantized ONNX build of Chinese-CLIP (OFA-Sys, MIT; converted by Xenova); face models YuNet (MIT) and SFace (Apache 2.0) from OpenCV Zoo; inference with onnxruntime (MIT). `data\clip_vocab_zh.txt` is the Chinese-CLIP vocabulary.
- Base maps: © OpenStreetMap contributors (ODbL) and © AMap, loaded online under their respective terms, for personal viewing only.
- PySide6 (LGPL), rawpy / LibRaw (MIT / LGPL), pillow-heif (BSD / LGPL), OpenCV (Apache 2.0).
