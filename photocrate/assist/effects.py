"""调色词典：把“亮一点、天空更蓝、太黄了、要日系”这类描述变成调整图层的参数。

三类词条：
- 轴 Axis：一个可以往两边调的方向（亮 ↔ 暗、暖 ↔ 冷、通透 ↔ 朦胧…），up / down 是强度 1 时的参数增量。
  词分四种：形容词（“亮”，前面有“太 / 不要 / 别 / 偏”就反过来）、固定词（“死黑”“灰蒙蒙”，说的是问题，方向固定）、
  名词（“颗粒”“高光”，方向看前后的动词：加 / 去掉 / 太多 / 高一点…，没有动词时用默认方向）。
- 风格 Style：一整套参数（日系、胶片、电影感…），大部分直接用 edit/adjust.PRESETS，和「滤镜预设」菜单一致。
- 做法 Recipe：不是滑块能完成的（背景虚化、磨皮、去路人…），给出步骤和相关功能。

参数键：滑块用 adjust.SLIDERS 的键；HSL 写成 "hsl:蓝色键"，值是 (色相, 饱和度, 明亮度) 的增量。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..edit import adjust


@dataclass(frozen=True, eq=False)
class Axis:
    id: str
    up_name: str                    # “更亮”
    down_name: str                  # “更暗”
    up: dict
    down: dict
    why_up: str
    why_down: str
    adj_up: tuple = ()
    adj_down: tuple = ()
    fix_up: tuple = ()
    fix_down: tuple = ()
    noun: tuple = ()                # 名词
    noun_sign: int = 1              # “多一点 X” 是 up 还是 down（“多一点高光”是 down：这条轴的 up 是压高光）
    noun_default: int = 1           # 只说了名词、没说加还是减时，当作加（1）还是减（-1）


@dataclass(frozen=True, eq=False)
class Style:
    id: str
    name: str
    words: tuple
    params: dict                    # 完整参数（adjust 的格式）
    why: str
    preset: str = ""                # 在「滤镜预设」菜单里的名字


@dataclass(frozen=True, eq=False)
class Recipe:
    id: str
    name: str
    words: tuple
    steps: tuple
    features: tuple = ()
    params: dict = field(default_factory=dict)


def _w(s: str) -> tuple:
    return tuple(s.split())


AXES: list[Axis] = [
    Axis("bright", "更亮", "更暗", {"exposure": 0.45, "shadows": 12}, {"exposure": -0.45, "highlights": -12},
         "整体提亮，阴影多提一点免得暗部发闷", "整体压暗，高光多压一点保住亮部细节",
         adj_up=_w("亮 明亮 亮堂 敞亮 明快 亮一些 透亮些"), adj_down=_w("暗 暗沉 昏暗 深沉 黑一点"),
         fix_up=_w("提亮 调亮 欠曝 太黑 黑乎乎 看不清 曝光不足 偏暗"), fix_down=_w("压暗 调暗 过亮 曝光过度 偏亮"),
         noun=_w("亮度 曝光 曝光度 明度")),
    Axis("contrast", "对比更强", "对比更弱", {"contrast": 25}, {"contrast": -25},
         "加大明暗反差，画面更有力", "减小明暗反差，画面更柔和平缓",
         adj_up=_w("对比强 反差大 高对比 硬朗 有力量 有冲击力"), adj_down=_w("对比弱 反差小 低对比 平缓"),
         fix_up=_w("没层次 没有层次 平淡 太平 发闷 闷"), noun=_w("对比 对比度 反差 层次 层次感 立体感")),
    Axis("highlights", "压高光", "提高光", {"highlights": -40, "whites": -10}, {"highlights": 25},
         "压暗最亮的部分，找回天空、白衣服的细节", "提亮亮部，让高光更闪",
         fix_up=_w("过曝 曝过了 天空发白 天空过曝 天空太亮 天空太白 天发白 死白 高光溢出 压高光 找回高光 亮部细节 高光细节 白得没细节"),
         noun=_w("高光 亮部 亮处 最亮的地方"), noun_sign=-1, noun_default=-1),
    Axis("shadows", "提暗部", "压暗部", {"shadows": 30, "blacks": 8}, {"shadows": -25, "blacks": -8},
         "提亮暗部，看清阴影里的细节", "压暗阴影，画面更沉稳、更有重量",
         fix_up=_w("死黑 暗部细节 阴影细节 提亮暗部 提亮阴影 阴影提亮 暗部提亮 提阴影 逆光人脸黑 脸太黑 暗部看不清"),
         fix_down=_w("压暗阴影 压暗暗部 暗部压暗 阴影压暗"),
         noun=_w("阴影 暗部 暗处 暗的地方 黑色部分"), noun_default=1),
    Axis("warm", "更暖", "更冷", {"temp": 22}, {"temp": -22},
         "色温往黄调，画面更温暖", "色温往蓝调，画面更清冷",
         adj_up=_w("暖 温暖 暖色 暖调 暖和 暖黄 黄 金黄 温馨"), adj_down=_w("冷 冷色 冷调 清冷 冷淡 蓝调 蓝 冷峻"),
         noun=_w("色温 冷暖")),
    Axis("tint", "减绿（往洋红）", "减洋红（往绿）", {"tint": 14}, {"tint": -14},
         "色调往洋红推，抵消发绿", "色调往绿推，抵消发紫发粉",
         adj_up=_w("品红 洋红 紫红 粉 粉嫩"), adj_down=_w("绿 青绿")),
    Axis("sat", "更鲜艳", "更淡雅", {"vibrance": 25, "saturation": 8}, {"saturation": -25, "vibrance": -10},
         "自然饱和度为主：先加不够鲜艳的颜色，肤色不容易过头", "降低饱和度，颜色更素雅、更高级",
         adj_up=_w("鲜艳 艳丽 浓郁 饱满 鲜亮 鲜 艳 浓 色彩丰富 色彩鲜明 有色彩"),
         adj_down=_w("淡雅 素 素雅 清淡 低饱和 淡 柔淡"),
         fix_up=_w("寡淡 颜色淡 颜色太淡 没颜色 褪色严重 颜色灰"), fix_down=_w("颜色太艳 太艳了 艳俗 太俗 刺眼 太鲜艳"),
         noun=_w("饱和度 饱和 色彩 颜色饱和度")),
    Axis("clarity", "更清晰", "更柔和", {"clarity": 22, "sharpen": 18}, {"clarity": -25},
         "加清晰度和锐化，纹理、轮廓更分明", "降低清晰度，画面更柔和、皮肤更光滑",
         adj_up=_w("清晰 锐利 清楚 犀利 锐 利落 有质感"), adj_down=_w("柔 柔和 柔美 柔焦 软"),
         fix_up=_w("不清楚 不够清晰 糊 发虚 软绵绵 没质感"), noun=_w("清晰度 锐度 锐化 质感 细节 纹理")),
    Axis("clear", "更通透", "更朦胧", {"contrast": 12, "clarity": 18, "blacks": -15, "vibrance": 10},
         {"fade": 22, "clarity": -15, "contrast": -10},
         "压黑色色阶 + 清晰度 + 对比，去掉灰雾感", "抬黑场、降清晰度，做出空气感、朦胧感",
         adj_up=_w("通透 清透 透亮 干净 透 清爽"), adj_down=_w("朦胧 雾感 空气感 柔光感 梦幻 仙气 朦胧感"),
         fix_up=_w("灰蒙蒙 发灰 太灰 很灰 灰了 雾蒙蒙 雾霾 去雾 不通透 脏 灰灰的 蒙了一层"), noun=_w("雾 薄雾 雾气"),
         noun_sign=-1, noun_default=-1),
    Axis("fade", "哑光褪色", "黑位更实", {"fade": 30, "contrast": -6}, {"blacks": -15, "contrast": 8},
         "抬起黑场（褪色），做胶片 / 哑光质感", "黑色压实，画面更扎实",
         adj_up=_w("哑光 褪色 灰调 雾面 发旧 matte"), fix_down=_w("黑不够黑 黑位不实 黑色发灰"),
         noun=_w("褪色感 胶片灰")),
    Axis("vignette", "加暗角", "去暗角", {"vignette": -25}, {"vignette": 20},
         "压暗四周，视线集中到中间的主体", "提亮四周，抵消镜头暗角",
         fix_up=_w("四周压暗 边缘压暗 四周暗一点 边角压暗"), fix_down=_w("四周发暗 四角发黑 边角发暗 四周太暗 四角太暗"),
         noun=_w("暗角 晕影"), noun_default=1),
    Axis("grain", "加颗粒", "去颗粒", {"grain": 25}, {"denoise": 25},
         "加细颗粒，像胶片的质感", "降噪，画面更干净",
         noun=_w("颗粒 颗粒感 胶片颗粒")),
    Axis("noise", "降噪", "加噪点", {"denoise": 35}, {"grain": 25},
         "降噪去掉暗处的彩色杂点（太高会糊，30–50 之间看效果）", "加细颗粒，像胶片的质感",
         fix_up=_w("降噪 去噪 去噪点 噪点太多 噪点多 太多噪点 杂点 脏点多"),
         noun=_w("噪点 杂色 噪声 噪点感"), noun_sign=-1, noun_default=-1),
    Axis("sky", "天空更蓝", "天空更淡", {"hsl:blue": (0, 25, -18), "hsl:aqua": (0, 10, -8), "highlights": -10},
         {"hsl:blue": (0, -25, 10)},
         "只加深蓝色、青色：天空更蓝更沉，其他颜色不受影响", "降低蓝色饱和度，天空更淡",
         fix_up=_w("天空更蓝 天更蓝 蓝天 天空湛蓝 天空蓝一点 天空蓝些 天空颜色 天空不够蓝 海更蓝 大海更蓝 湖更蓝"),
         fix_down=_w("天空太蓝 天太蓝 蓝得假")),
    Axis("green", "绿色更鲜", "绿色更淡", {"hsl:green": (0, 22, 0), "hsl:yellow": (0, 8, 0)},
         {"hsl:green": (-15, -30, 5), "hsl:yellow": (0, -10, 0)},
         "只加绿色、黄色饱和度，植物更生机", "降低绿色饱和度并往黄偏一点，植物不那么刺眼",
         fix_up=_w("草地更绿 树更绿 树叶更绿 植物更绿 绿植更绿 更绿 绿意 生机 春天"),
         fix_down=_w("绿色太艳 绿得刺眼 太绿了 绿得假 草太绿 树太绿")),
    Axis("skin", "肤色更白皙", "肤色更健康", {"hsl:orange": (0, -12, 15), "hsl:red": (0, -5, 5)},
         {"hsl:orange": (0, 12, -10)},
         "只调橙色（肤色所在）：降一点饱和度、提明亮度，皮肤更白净", "橙色加饱和、压明亮度，小麦色更健康",
         fix_up=_w("白皙 美白 皮肤白 肤色白 皮肤通透 肤色通透 脸白一点 皮肤亮 肤色亮 提亮肤色 提亮皮肤 皮肤白一点"),
         fix_down=_w("小麦色 古铜色 健康肤色 晒黑")),
    Axis("skin_yellow", "肤色去黄", "肤色更暖", {"hsl:orange": (-12, -15, 8), "hsl:yellow": (0, -15, 0)},
         {"hsl:orange": (8, 10, 0)},
         "橙色往红偏一点、降饱和，去掉蜡黄", "橙色加饱和，肤色更暖",
         fix_up=_w("肤色发黄 肤色太黄 皮肤发黄 皮肤黄 脸色发黄 脸黄 脸太黄 蜡黄 皮肤太黄 脸发黄")),
    Axis("rosy", "气色更红润", "肤色去红", {"hsl:red": (0, 10, 0), "hsl:orange": (5, 5, 5)}, {"hsl:red": (8, -18, 5)},
         "红色加一点饱和，气色更好", "降低红色饱和度，去掉脸上泛红",
         fix_up=_w("红润 气色 气色好 好气色 有血色"), fix_down=_w("脸太红 脸红 泛红 皮肤发红 红血丝")),
]


def _p(**kw) -> dict:
    p = adjust.default_params()
    for k, v in kw.items():
        if k == "curve_rgb":
            p["curve"]["rgb"] = v
        elif k == "hsl":
            p["hsl"].update({b: list(x) for b, x in v.items()})
        else:
            p[k] = float(v)
    return p


def _preset(name: str, words: str, why: str) -> Style:
    return Style("preset_" + name, name, _w(words), adjust.preset(name), why, name)


STYLES: list[Style] = [
    _preset("鲜明", "鲜明 鲜明风格", "对比 + 自然饱和度 + 一点清晰度，颜色干脆"),
    _preset("清新", "清新 小清新 清爽风", "略提亮、压高光提阴影、偏冷一点、饱和度收一点，干净明快"),
    _preset("日系", "日系 日式 日杂 日系风 日系感 小森林", "整体提亮、低对比、偏青、低饱和，曲线抬黑场再加褪色"),
    _preset("暖阳", "暖阳 阳光感 午后阳光 暖阳风 阳光", "色温偏暖，高光染金色"),
    _preset("冷调", "冷调 冷淡风 性冷淡 冷色调", "色温偏蓝、饱和度略降，阴影染一点蓝"),
    _preset("胶片", "胶片 胶片感 胶片风 菲林 film 富士 柯达 胶卷", "低饱和 + 褪色 + 颗粒，高光偏暖、阴影偏青，曲线压软两端"),
    _preset("复古", "复古 怀旧 老照片 年代感 旧照片", "偏暖、大幅降饱和、褪色、暗角和颗粒"),
    _preset("电影感", "电影感 电影 电影色调 大片 大片感 青橙 cinematic", "高光染橙、阴影染青（青橙对比），压高光加暗角"),
    _preset("黑白", "黑白 黑白照 黑白照片 灰度", "去掉所有颜色，再加点对比和清晰度免得发灰"),
    _preset("高对比黑白", "高对比黑白 硬朗黑白 纪实黑白 街拍黑白 森山大道", "黑白 + 大对比 + 压黑 + 暗角颗粒，硬朗纪实"),
    _preset("夜景", "夜景 夜晚 夜拍 城市夜景 夜色", "压高光（灯光不过曝）、提阴影、偏冷、降噪"),
    _preset("美食", "美食 食物 吃的 好吃 食欲 诱人", "偏暖、加自然饱和度和锐化，食物看着更有食欲"),
    _preset("人像柔肤", "人像 柔肤 人像柔和 人像风格", "降清晰度柔肤、压高光，橙色提亮降饱和让肤色干净"),
    _preset("风光", "风光 风景 山水 自然风光 风景照", "清晰度 + 自然饱和度、压高光提阴影，蓝绿色更浓"),
    Style("hk", "港风", _w("港风 港味 港式 王家卫 港片"),
          _p(contrast=15, temp=10, tint=6, saturation=10, fade=12, vignette=-18, grain=15, sp_hi_hue=35, sp_hi_sat=22,
             sp_sh_hue=170, sp_sh_sat=28, hsl={"green": [-20, -10, -5], "red": [0, 10, -5]}),
          "高光偏暖、阴影偏青绿，偏红的浓郁色彩 + 暗角颗粒"),
    Style("cyber", "赛博朋克", _w("赛博朋克 赛博 cyberpunk 霓虹 霓虹灯"),
          _p(contrast=20, temp=-28, tint=28, clarity=15, vibrance=20, sp_hi_hue=300, sp_hi_sat=30, sp_sh_hue=200,
             sp_sh_sat=35, highlights=-20),
          "整体推向青蓝和洋红，高光染紫、阴影染青，夜景霓虹最合适"),
    Style("morandi", "莫兰迪", _w("莫兰迪 莫兰迪色 高级灰 灰调高级"),
          _p(exposure=0.1, contrast=-15, saturation=-35, fade=20, highlights=-10),
          "大幅降饱和、低对比再加褪色，颜色像蒙了一层灰"),
    Style("forest", "森系", _w("森系 森林系 文艺 清新文艺"),
          _p(exposure=0.2, contrast=-10, temp=-5, fade=15, hsl={"green": [-25, -15, 10], "yellow": [-10, -10, 5]}),
          "略提亮、低对比，绿色往黄绿偏、降饱和，安静柔和"),
    Style("golden", "黄昏 / 日落", _w("黄昏 日落 夕阳 晚霞 金色时刻 落日 傍晚 golden"),
          _p(temp=25, tint=6, highlights=-20, vibrance=15, hsl={"orange": [0, 15, 0], "red": [0, 10, 0]},
             sp_hi_hue=35, sp_hi_sat=20, vignette=-12),
          "偏暖、加强橙红，高光染金，压高光保住晚霞层次"),
    Style("snow", "雪景", _w("雪景 雪 白雪 下雪 冰雪"),
          _p(exposure=0.4, whites=15, temp=-10, clarity=10, highlights=-10),
          "雪容易被相机拍成灰的：提曝光、白色色阶，偏冷一点更干净"),
    Style("stars", "星空", _w("星空 银河 星星 星轨"),
          _p(temp=-20, contrast=15, clarity=25, denoise=30, blacks=-10, highlights=-10, vibrance=10),
          "偏冷、加清晰度让星点更明显，降噪去掉高感光的杂点"),
    Style("autumn", "秋色", _w("秋天 秋色 金秋 秋景 秋意 落叶"),
          _p(temp=12, hsl={"green": [-60, -10, 0], "yellow": [-20, 20, 0], "orange": [0, 15, 0]}),
          "绿色往黄偏、加强黄橙，偏暖，满眼秋色"),
    Style("lowkey", "暗调（低调）", _w("暗调 低调 暗黑 暗黑风 质感暗调 low key"),
          _p(exposure=-0.5, contrast=25, blacks=-20, highlights=-10, vignette=-30),
          "整体压暗、加大对比和暗角，只留主体被光照到的部分"),
    Style("highkey", "亮调（高调）", _w("亮调 高调 high key 明亮干净 奶白"),
          _p(exposure=0.6, contrast=-20, shadows=30, whites=20, fade=10),
          "大幅提亮、低对比，几乎没有深色，干净轻盈"),
]

RECIPES: list[Recipe] = [
    Recipe("bg_blur", "背景虚化", _w("背景虚化 虚化背景 背景模糊 模糊背景 大光圈 浅景深 背景糊 景深效果 焦外"),
           ("「选择 → 选择主体（自动）」选出人或主体", "「选择 → 反选」（Ctrl+Shift+I）得到背景",
            "「滤镜 → 模糊 → 镜头模糊…」，半径 15–40 看效果", "Ctrl+D 取消选择；边缘不自然时先用「选择并遮住…」修选区"),
           ("select_subject", "invert_sel", "lensblur", "select_mask")),
    Recipe("remove_bg", "去背景 / 换背景", _w("去背景 去除背景 透明背景 换背景 白底 证件照 抠图 抠出来 扣图 换个背景"),
           ("「选择 → 选择主体（自动）」", "「图层 → 添加图层蒙版」：背景变透明（随时能用画笔改蒙版）",
            "换背景：「文件 → 置入图片（作为新图层）…」，在图层面板把它拖到主体下面",
            "头发丝：「选择 → 选择并遮住…」细修边缘"),
           ("select_subject", "mask", "place", "select_mask")),
    Recipe("remove_obj", "去掉路人 / 杂物", _w("去路人 去掉路人 去杂物 去掉杂物 去掉东西 抹掉 p掉 去掉电线 去掉垃圾桶 去水印 消除"),
           ("小东西（电线、灰尘、痘痘）：污点修复画笔（J）涂一下", "大一点的：套索（L）圈住它", "按 Shift+Backspace 内容识别填充",
            "没补好的地方再用修补工具或仿制图章（S）修"),
           ("heal", "lasso", "content_fill", "clone")),
    Recipe("skin_smooth", "磨皮", _w("磨皮 美颜 皮肤光滑 皮肤细腻 皮肤平滑 皮肤好一点"),
           ("先用污点修复画笔（J）去掉痘痘和斑", "Ctrl+J 复制一层", "「滤镜 → 人像 → 皮肤平滑…」",
            "把这一层不透明度降到 50–70%，保留皮肤纹理更自然"),
           ("heal", "skin", "blend_mode")),
    Recipe("slim", "瘦脸 / 瘦身", _w("瘦脸 瘦身 变瘦 瘦腿 收下巴 大眼 眼睛大一点 脸小一点"),
           ("「滤镜 → 液化…」（Ctrl+Shift+X）", "用向前推工具从外往里轻推轮廓，画笔要比要推的地方大", "幅度小一点，避免背景直线变弯"),
           ("liquify",)),
    Recipe("sky_swap", "换天空", _w("换天空 换个天空 阴天 阴天变晴 天空没颜色 天空一片白"),
           ("「编辑 → 天空替换…」，选一张天空", "只是天空太亮：用调整图层压「高光」，或天空更蓝调 HSL 的「蓝」"),
           ("sky",)),
    Recipe("subject_pop", "突出主体", _w("突出主体 主体突出 主体不明显 突出人物 人物不突出 视线集中"),
           ("加暗角把四周压暗", "减淡工具（O）轻刷主体受光的地方，加深工具刷背景", "或者背景虚化：选择主体 → 反选 → 镜头模糊"),
           ("dodge_burn", "vignette_grain", "lensblur"), {"vignette": -25, "clarity": 10}),
    Recipe("local", "只调一部分", _w("只调 局部 只让天空 只提亮脸 只调人 部分区域 某一块 只调背景 只调天空"),
           ("先选出那部分：选择主体 / 色彩范围 / 套索", "再新建调整图层：选区会自动变成它的蒙版，调整只作用在那里",
            "蒙版上用黑色画笔擦掉不要的、白色补回来；边缘生硬就「选择 → 羽化…」"),
           ("mask", "select_subject", "color_range", "feather")),
    Recipe("straighten", "拉直 / 裁剪构图", _w("拉直 地平线歪 地平线 歪了 斜了 构图 二次构图 裁一下"),
           ("裁剪工具（C）", "按住 Ctrl 沿地平线拖一条线，自动拉直", "选项栏选比例，回车确认"), ("crop",)),
    Recipe("distortion", "校正变形 / 建筑倾斜", _w("畸变 变形 楼歪 建筑歪 广角变形 透视 梯形 鱼眼 线不直"),
           ("「滤镜 → 镜头校正…」：畸变、暗角、色差、垂直透视", "或「编辑 → 变换 → 透视」手动拉正"), ("lens", "transform")),
    Recipe("hdr_look", "亮处暗处都要有细节", _w("亮暗都有细节 逆光 大光比 hdr效果 亮的过曝暗的死黑"),
           ("单张：调整图层压「高光」-60、提「阴影」+50，再加一点清晰度", "多张包围曝光：「合并曝光（HDR）」效果最好"),
           ("studio_hdr", "merge_hdr"), {"highlights": -60, "shadows": 50, "clarity": 12, "whites": -10}),
]

# 含这些字但和调色无关的词：先占住，免得“漂亮”被当成“亮”
IGNORE = _w("漂亮 好看 月亮 亮点 亮相 暗示 点亮 黑白 高光时刻 色调分离 暗角工具 清晰度检测 模糊照片 透明 透明度 "
            "不透明度 颜色范围 色彩范围 色彩平衡 色彩空间 降噪工具 天空替换")

# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------

VOCAB: dict[str, tuple] = {}         # 词 → ("axis", Axis, 种类, 符号) / ("style", Style) / ("recipe", Recipe) / ("ignore",)
for _a in AXES:
    for _kind, _sign, _words in (("a", 1, _a.adj_up), ("a", -1, _a.adj_down), ("f", 1, _a.fix_up),
                                 ("f", -1, _a.fix_down), ("n", _a.noun_sign, _a.noun)):
        for _x in _words:
            VOCAB.setdefault(_x, ("axis", _a, _kind, _sign))
for _s in STYLES:
    for _x in _s.words:
        VOCAB.setdefault(_x, ("style", _s))
for _r in RECIPES:
    for _x in _r.words:
        VOCAB.setdefault(_x, ("recipe", _r))
for _x in IGNORE:
    VOCAB[_x] = ("ignore",)
# 黑白既是风格也常出现在别处（“黑白照”“转黑白”），风格优先
VOCAB["黑白"] = ("style", next(s for s in STYLES if s.preset == "黑白"))

AXIS_BY_ID = {a.id: a for a in AXES}
STYLE_BY_ID = {s.id: s for s in STYLES}

# 方向与强度的修饰语
_SUF_DOWN = _w("太高 太大 太强 太多 太重 太亮 太白 太浓 太艳 太深 太硬 太明显 太黄 太蓝 太绿 太红 过头 过了 过度 过曝 溢出 发白 "
               "少一点 少一些 少点 低一点 低点 小一点 小点 弱一点 弱点 淡一点 淡点 轻一点 降一降 压一压 压一下 减一减 去掉 不要")
_SUF_UP = _w("太低 太小 太弱 太少 太暗 太淡 太浅 太轻 不够 不足 不明显 多一点 多一些 多点 高一点 高点 大一点 大点 强一点 强点 "
             "重一点 浓一点 深一点 加强 提一提 拉一拉 更多 更强 更重 更明显 更浓 更高 更大")
_PRE_DOWN = _w("不要 别 不想要 不想 去掉 去除 去 减少 降低 减弱 减小 少 减 压 压低 压一压 降 拿掉 消除 没有 少点 少一点 削弱 淡化 "
               "弱化 调低 拉低 压暗 调暗")
_PRE_UP = _w("加 增加 提高 多 加强 提升 加点 加些 增强 更多 来点 来些 要 加上 添加 加一点 加一些 多点 多一点 多一些 提 拉高 调高 "
             "强化 突出 提亮 调亮")
_MILD = _w("稍微 稍稍 略微 略 有点 有些 一点 一些 微微 轻微 稍 点儿 一丢丢 淡淡 些许 少许 一丝")
_STRONG = _w("非常 特别 超级 很 极其 极 大幅 强烈 十分 更加 超 明显 狠狠 好多 很多 得多 多多 大大 再多")
_COMPLAIN_PRE = _w("太 过于 太过 有点太 有些太 偏 过分")
_NEGATE_PRE = _w("不要太 不要那么 不要 别太 别那么 别 不想太 不想 没那么 不那么 不用那么 不用太 少点")


def _ends(s: str, words) -> str:
    for w in sorted(words, key=len, reverse=True):
        if s.endswith(w):
            return w
    return ""


def _starts(s: str, words) -> str:
    for w in sorted(words, key=len, reverse=True):
        if s.startswith(w):
            return w
    return ""


def _trim_tail(s: str) -> str:
    """去掉前文末尾不影响方向的字：“降低一下 高光” → “降低”。"""
    changed = True
    while changed:
        changed = False
        for w in ("一下", "一点", "一些", "的", "点", "些", "更", "再", "得", "把", "将", " "):
            if s.endswith(w) and len(s) > len(w):
                s = s[: -len(w)]
                changed = True
    return s


@dataclass
class Hit:
    kind: str              # axis / style / recipe
    obj: object
    sign: int = 1          # axis 的方向
    scale: float = 1.0
    word: str = ""
    span: tuple = (0, 0)
    complaint: bool = False

    @property
    def label(self) -> str:
        if self.kind == "axis":
            name = self.obj.up_name if self.sign > 0 else self.obj.down_name
            if self.scale < 0.8:
                return name + "（轻一点）"
            if self.scale > 1.2:
                return name + "（多一些）"
            return name
        return self.obj.name


def _intensity(pre: str, suf: str) -> float:
    tail = pre[-4:]
    if _starts(suf, _STRONG) or any(w in tail for w in _STRONG):
        return 1.5
    if _starts(suf, _MILD) or any(w in tail for w in _MILD) or suf[:1] in ("些", "点"):
        return 0.6
    return 1.0


def parse(text: str) -> list[Hit]:
    """text 已经 normalize 过。"""
    from .text import find_terms
    hits: list[Hit] = []
    consumed: list[tuple[int, int]] = []
    for i, j, w in find_terms(text, VOCAB):
        if any(a <= i and j <= b for a, b in consumed):
            continue
        entry = VOCAB[w]
        if entry[0] == "ignore":
            continue
        if entry[0] in ("style", "recipe"):
            hits.append(Hit(entry[0], entry[1], word=w, span=(i, j)))
            continue
        _, axis, kind, sign = entry
        pre, suf = text[max(0, i - 6):i], text[j:j + 5]
        scale = _intensity(pre, suf)
        complaint = False
        d = sign
        if kind == "a":
            neg = _ends(pre, _NEGATE_PRE)
            comp = _ends(pre, _COMPLAIN_PRE)
            if comp == "偏" and _starts(suf, _MILD + ("一点", "些")):
                comp = ""                                   # “偏暖一点” 是想要，不是抱怨
            after = _starts(suf, ("过头", "过了", "过度", "太多了"))
            if neg or comp or after:
                d, complaint = -sign, True
                if after:
                    consumed.append((j, j + len(after)))
        elif kind == "n":
            s_dn, s_up = _starts(suf, _SUF_DOWN), _starts(suf, _SUF_UP)
            if s_dn or s_up:
                vd = -1 if len(s_dn) >= len(s_up) and s_dn else 1
                consumed.append((j, j + len(s_dn or s_up)))
                complaint = (s_dn or s_up).startswith(("太", "过", "不"))
            else:
                p = _trim_tail(pre)
                p_dn, p_up = _ends(p, _PRE_DOWN), _ends(p, _PRE_UP)
                vd = (-1 if len(p_dn) >= len(p_up) else 1) if (p_dn or p_up) else axis.noun_default
            d = axis.noun_sign * vd
        if complaint:
            scale *= 0.75
        hits.append(Hit("axis", axis, d, scale, w, (i, j), complaint))
    return hits


# ---------------------------------------------------------------------------
# 直接说参数：“曝光 +0.5”“色温调到 -20”“饱和度加10”
# ---------------------------------------------------------------------------

_NUM_NAMES: dict[str, str] = {}
for _k, (_label, *_r) in adjust.SLIDERS.items():
    _NUM_NAMES[_label.lower()] = _k
_NUM_NAMES.update({"曝光度": "exposure", "对比": "contrast", "色温": "temp", "色调": "tint", "清晰度": "clarity",
                   "自然饱和": "vibrance", "饱和": "saturation", "黑色": "blacks", "白色": "whites", "ev": "exposure"})
_NUM_RE = re.compile(r"(" + "|".join(sorted(map(re.escape, _NUM_NAMES), key=len, reverse=True)) + r")\s*"
                     r"(加|减|增加|减少|提高|降低|调到|调成|设为|设成|改成|改为|=|\+|-|−|到)?\s*([+\-−]?\d+(?:\.\d+)?)")


def explicit_values(text: str) -> list[tuple[str, str, float, tuple[int, int]]]:
    """[(参数键, "set" | "add", 数值, 在句子里的位置)]。“曝光 +0.5”“色温 -20” 是目标值；“曝光加 0.5”是在建议值上再加。"""
    out = []
    for m in _NUM_RE.finditer(text):
        key = _NUM_NAMES[m.group(1)]
        verb, num = m.group(2) or "", m.group(3).replace("−", "-")
        v = float(num)
        span = m.span()
        if verb in ("减", "减少", "降低"):
            out.append((key, "add", -abs(v), span))
        elif verb in ("加", "增加", "提高"):
            out.append((key, "add", abs(v), span))
        elif verb in ("-", "−"):
            out.append((key, "set", -abs(v), span))
        else:
            out.append((key, "set", v, span))
    return out
