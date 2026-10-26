"""English UI text added by the security / bug review (v2.2.1).

Kept apart from the other locale tables so they can be edited independently; i18n.set_language merges all.
"""

EN_SAFETY = {
    '文件名里有换行等控制字符，不能交给 ExifTool': 'The file name contains line breaks or other control characters and cannot be passed to ExifTool',
    'PSD 文件头损坏（尺寸 {0} × {1}）': 'The PSD header is damaged (size {0} × {1})',
    '下载的文件校验值不对（可能被镜像站或网络篡改），没有使用':
        'The downloaded file failed its checksum (the mirror or the network may have altered it); it was not used',
}
