"""生成面向同事的《使用手册》：同时输出 Markdown 与 Word (.docx)。

内容只有「安装 / 启动 / 使用」，不含任何技术原理。
"""
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"E:\agent\工作\抖音信息抓取")
OUT_DOCX = ROOT / "使用手册（发给同事）.docx"
OUT_MD = ROOT / "使用手册（发给同事）.md"

FONT = "微软雅黑"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
NOTE_BG = "FFF4E5"
WARN_BG = "FDECEC"
TIP_BG = "EAF3FB"

# ---------------------------------------------------------------- 内容定义
# ("h1"|"h2"|"p"|"bullet"|"num"|"note"|"warn"|"tip"|"table"|"pagebreak", 内容)
CONTENT = [
    ("h1", "抖音合集播放量监控 · 使用手册"),
    ("p", "这个工具用来查看抖音合集的播放量，以及合集里每一集的单独播放量。"
          "装在电脑上运行，每天打开点一下，就能拿到最新数据。"),

    ("h2", "一、运行环境要求"),
    ("table", [
        ["项目", "要求"],
        ["操作系统", "Windows 10 / Windows 11"],
        ["浏览器", "系统自带的 Microsoft Edge（不用另外安装）"],
        ["网络", "能正常访问抖音即可"],
        ["安装", "不需要安装，把文件夹拷过去就能用"],
    ]),

    ("h2", "二、安装（就是把文件夹拷过去）"),
    ("num", "拿到「抖音监控」文件夹（如果同事给你的是压缩包，先解压）"),
    ("num", "把整个文件夹放到自己电脑上，建议放在 D 盘，例如 D:\\抖音监控"),
    ("num", "完成。不需要安装任何软件，不需要注册，不需要登录"),
    ("warn", "一定是拷【整个文件夹】，不能只拷 抖音监控.exe。"
             "文件夹里的 _internal 目录放着运行库，缺了程序打不开。"),
    ("tip", "文件夹放哪里都可以，建议路径短一点、不带特殊符号，例如 D:\\抖音监控。"),

    ("h2", "三、启动程序"),
    ("num", "打开文件夹，双击 抖音监控.exe"),
    ("num", "屏幕上会弹出一个黑色窗口，上面写着「抖音合集播放量监控 已启动」"),
    ("num", "等 2～3 秒，浏览器会自动打开操作界面"),
    ("num", "如果浏览器没有自动打开：手动打开浏览器，"
            "在地址栏输入 127.0.0.1:8765 然后回车"),
    ("warn", "那个黑色窗口不要关闭。关掉它 = 退出程序。"
             "可以最小化，不影响你做别的事。"),

    ("h2", "四、第一次使用：把要追踪的视频加进去"),
    ("p", "第一次打开时清单是空的，需要先告诉程序「要追踪哪些视频」。"),
    ("num", "在抖音里找到目标视频 → 点「分享」→ 复制链接"),
    ("num", "回到程序界面，切到「清单」页"),
    ("num", "把链接粘进上面的大文本框。可以一次粘很多条，一行一条"),
    ("num", "（可选）在右边填个备注，比如账号名，方便以后辨认"),
    ("num", "点「导入到清单」"),
    ("num", "下面表格里会列出这些链接，并自动识别出它属于哪个合集、共几集"),
    ("tip", "重复的链接会自动跳过，不用担心中途粘重复。"),
    ("tip", "如果某条显示「无合集」，说明这个视频没有发布在合集里——"
            "这类视频的播放量平台不公开，属于正常现象。"),

    ("h2", "五、抓取数据"),
    ("num", "切到「抓取」页"),
    ("num", "点「立即抓取」大按钮"),
    ("num", "进度条开始走，下面的日志会实时滚动，每条成功或失败都写得清清楚楚"),
    ("num", "等它跑完。大约每条链接 10 秒，100 条链接约 10～15 分钟"),
    ("tip", "抓取过程中可以随时点「停止」。已经采到的数据不会丢，"
            "下次重新点「立即抓取」会补齐剩下的。"),

    ("h2", "六、查看结果"),
    ("p", "切到「结果」页，从上往下看："),
    ("bullet", "顶部四个数字：追踪的合集数、总播放量、本次总增量、停涨合集数"),
    ("bullet", "合集总览：每个合集的发布日期、总播放量、本次增量、收藏量、集数、更新至第几集"),
    ("bullet", "行首的彩色竖条是播放量等级：蓝色 = 达标（默认 2 万以上）、橙色 = 50 万以上、"
               "红色 = 100 万以上，播放量旁边还有对应的文字徽章，一眼就能看出哪些合集跑出来了"),
    ("bullet", "排序：点「总播放量」或「发布日期」的表头可以在从高到低 / 从低到高之间切换，"
               "也可以用表格上方的下拉框选。没有发布日期的合集会排在最后"),
    ("bullet", "分集明细：点开任意一个合集，看每一集的播放量、本次增量、"
               "以及这一集占整个合集的百分比"),
    ("bullet", "「导出 Excel 日报」：生成表格文件，包含「合集总览 / 分集明细 / 账号汇总」"
               "三个工作表，可以直接发给领导"),
    ("bullet", "「生成看板」：生成一个网页文件，可以直接发给同事看（对方不用装任何东西）"),
    ("tip", "导出的 Excel 和看板文件，都存放在程序文件夹的 output 目录里。"
            "在「结果」页点完按钮后会自动帮你打开。"),

    ("h2", "七、日常怎么用"),
    ("num", "打开 抖音监控.exe"),
    ("num", "界面右上角会告诉你今天跑没跑："),
    ("sub", "显示「今日已采集 ✓」—— 今天的已经有了，直接看「结果」页"),
    ("sub", "显示「今日还没跑 ⚠」—— 到「抓取」页点一下「立即抓取」"),
    ("num", "看完把浏览器关掉即可，黑色窗口可以一直开着"),

    ("h2", "八、注意事项"),
    ("bullet", "黑色窗口不要关掉，关掉就等于退出程序"),
    ("bullet", "界面地址 127.0.0.1:8765 只能本机访问，别人的电脑打不开，这是正常的"),
    ("bullet", "同一天重复抓取会覆盖当天数据，不会重复计数（这是刻意设计，"
               "避免把半天的增长当成一整天的增长）"),
    ("bullet", "「本次增量」需要至少两天的数据才会出现。第一次抓取显示「—」是正常的，"
               "第二天再抓一次就有了"),

    ("h2", "九、常见问题"),
    ("qa", ("双击 exe 没反应，或提示缺少文件？",
            "检查是不是只拷了 exe、没拷 _internal 文件夹。"
            "另外看看杀毒软件有没有拦截，如果有就把它加入白名单。")),
    ("qa", ("浏览器没有自动打开界面？",
            "手动打开浏览器，地址栏输入 127.0.0.1:8765 回车。")),
    ("qa", ("黑色窗口里显示的地址不是 8765？",
            "说明 8765 端口被别的程序占用了，程序会自动换一个端口。"
            "以黑色窗口里显示的那个地址为准。")),
    ("qa", ("有的视频抓不到播放量？",
            "抖音只在「合集」里公开播放量。如果视频没有发布在合集里，就取不到。"
            "程序会在日志里明确标出是哪一条，其余条目不受影响。")),
    ("qa", ("数据存在哪里？怎么备份？",
            "都在程序文件夹的 data 文件夹里。备份 = 把 data 文件夹拷走。"
            "换电脑 = 把整个文件夹拷过去，data 一起带上，历史数据就还在。")),
    ("qa", ("抓取会不会导致账号被封？",
            "不会。程序不登录任何账号，只读取公开页面，每天一次、逐条抓取，频率很低。")),
    ("qa", ("怎么把结果发给别人？",
            "「结果」页点「生成看板」，会生成一个 dashboard.html 文件。"
            "直接发这个文件，对方双击就能看，不需要安装任何东西。")),
    ("qa", ("想加新视频怎么办？",
            "「清单」页粘链接 → 导入即可。第二天抓取时会自动带上。")),
    ("qa", ("某个视频不想追踪了？",
            "「清单」页找到那一条，点「停用」。历史数据会保留，不会删除。")),

    ("h2", "十、一分钟速查表"),
    ("table", [
        ["我想……", "怎么做"],
        ["启动程序", "双击 抖音监控.exe"],
        ["打开界面", "浏览器访问 127.0.0.1:8765"],
        ["添加新视频", "「清单」页 → 粘贴链接 → 导入到清单"],
        ["立刻抓一次", "「抓取」页 → 立即抓取"],
        ["看数据", "「结果」页"],
        ["导出表格", "「结果」页 → 导出 Excel 日报"],
        ["发给同事", "「结果」页 → 生成看板 → 发送 dashboard.html"],
        ["暂停追踪某条", "「清单」页 → 停用"],
        ["调整抓取速度", "「设置」页 → 链接间隔"],
        ["调整高亮标准", "「设置」页 → 播放量分级（达标 / 优秀 / 爆款三档阈值）"],
        ["备份数据", "拷走程序文件夹里的 data 文件夹"],
    ]),
]


