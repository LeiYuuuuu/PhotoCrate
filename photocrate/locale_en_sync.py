"""English UI text for auto sync (watching the source folders, v2.2).

Kept apart from locale_en.py / locale_en_photos.py so they can be edited independently; i18n.set_language merges all.
"""

EN_SYNC = {
    # 资源接入
    '自动同步': 'Auto sync',
    '开启后一直监视这些文件夹：往里面拷进、删掉或改动照片，照片库会自动跟着更新，不用重新扫描。':
        'Keep watching these folders: copy photos in, delete or change them, and the library updates by itself — no rescan needed.',
    '拷进 / 删掉照片时自动更新照片库': 'Update the library when photos are added or removed',
    # 状态
    '自动同步已关闭': 'Auto sync is off',
    '自动同步已开启，还没有文件夹': 'Auto sync is on, but there are no folders yet',
    '自动同步：正在读取 {0} 个文件夹…': 'Auto sync: reading {0} folder(s)…',
    '自动同步：正在更新照片库…': 'Auto sync: updating the library…',
    '自动同步出错：{0}': 'Auto sync failed: {0}',
    '（定时检查）': ' (checking periodically)',
    '● 自动同步中：{0} 个文件夹{1}': '● Auto sync: {0} folder(s){1}',
    '自动同步：比较文件夹…': 'Auto sync: comparing folders…',
    '自动同步：正在分析新照片…  元数据 {0}/{1}  画面 {2}/{3}':
        'Auto sync: analyzing new photos…  metadata {0}/{1}  pixels {2}/{3}',
    '自动同步：AI 识别新照片 {0}/{1}': 'Auto sync: AI on new photos {0}/{1}',
    '自动同步：{0}': 'Auto sync: {0}',
    '自动同步：': 'Auto sync: ',
    '自动同步出错：': 'Auto sync failed: ',
    '新增 {0} 张': '{0} added',
    '移除 {0} 张': '{0} removed',
    '更新 {0} 张': '{0} updated',
    '没有变化': 'no changes',
    # 相册
    '自动同步 ▾': 'Auto sync ▾',
    '自动同步：关 ▾': 'Auto sync: off ▾',
    '同步中… ▾': 'Syncing… ▾',
    '▲ 自动同步 ▾': '▲ Auto sync ▾',
    '● 自动同步 ▾': '● Auto sync ▾',
    '选一个照片文件夹：往里面拷进、删掉照片，这里自动跟着更新。':
        'Pick a photo folder: copy photos in or delete them, and this view follows automatically.',
    '选择要自动同步的照片文件夹': 'Choose a photo folder to auto sync',
    '添加要同步的文件夹…': 'Add a folder to sync…',
    '自动同步文件夹里的变化': 'Auto sync changes in the folders',
    '立即检查': 'Check now',
    '照片文件夹（自动同步已关闭）：': 'Photo folders (auto sync is off):',
    '    …还有 {0} 个': '    …and {0} more',
    '管理文件夹（资源接入）…': 'Manage folders (Sources)…',
    '正在载入照片…': 'Loading photos…',
    '选择照片文件夹…': 'Choose photo folder…',
    '选一个照片文件夹，影匣会载入里面的照片，之后往文件夹里拷进、删掉照片也会自动同步。'
    '也可以在「01 资源接入」添加多个文件夹；扫描过的话，点「扫描照片库」重新载入（有缓存，很快）。':
        'Pick a photo folder: PhotoCrate loads its photos and keeps in sync when you copy photos in or delete them later. '
        'You can also add several folders in "01 Sources"; if you have scanned before, click "Scan library" to reload (cached, fast).',
}
