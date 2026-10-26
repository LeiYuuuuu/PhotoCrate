"""English UI text for v2.3: eyes / expression check in culling, the Hidden album, copy / paste edits, match colors,
negative inversion, the share-time privacy check and phone transfer over Wi-Fi.

Kept apart from the other locale tables so they can be edited independently; i18n.set_language merges all.
"""

EN_EXTRAS = {
    # ---- 闭眼 / 表情 ----
    '{0} 人闭眼': '{0} with eyes closed',
    '{0} 人表情不佳': '{0} with a poor expression',
    '{0} 人没在笑（别的几张在笑）': '{0} not smiling (smiling in other shots)',
    '表情识别（MobileFaceNet · 连拍里挑表情好的）': 'Facial expression (MobileFaceNet · picks good expressions in bursts)',
    '闭眼 / 表情': 'Eyes / expression',
    '检查': 'Check',
    '看看连拍组里谁闭眼了、谁表情不好（和同一个人在同组其他几张里比），每组“最佳”会避开这些照片':
        'Find who blinked or pulled a face in each burst (compared with the same person in the other shots); '
        'the "Best" of each burst avoids these photos',
    '停止': 'Stop',
    '正在检查连拍里的闭眼 / 表情…': 'Checking bursts for closed eyes / expressions…',
    '拍摄时间相近、画面指纹相似的照片归为一组，没人闭眼、表情好又最清晰的标“最佳”。移出时跳过每组最佳、已打星和标记“留用”的照片。':
        'Photos taken close together with similar fingerprints form a group; the sharpest one where nobody blinks and '
        'expressions are good is marked "Best". Moving skips each group\'s best, rated and "Pick" photos.',
    '有连拍组时，会检查组里谁闭眼了、谁表情不好。': 'When there are bursts, they are checked for closed eyes and poor expressions.',
    '正在检查连拍里的闭眼 / 表情… {0} / {1}（可以继续挑片；随时停止，下次接着查）':
        'Checking bursts for closed eyes / expressions… {0} / {1} (keep culling; stop any time and it resumes next time)',
    '下载人脸模型后，可以检查连拍组里谁闭眼了、谁表情不好。':
        'Download the face model to check bursts for closed eyes and poor expressions.',
    '去下载…': 'Download…',
    '连拍组已检查：{0} 张有人闭眼或表情不佳，每组“最佳”已避开它们。':
        'Bursts checked: {0} photos have closed eyes or a poor expression; each burst\'s "Best" avoids them.',
    '闭眼 / 表情检查完成：看了 {0} 张': 'Eyes / expression check done: {0} photos looked at',
    '闭眼 / 表情检查出错：': 'Eyes / expression check failed: ',
    '连拍组里还有 {0} 张照片没检查闭眼 / 表情。': '{0} photos in bursts have not been checked for closed eyes / expressions.',
    '下载{0}后判断得更全。': ' Download {0} for a fuller check.',
    '中文 CLIP（判断闭眼）': 'Chinese CLIP (for closed eyes)',
    '表情识别模型': 'the expression model',
    '表情识别模型已就绪。到「05 挑片」页，连拍组会自动重新检查表情。':
        'The expression model is ready. Open "05 Culling" and bursts are re-checked for expressions automatically.',
    '和同组其他几张比：{0}': 'Compared with the rest of the burst: {0}',
    '，这一组里最好': ', the best of this group',
    '闭眼 {0}': 'Eyes {0}',
    '表情 {0}': 'Face {0}',

    # ---- 隐藏 ----
    '已隐藏': 'Hidden',
    '已锁定': 'Locked',
    '隐藏': 'Hide',
    '取消隐藏': 'Unhide',
    '隐藏照片': 'Hide photos',
    '隐藏的 {0} 张照片不会出现在图库、相簿、搜索和局域网相册里，只能在「相簿 → 已隐藏」里看到。<br><br>{1}':
        'The {0} hidden photos will not appear in the library, albums, search or the Wi-Fi album — only in '
        '"Albums → Hidden".<br><br>{1}',
    '照片文件还在原来的文件夹，没有加密。之后可以在「已隐藏」相簿里设置密码。':
        'The files stay in their folders and are not encrypted. You can set a password in the Hidden album later.',
    '隐藏只是让照片不出现在图库、相簿、搜索和局域网相册里，照片文件还在原来的文件夹，没有加密。':
        'Hiding only keeps photos out of the library, albums, search and the Wi-Fi album; the files stay in their '
        'folders and are not encrypted.',
    '已隐藏 {0} 张：在「相簿 → 已隐藏」里查看': 'Hid {0}: see "Albums → Hidden"',
    '已取消隐藏 {0} 张': 'Unhid {0}',
    '没有隐藏的照片：在照片上右键 →「隐藏」': 'No hidden photos: right-click a photo → "Hide"',
    '设置密码…': 'Set password…',
    '修改密码…': 'Change password…',
    '已隐藏的照片': 'Hidden photos',
    '密码': 'Password',
    '新密码': 'New password',
    '再输一次': 'Repeat password',
    '输入密码查看已隐藏的照片': 'Enter the password to see hidden photos',
    '忘记密码：把照片数据文件夹里 annotations.db 的 lock 表清空即可（照片不受影响）。':
        'Forgot it? Clear the lock table in annotations.db in the data folder (photos are not affected).',
    '修改密码': 'Change password',
    '给「已隐藏」设密码': 'Set a password for Hidden',
    '以后打开「已隐藏」相簿时要输入这个密码。两栏都留空 = 不用密码。':
        'This password will be asked for when opening the Hidden album. Leave both empty for no password.',
    '密码不对': 'Wrong password',
    '两次输入的不一样': 'The two entries differ',
    '打开': 'Open',
    '已设置「已隐藏」相簿的密码': 'Password set for the Hidden album',
    '「已隐藏」相簿不再需要密码': 'The Hidden album no longer needs a password',
    '更多 ▾': 'More ▾',

    # ---- 编辑 ----
    '拷贝编辑': 'Copy edits',
    '记下这些调整，之后在别的照片上右键 →「粘贴编辑」一次套到很多张（存储时也会自动记下）':
        'Remember these adjustments, then right-click other photos → "Paste edits" to apply them to many at once '
        '(saving also remembers them)',
    '还没有做任何调整，没有可拷贝的': 'No adjustments yet, nothing to copy',
    '已拷贝编辑（{0}）：在别的照片上右键 →「粘贴编辑」': 'Edits copied ({0}): right-click other photos → "Paste edits"',
    '粘贴编辑': 'Paste edits',
    '粘贴编辑（{0} 张）…': 'Paste edits ({0})…',
    '统一色调': 'Match colors',
    '统一色调（{0} 张）…': 'Match colors ({0})…',
    '统一色调 {0:.0f}%': 'match colors {0:.0f}%',
    '导出分享（{0} 张）…': 'Export & share ({0})…',
    '把「{0}」的编辑套到选中的 <b>{1}</b> 张照片上：': 'Apply the edits of "{0}" to the <b>{1}</b> selected photos:',
    '左：原图　右：粘贴后（按缩略图预览）。每张另存为新照片，原片不会被修改。':
        'Left: original   Right: pasted (thumbnail preview). Each is saved as a new photo; originals are not changed.',
    '存储 {0} 张新照片': 'Save {0} new photos',
    '存储新照片': 'Save new photos',
    '已存储 <b>{0}</b> 张新照片（原名_编辑.jpg，原片未修改）。':
        'Saved <b>{0}</b> new photos (name_edit.jpg; originals unchanged).',
    '{0}：已存储 {1} 张新照片': '{0}: saved {1} new photos',
    '至少选两张照片：一张当参考，其余的往它靠。': 'Select at least two photos: one reference, the rest are matched to it.',
    '点一张当<b>参考照片</b>，其余 {0} 张的颜色和明暗往它靠：':
        'Click one as the <b>reference</b>; the colors and tones of the other {0} are matched to it:',
    '下面是调整后的其余几张（按缩略图预览）。存成新照片后，还可以在单张编辑里接着调；这份“统一色调”也会记成拷贝的编辑，可以再粘贴到别的照片。':
        'Below: some of the others after matching (thumbnail preview). They are saved as new photos you can keep '
        'editing; the match is also kept as copied edits you can paste onto more photos.',
    '调整滑块': 'Adjustment sliders',
    '自动增强（每张按自己的明暗重新算）': 'Auto enhance (recomputed for each photo)',
    '裁剪与旋转': 'Crop and rotation',
    '负片反转': 'Invert negative',
    '底片': 'Film',
    '彩色负片': 'Color negative',
    '黑白负片': 'B&W negative',
    '用相机翻拍 / 扫描的底片转成正片：自动去掉彩色负片的橙色片基、校正偏色':
        'Turn a camera-scanned or scanned negative into a positive: removes the orange mask of color film and fixes casts',

    # ---- 隐私体检 ----
    '分享前隐私体检：找出手机号、证件号、车牌、快递单、二维码和路人的脸，确认后打码':
        'Privacy check before sharing: find phone numbers, ID numbers, plates, parcel labels, QR codes and strangers\' '
        'faces, and blur them after you confirm',
    '导出前逐张检查，在确认窗口里勾选要遮住的地方；只在导出的照片上打码，原片不变。照片里的文字需要文字识别模型（「相册」页可下载）。':
        'Each photo is checked before export; tick what to cover in the review window. Only the exported copies are '
        'redacted, never the originals. Text in photos needs the text recognition model (download on the Photos page).',
    '已取消导出': 'Export cancelled',
    '正在做隐私体检': 'Checking for privacy issues',
    '分享前隐私体检': 'Privacy check',
    '取消导出': 'Cancel export',
    '不打码，直接导出': 'Export without redacting',
    '打码并导出': 'Redact and export',
    '隐私体检出错': 'Privacy check failed',
    '在 <b>{0}</b> 张照片里找到 <b>{1}</b> 处可能泄露隐私的地方。勾选的会在导出的照片上打码（原片不变）：':
        'Found <b>{1}</b> possible privacy leaks in <b>{0}</b> photos. Ticked ones are redacted in the exported '
        'photos (originals unchanged):',
    '这张照片里：': 'In this photo:',
    '打码方式': 'Redact with',
    '灰框 = 主要人物的脸，默认不打码；需要时勾上。': 'Grey boxes are the main people\'s faces, not redacted unless you tick them.',
    '{0} 处 · 打码 {1}': '{0} found · {1} redacted',
    '预览失败': 'Preview failed',
    '隐私体检：没有发现号码、证件、车牌、二维码或路人脸': 'Privacy check: no numbers, IDs, plates, QR codes or strangers\' faces found',
    '没有下载文字识别模型，这次只检查了二维码、条形码和人脸；下载后还能找出照片里的手机号、证件号、车牌…':
        'The text recognition model is not downloaded, so only QR codes, barcodes and faces were checked; with it, '
        'phone numbers, ID numbers, plates… in photos are found too.',
    '（没有文字识别模型，只查了二维码和人脸）': ' (no text recognition model: only QR codes and faces were checked)',
    '马赛克': 'Mosaic',
    '色块': 'Solid block',
    '主要人物': 'Main subject',
    '路人': 'Stranger',
    '（疑似）': ' (possible)',
    '二维码': 'QR code',
    '条形码': 'Barcode',
    '手机号 / 电话': 'Phone number',
    '身份证号': 'ID number',
    '银行卡号': 'Bank card number',
    '车牌': 'License plate',
    '快递面单': 'Parcel label',
    '门牌地址': 'Street address',
    '邮箱': 'Email',
    '路人的脸': 'Stranger\'s face',
    '人脸': 'Face',

    # ---- 手机互传 ----
    '手机互传': 'Phone transfer',
    '手机扫码：把照片传到电脑，或在手机 / 电视上浏览相册（同一个 Wi-Fi）':
        'Scan with your phone: send photos to this PC, or browse the library on a phone / TV (same Wi-Fi)',
    '手机上传': 'From phone',
    '允许从手机上传照片 / 视频到电脑': 'Allow uploading photos / videos from the phone',
    '允许在手机 / 平板 / 电视上浏览相册（只读，隐藏的照片不显示）':
        'Allow browsing the library on a phone / tablet / TV (read-only, hidden photos excluded)',
    '<b>手机和电脑连同一个 Wi-Fi</b>，用手机相机或微信扫左边的二维码：':
        '<b>Put the phone on the same Wi-Fi as this PC</b> and scan the QR code on the left with the camera:',
    '上传到：': 'Upload to:',
    '更改…': 'Change…',
    '加入自动同步': 'Add to auto sync',
    '还没有收到文件': 'Nothing received yet',
    '停止并关闭': 'Stop and close',
    '手机上传的照片放到哪个文件夹？': 'Where should photos from the phone go?',
    '已收到 {0} 个文件：{1}': 'Received {0} files: {1}',
    '手机互传：收到 {0}': 'Phone transfer: received {0}',
    '只要这个窗口开着就能用，关掉就停止。网址里带一次性的口令，同一个 Wi-Fi 里的其他人猜不到；第一次使用时 Windows 防火墙可能会询问，请允许“专用网络”。':
        'Works while this window is open; closing it stops the service. The address carries a one-time key others on '
        'the Wi-Fi cannot guess. The first time, Windows Firewall may ask — allow "Private networks".',
    '这个文件夹在自动同步里：传上来的照片几秒后就出现在相册。':
        'This folder is auto-synced: uploaded photos appear in the library within seconds.',
    '这个文件夹还不在自动同步里，传上来的照片不会自动出现在相册。':
        'This folder is not auto-synced yet, so uploaded photos will not show up in the library by themselves.',
    '开不了网页服务：{0}': 'Could not start the web service: {0}',
    '手机互传已停止：这次收到 {0} 个文件': 'Phone transfer stopped: {0} files received',
    '影匣 · 手机互传': 'PhotoCrate · Phone transfer',
    '上传到电脑': 'Send to PC',
    '选择照片或视频': 'Choose photos or videos',
    '可以一次选很多张；传完几秒后电脑上的相册就能看到': 'Pick as many as you like; they appear on the PC seconds after uploading',
    '等待上传': 'Waiting',
    '已传到电脑': 'Sent',
    '电脑上已经有了，跳过': 'Already on the PC, skipped',
    '失败': 'Failed',
    '已上传 {0} / {1}': 'Uploaded {0} / {1}',
    '下载原图': 'Download original',
    '上传时请不要锁屏或切走浏览器': 'keep the screen on and the browser open while uploading',
    '周日': 'Sun', '周一': 'Mon', '周二': 'Tue', '周三': 'Wed', '周四': 'Thu', '周五': 'Fri', '周六': 'Sat',
}