# ---------------------------------------------------------------- Word 输出
def _set_font(run, name=FONT, size=None, bold=None, color=None):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _shade(paragraph, color):
    pPr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    pPr.append(shd)


def _left_bar(paragraph, color="1F4E79"):
    pPr = paragraph._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "24")
    left.set(qn("w:space"), "6")
    left.set(qn("w:color"), color)
    bdr.append(left)
    pPr.append(bdr)


def build_docx():
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = FONT
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.line_spacing = 1.5
    st.paragraph_format.space_after = Pt(4)

    for sec in doc.sections:
        sec.top_margin = Cm(2.2)
        sec.bottom_margin = Cm(2.2)
        sec.left_margin = Cm(2.4)
        sec.right_margin = Cm(2.4)

    # 手动编号：每个小节从 1 重新开始（Word 内置 List Number 会让全文连号）
    counter = 0

    for kind, val in CONTENT:
        if kind == "h1":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(14)
            _set_font(p.add_run(val), size=20, bold=True, color=ACCENT)
        elif kind == "h2":
            counter = 0
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(16)
            p.paragraph_format.space_after = Pt(6)
            _left_bar(p, "1F4E79")
            _set_font(p.add_run(val), size=13.5, bold=True, color=ACCENT)
        elif kind == "p":
            p = doc.add_paragraph()
            _set_font(p.add_run(val))
        elif kind == "bullet":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.75)
            p.paragraph_format.first_line_indent = Cm(-0.4)
            _set_font(p.add_run("•  " + val))
        elif kind == "num":
            counter += 1
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.85)
            p.paragraph_format.first_line_indent = Cm(-0.85)
            _set_font(p.add_run(f"{counter}.  "), bold=True)
            _set_font(p.add_run(val))
        elif kind == "sub":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(1.7)
            p.paragraph_format.first_line_indent = Cm(-0.45)
            _set_font(p.add_run("— " + val), size=10)
        elif kind in ("note", "warn", "tip"):
            bg = {"note": NOTE_BG, "warn": WARN_BG, "tip": TIP_BG}[kind]
            mark = {"note": "说明", "warn": "注意", "tip": "提示"}[kind]
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.4)
            p.paragraph_format.space_before = Pt(4)
            p.paragraph_format.space_after = Pt(6)
            _shade(p, bg)
            _set_font(p.add_run(f"【{mark}】"), bold=True,
                      color=RGBColor(0xA3, 0x2D, 0x2D) if kind == "warn"
                      else RGBColor(0x85, 0x4F, 0x0B))
            _set_font(p.add_run(val))
        elif kind == "qa":
            q, a = val
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(2)
            _set_font(p.add_run("Q：" + q), bold=True)
            p2 = doc.add_paragraph()
            p2.paragraph_format.left_indent = Cm(0.6)
            _set_font(p2.add_run("A：" + a))
        elif kind == "table":
            rows = val
            t = doc.add_table(rows=len(rows), cols=len(rows[0]))
            t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, row in enumerate(rows):
                for j, cell in enumerate(row):
                    c = t.cell(i, j)
                    c.text = ""
                    para = c.paragraphs[0]
                    para.paragraph_format.space_after = Pt(2)
                    _set_font(para.add_run(str(cell)), size=10,
                              bold=(i == 0))
                    if i == 0:
                        _shade(para, "EAF3FB")
                        for r in para.runs:
                            r.font.color.rgb = ACCENT
            doc.add_paragraph().paragraph_format.space_after = Pt(2)

    doc.save(str(OUT_DOCX))
    return OUT_DOCX


