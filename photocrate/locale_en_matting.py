"""修图 AI 抠图（对象选择 SAM、选择主体 / 去除背景 BiRefNet、选择人像 / 人像虚化 MODNet、天空分割）的英文。"""

EN_MATTING = {
    # 模型（models.py）
    'SAM 2.1 tiny（对象选择 · 点一下选出物体）': 'SAM 2.1 tiny (Object Selection · click to select)',
    'Meta SAM 2.1：Apache 2.0；ONNX 转换：onnx-community': 'Meta SAM 2.1: Apache 2.0; ONNX conversion: onnx-community',
    'BiRefNet lite（选择主体 · 去背景）': 'BiRefNet lite (Select Subject · Remove Background)',
    'BiRefNet：MIT；ONNX 转换：onnx-community': 'BiRefNet: MIT; ONNX conversion: onnx-community',
    'MODNet（人像抠图 · 发丝）': 'MODNet (portrait matting · hair)',
    'MODNet：Apache 2.0；ONNX 转换：Xenova': 'MODNet: Apache 2.0; ONNX conversion: Xenova',
    '天空分割（U²-Net · 天空替换）': 'Sky segmentation (U²-Net · Sky Replacement)',
    'Sky-Segmentation（xiongzhu666）：MIT': 'Sky-Segmentation (xiongzhu666): MIT',
    # 下载询问（ui/editor/ai_dl.py）
    '缺少 onnxruntime': 'onnxruntime is missing',
    'AI 抠图需要 onnxruntime：请重新运行 run.bat 自动安装（或 pip install onnxruntime）。':
        'AI masking needs onnxruntime: run run.bat again to install it (or pip install onnxruntime).',
    '{0}需要 <b>{1}</b>（约 {2:.0f} MB）。<br>下载到 {3}，只需下载一次，之后在本机离线运行，照片不会上传。'
    '<br><br>来源：Hugging Face（失败时自动换国内镜像）；许可：{4}。':
        '{0} needs <b>{1}</b> (about {2:.0f} MB).<br>It is downloaded once to {3} and then runs offline on this computer; '
        'photos are never uploaded.<br><br>Source: Hugging Face (falls back to a mirror in China if that fails); license: {4}.',
    '取消后不会留下下载了一半的文件。': 'Cancelling leaves no half-downloaded files behind.',
    '{0}<br><br>可以稍后再试；或手动下载后放进 {1}。': '{0}<br><br>Try again later, or download it by hand and put it in {1}.',
    # 对象选择工具
    '对象选择工具（W，Shift+W 和魔棒轮换）：点一下选中点到的东西，拖一个框选出框里的整个物体'
    '（AI 模型 SAM，第一次用时下载；不下载就用 GrabCut 框选）。Shift 加选、Alt 减选':
        'Object Selection tool (W, Shift+W cycles with the Magic Wand): click to select what you click on, or drag a box '
        'to select the whole object inside it (AI model SAM, downloaded on first use; without it, box selection uses '
        'GrabCut). Shift adds, Alt subtracts',
    '点一下选中点到的东西（衣服、头发这类局部也行）；拖框选出整个物体':
        'Click to select what you click on (parts such as clothes or hair work too); drag a box for the whole object',
    '框住一个物体，自动沿边缘选出来': 'Drag a box around an object to select it along its edges',
    '下载 AI 模型（点一下就能选）': 'Download AI model (click to select)',
    '对象选择「点一下就选中」': 'Click-to-select in Object Selection',
    '模型已就绪：点一下物体试试': 'Model ready: try clicking an object',
    '正在识别…': 'Recognizing…',
    '拖一个框住物体；下载 AI 模型后点一下就能选（工具选项栏里的按钮）':
        'Drag a box around the object; after downloading the AI model a click is enough (button in the tool options bar)',
    # 选择主体 / 选择人像 / 人像虚化 / 去除背景
    '「选择主体」用 AI 模型更准（头发、毛边都能选到）：':
        'Select Subject is more accurate with the AI model (it catches hair and fuzzy edges): it',
    '先用旧方法': 'Use the old method',
    '正在识别主体（AI，几秒钟）…': 'Recognizing the subject (AI, a few seconds)…',
    '选择人像（发丝）': 'Select People (with hair)',
    '「选择人像」': 'Select People',
    '正在识别人像…': 'Recognizing people…',
    '没有找到人：这张照片里没有明显的人像，可以试试「选择主体」':
        'No person found in this photo; try Select Subject instead',
    '选择人像': 'Select People',
    '已选出人像（头发丝是半透明的）；只想虚化背景可以直接用「滤镜 → 人像 → 人像虚化」':
        'People selected (hair is semi-transparent); to just blur the background use Filter → Portraits → Portrait Blur',
    '人像虚化…': 'Portrait Blur…',
    '人像虚化': 'Portrait Blur',
    '「人像虚化」': 'Portrait Blur',
    '没有找到人：这张照片里没有明显的人像，照片没有改动': 'No person found in this photo; nothing was changed',
    '背景模糊半径': 'Background blur radius',
    '人保持清晰，背景按这个半径虚化。不满意可以撤销换个半径。':
        'People stay sharp and the background is blurred by this radius. Undo and try another radius if needed.',
    '正在虚化背景…': 'Blurring the background…',
    '已虚化背景：可以撤销换个半径；想只调人或只调背景，用「选择 → 选择人像（发丝）」':
        'Background blurred: undo to try another radius; to adjust only the people or only the background, use '
        'Select → Select People (with hair)',
    '去除背景': 'Remove Background',
    '「去除背景」': 'Remove Background',
    '先在图层面板里选中照片所在的图层': 'First select the layer with the photo in the Layers panel',
    '这个图层已经有蒙版了：先删除或应用蒙版，再去除背景':
        'This layer already has a mask: delete or apply it first, then remove the background',
    '没有识别出主体，背景没有去掉': 'No subject found; the background was not removed',
    '已去除背景（加了图层蒙版，原图像素没动）：用黑 / 白画笔在蒙版上修边，或「应用图层蒙版」':
        'Background removed (a layer mask was added; the pixels are untouched): paint black / white on the mask to fix '
        'edges, or Apply layer mask',
    # 天空替换
    '用 AI 天空分割模型自动找出天空（云、灰天、逆光都认得），边缘按树梢、楼顶精修。':
        'Finds the sky with an AI sky segmentation model (clouds, gray skies and backlight included), then refines the '
        'edge along treetops and rooftops. ',
    '自动找出天空（亮、平滑、和画面上边相连的区域），边缘按树梢、楼顶精修。':
        'Finds the sky automatically (bright, smooth areas touching the top edge), then refines the edge along treetops '
        'and rooftops. ',
    '用 AI 模型找天空（更准）': 'Find the sky with AI (more accurate)',
    '结果放在一个组里：新天空带蒙版，可以用画笔修蒙版、用移动工具挪天空。':
        'The result goes into a group: the new sky has a mask you can paint on, and the Move tool shifts the sky.',
    '「天空替换」用 AI 模型找天空：': 'Finding the sky with AI in Sky Replacement',
}
