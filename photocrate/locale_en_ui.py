"""界面简化（顶栏、导航、修图右侧面板）新增 / 改过的文字的英文。"""

EN_UI = {
    '预设': 'Preset',
    '未找到': 'not found',
    '已选': 'Selected',
    '数据：{0}': 'Data: {0}',
    # 面板右上角的计数 / 状态（以前直接写英文）
    '{0} 项': '{0} items',
    '{0} 项 · {1}': '{0} items · {1}',
    '{0} 个文件': '{0} files',
    '{0} 个文件 · {1}': '{0} files · {1}',
    '{0} 个文件 · 批次 #{1}': '{0} files · batch #{1}',
    '{0} 批': '{0} batches',
    '{0} 类': '{0} classes',
    '{0} 个文件夹': '{0} folders',
    '{0} 人 · 已命名 {1} · {2} 张人脸': '{0} people · {1} named · {2} faces',
    '{0} 项 · 阈值 {1:.0f}': '{0} items · threshold {1:.0f}',
    '{0} 项 · 场景：{1}（按把握度排序）': '{0} items · Scene: {1} (by confidence)',
    '{0} 项 · {1} · 标记已变化，切换视图刷新': '{0} items · {1} · marks changed; switch view to refresh',
    '空闲': 'Idle',
    '扫描中': 'Scanning',
    '完成 · {0:.1f} 秒': 'Done · {0:.1f}s',
    '元数据 —      像素 —': 'Metadata —      Pixels —',
    '元数据 {0}/{1}      像素 {2}/{3}': 'Metadata {0}/{1}      Pixels {2}/{3}',
    '浅色': 'Light',
    '深色': 'Dark',
    '● 已就绪   v{0}': '● Ready   v{0}',
    '● 未找到 ExifTool，使用内置读取': '● ExifTool not found; using the built-in reader',
    '表情识别模型已就绪。到「挑片」页，连拍组会自动重新检查表情。':
        'The expression model is ready. Open "Culling" and bursts are re-checked for expressions automatically.',
    '选一个照片文件夹，影匣会载入里面的照片，之后往文件夹里拷进、删掉照片也会自动同步。也可以在「资源接入」添加多个文件夹；扫描过的话，点「扫描照片库」重新载入（有缓存，很快）。':
        'Pick a photo folder: PhotoCrate loads its photos and keeps in sync when you copy photos in or delete them later. You can also add several folders in "Sources"; if you have scanned before, click "Scan library" to reload (cached, fast).',
    '最常用的调色方法：点图层面板底部的<b>调整图层按钮</b>（半黑半白的圆），或菜单「图像 → 调整」，新建一个<b>调整图层</b>，然后在这里拖<b>曝光、对比度、高光、阴影、色温、曲线、HSL</b>……<br><br>顶部的<b>「套用预设」</b>可以一键套用胶片、日系、电影感等效果，再慢慢微调。<br>调整图层不改像素，随时可以回来改、隐藏或删掉。':
        'The usual way to adjust color: click the <b>adjustment layer button</b> (the half-black, half-white circle) at the bottom of the Layers panel, or use the Image → Adjust menu, to create an <b>adjustment layer</b>, then drag <b>exposure, contrast, highlights, shadows, temperature, curves, HSL</b>… here.<br><br><b>"Apply preset"</b> at the top gives you film, Japanese airy, cinematic and other looks in one click; fine-tune from there.<br>Adjustment layers don\'t change pixels, so you can come back to edit, hide or delete them at any time.',
    '每一次修改最好放在单独的图层上，互不影响：<br>• <b>眼睛</b>：显示 / 隐藏这一层，方便对比<br>• 上方的<b>混合模式</b>和<b>不透明度</b>：控制这一层的强弱<br>• 底部的<b>蒙版按钮</b>（方框里一个圆）：只让一部分生效（在蒙版上用黑色画笔擦掉不要的地方）<br>• 双击图层可以改名，用底部的上下箭头调整上下顺序<br>• 旁边的<b>历史记录</b>标签：做过的每一步':
        "Ideally put each change on its own layer so they don't interfere:<br>• <b>Eye</b>: show / hide the layer to compare<br>• <b>Blend mode</b> and <b>opacity</b> above: control how strong the layer is<br>• The <b>mask button</b> at the bottom (a square with a circle): apply to only part of the image (paint the unwanted areas black on the mask)<br>• Double-click a layer to rename it; use the up / down arrows at the bottom to reorder<br>• The <b>History</b> tab next to it: every step you've taken",
    '<b>1. 一键调色</b>：点图层面板底部的调整图层按钮 → 右侧「套用预设」选「日系」。<br><br><b>2. 去掉一个小瑕疵</b>：在图层面板选中「背景」→ 按 J → 在瑕疵上涂一下。<br><br><b>3. 裁剪</b>：按 C → 选项栏选比例 1 : 1 → 拖动框 → 回车。<br><br>想再看一遍这个教程：<b>帮助 → 新手教程</b>。祝修图愉快！':
        '<b>1. One-click color</b>: click the adjustment layer button at the bottom of the Layers panel → choose "Japanese airy" under "Apply preset" on the right.<br><br><b>2. Remove a small blemish</b>: select "Background" in the Layers panel → press J → paint over the blemish.<br><br><b>3. Crop</b>: press C → choose ratio 1 : 1 in the options bar → drag the box → press Enter.<br><br>To see this tour again: <b>Help → Beginner Tutorial</b>. Happy editing!',
}