# ---------------------------------------------------------------- Markdown 输出
def build_md():
    lines = []

    def blank():
        if lines and lines[-1] != "":
            lines.append("")

    for kind, val in CONTENT:
        if kind == "h1":
            blank()
            lines += [f"# {val}", ""]
        elif kind == "h2":
            blank()
            lines += [f"## {val}", ""]
        elif kind == "p":
            lines += [val, ""]
        elif kind == "bullet":
            lines += [f"- {val}"]
        elif kind == "num":
            lines += [f"1. {val}"]
        elif kind == "sub":
            lines += [f"   - {val}"]
        elif kind in ("note", "warn", "tip"):
            mark = {"note": "说明", "warn": "注意", "tip": "提示"}[kind]
            blank()
            lines += [f"> **【{mark}】**{val}", ""]
        elif kind == "qa":
            q, a = val
            blank()
            lines += [f"**Q：{q}**", "", f"A：{a}", ""]
        elif kind == "table":
            rows = val
            blank()
            lines += ["| " + " | ".join(str(c) for c in rows[0]) + " |",
                      "|" + "---|" * len(rows[0])]
            for r in rows[1:]:
                lines += ["| " + " | ".join(str(c) for c in r) + " |"]
            lines += [""]
    blank()
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    return OUT_MD


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("Word :", build_docx())
    print("Markdown:", build_md())
