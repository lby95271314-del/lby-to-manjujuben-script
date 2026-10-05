#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""漫剧剧本中间稿(.md) -> 彩色标注 Word 文档(.docx)

零第三方依赖：直接用标准库 zipfile 写最小 OOXML，任何装有 Python 的机器可直接运行。
中间稿行语法见同技能 references/format-spec.md 第5节对照表。

用法:
    python build_docx.py 成稿.md -o 剧本.docx [--open]
    py build_docx.py 成稿.md --open
"""
import argparse
import os
import sys
import zipfile
from xml.sax.saxutils import escape

# 样式: (底纹填充色, 文字色); 填充为 None 表示只改文字色
STYLES = {
    "gray":   ("D9D9D9", "3F3F3F"),
    "green":  ("C6E0B4", "375623"),
    "orange": ("F8CBAD", "C55A11"),
    "blue":   ("BDD7EE", "1F4E79"),
    "red":    ("F8B8B8", "C00000"),
    "purple": (None,     "7030A0"),
}
BORDER_COLOR = "9CB8C4"
EA_FONT = "宋体"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def run_xml(text, half_pts, color=None, bold=False):
    """一个 <w:r>。half_pts 为半磅字号(11pt=22)。"""
    rpr = '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="%s"/>' % EA_FONT
    if bold:
        rpr += "<w:b/>"
    if color:
        rpr += '<w:color w:val="%s"/>' % color
    rpr += '<w:sz w:val="%d"/><w:szCs w:val="%d"/></w:rPr>' % (half_pts, half_pts)
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, escape(text))


def para_xml(runs, fill=None, align_right=False, boxed=False, after=80):
    """一个 <w:p>。runs 为 run_xml 列表。after 为段后间距( twentieths of a point )。"""
    ppr = "<w:pPr>"
    if boxed:
        ppr += '<w:pBdr>'
        for edge in ("top", "left", "bottom", "right"):
            ppr += ('<w:%s w:val="single" w:sz="4" w:space="2" w:color="%s"/>' % (edge, BORDER_COLOR))
        ppr += "</w:pBdr>"
    if fill:
        ppr += '<w:shd w:val="clear" w:color="auto" w:fill="%s"/>' % fill
    ppr += '<w:spacing w:after="%d"/>' % after
    if align_right:
        ppr += '<w:jc w:val="right"/>'
    ppr += "</w:pPr>"
    return "<w:p>%s%s</w:p>" % (ppr, "".join(runs))


def document_xml(paragraphs):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>%s</w:body></w:document>" % "".join(paragraphs))


def split_dialogue(line):
    """按第一个全角/半角冒号拆 角色名 与 台词。返回 (name, sep, body) 或 None。"""
    for i, ch in enumerate(line):
        if ch in "：:":
            return line[:i], ch, line[i + 1:]
    return None


def convert(md_path, docx_path, auto_open=False):
    with open(md_path, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()

    paras = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            paras.append(para_xml([run_xml(line[3:], 24, bold=True)]))
        elif line.startswith("# "):
            paras.append(para_xml([run_xml(line[2:], 32, bold=True)]))
        elif line.startswith("【镜头语言】"):
            paras.append(para_xml([run_xml("【镜头语言：" + line[6:] + "】", 21, STYLES["gray"][1])], fill=STYLES["gray"][0]))
        elif line.startswith("【场景目标】"):
            paras.append(para_xml([run_xml("【" + line[6:] + "】", 21, STYLES["green"][1])], fill=STYLES["green"][0]))
        elif line.startswith("【情绪】"):
            paras.append(para_xml([run_xml("【情绪：" + line[4:] + "】", 21, STYLES["orange"][1])], fill=STYLES["orange"][0]))
        elif line.startswith("【蓝】"):
            paras.append(para_xml([run_xml("【" + line[3:] + "】", 21, STYLES["blue"][1])], fill=STYLES["blue"][0]))
        elif line.startswith("【红】"):
            paras.append(para_xml([run_xml("【" + line[3:] + "】", 21, STYLES["red"][1])], fill=STYLES["red"][0]))
        elif line.startswith("【边注】"):
            paras.append(para_xml([run_xml(line[4:], 18, "595959")], align_right=True, boxed=True))
        elif line.startswith("【字幕："):
            paras.append(para_xml([run_xml(line, 22, bold=True)]))
        elif line.startswith("出场人物："):
            paras.append(para_xml([run_xml(line, 22, bold=True)]))
        elif line.startswith("!"):
            d = split_dialogue(line.lstrip("!").strip())
            if d:
                name, sep, body = d
                paras.append(para_xml([
                    run_xml(name, 22),
                    run_xml(sep, 22),
                    run_xml(body, 22, STYLES["purple"][1]),
                ]))
            else:
                paras.append(para_xml([run_xml(line.lstrip("!").strip(), 22, STYLES["purple"][1])]))
        else:  # △动作行 / 普通对白
            paras.append(para_xml([run_xml(line, 22)]))

    doc = document_xml(paras)
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", doc)

    print("已生成:", os.path.abspath(docx_path))
    if auto_open and sys.platform == "win32":
        os.startfile(os.path.abspath(docx_path))


def main():
    ap = argparse.ArgumentParser(description="漫剧剧本中间稿 -> 彩色Word（零依赖）")
    ap.add_argument("input", help="中间稿 .md 路径")
    ap.add_argument("-o", "--output", help="输出 .docx 路径, 默认同名替换扩展名")
    ap.add_argument("--open", action="store_true", help="生成后用系统默认程序打开")
    args = ap.parse_args()
    out = args.output or (os.path.splitext(args.input)[0] + ".docx")
    convert(args.input, out, args.open)


if __name__ == "__main__":
    main()
