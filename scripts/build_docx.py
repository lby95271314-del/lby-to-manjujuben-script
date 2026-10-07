#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validated Markdown -> analysis/submission Word, using Python standard library."""
import argparse
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from script_core import ANALYSIS_KINDS, load_config, parse_script, print_report, render_markdown

STYLES = {
    'camera': ('D9D9D9', '3F3F3F'), 'goal': ('C6E0B4', '375623'),
    'emotion': ('F8CBAD', '843C0C'), 'blue': ('BDD7EE', '1F4E79'),
    'red': ('F8B8B8', '9C0006'),
}
CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
DOCUMENT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''
STYLE_XML = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="312" w:lineRule="auto"/><w:widowControl/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:keepLines/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
</w:styles>'''


def run_xml(text, size=22, color=None, bold=False):
    properties = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/>'
    properties += '<w:sz w:val="{}"/><w:szCs w:val="{}"/>'.format(size, size)
    if bold:
        properties += '<w:b/>'
    if color:
        properties += '<w:color w:val="{}"/>'.format(color)
    return '<w:r><w:rPr>{}</w:rPr><w:t xml:space="preserve">{}</w:t></w:r>'.format(properties, escape(text))


def para_xml(runs, style=None, fill=None, keep_next=False, page_break=False, note=False):
    properties = []
    if style:
        properties.append('<w:pStyle w:val="{}"/>'.format(style))
    if keep_next:
        properties.append('<w:keepNext/>')
    properties.append('<w:keepLines/>')
    if page_break:
        properties.append('<w:pageBreakBefore/>')
    if note:
        border = ''.join('<w:{} w:val="single" w:sz="4" w:space="3" w:color="9CB8C4"/>'.format(edge)
                         for edge in ('top', 'left', 'bottom', 'right'))
        properties.append('<w:pBdr>' + border + '</w:pBdr>')
    if fill:
        properties.append('<w:shd w:val="clear" w:color="auto" w:fill="{}"/>'.format(fill))
    properties.append('<w:spacing w:after="80" w:line="312" w:lineRule="auto"/>')
    if note:
        properties.append('<w:ind w:left="3000"/><w:jc w:val="right"/>')
    return '<w:p><w:pPr>{}</w:pPr>{}</w:p>'.format(''.join(properties), ''.join(runs))


def document_xml(result, mode='analysis'):
    paragraphs, episode_count = [], 0
    for item in result['events']:
        kind, value = item['kind'], item['payload']
        if mode == 'submission' and kind in ANALYSIS_KINDS:
            continue
        if kind == 'episode':
            paragraphs.append(para_xml([run_xml(value, 32, bold=True)], 'Heading1', keep_next=True,
                                       page_break=episode_count > 0))
            episode_count += 1
        elif kind == 'scene':
            paragraphs.append(para_xml([run_xml(value, 24, bold=True)], 'Heading2', keep_next=True))
        elif kind == 'roles':
            paragraphs.append(para_xml([run_xml('出场人物：' + value, bold=True)], keep_next=True))
        elif kind in STYLES:
            fill, color = STYLES[kind]
            label = {'camera': '镜头语言：', 'goal': '场景目标：', 'emotion': '情绪：', 'blue': '', 'red': ''}[kind]
            paragraphs.append(para_xml([run_xml('【' + label + value + '】', 21, color)], fill=fill,
                                       keep_next=kind in ('camera', 'goal', 'emotion', 'blue')))
        elif kind == 'note':
            paragraphs.append(para_xml([run_xml(value, 18, '595959')], note=True))
        elif kind in ('beat', 'hook', 'close'):
            paragraphs.append(para_xml([run_xml(item['raw'], 18, '595959')], keep_next=kind == 'beat'))
        elif kind == 'dialogue':
            speaker = item['name'] + ('（' + item['direction'] + '）' if item['direction'] else '') + item['separator']
            color = '7030A0' if mode == 'analysis' and item['emphasis'] else None
            paragraphs.append(para_xml([run_xml(speaker), run_xml(value, color=color)]))
        elif kind in ('subtitle', 'time'):
            paragraphs.append(para_xml([run_xml(item['raw'], bold=True)], keep_next=kind == 'time'))
        else:
            paragraphs.append(para_xml([run_xml(item['raw'])]))
    section = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
               '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
               'w:header="0" w:footer="0" w:gutter="0"/></w:sectPr>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + ''.join(paragraphs) + section + '</w:body></w:document>')


def convert(md_path, docx_path, auto_open=False, config_path=None, mode='analysis',
            input_mode='analysis', markdown_path=None):
    if mode not in ('analysis', 'submission'):
        raise ValueError('mode 必须是 analysis 或 submission')
    source, output = Path(md_path), Path(docx_path)
    protected = {source.resolve()}
    if config_path:
        protected.add(Path(config_path).resolve())
    targets = [output.resolve()] + ([Path(markdown_path).resolve()] if markdown_path else [])
    if any(p in protected for p in targets) or len(set(targets)) != len(targets):
        raise ValueError('输出路径不能覆盖输入、配置或另一个输出')
    result = parse_script(source.read_text(encoding='utf-8-sig'), load_config(config_path), input_mode)
    print_report(result)
    if not result['ok']:
        raise ValueError('结构校验失败，未写出新 Word；已有输出文件未更新')
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(suffix='.docx', dir=str(output.parent))
    os.close(handle)
    try:
        with zipfile.ZipFile(temp_name, 'w', zipfile.ZIP_DEFLATED) as package:
            package.writestr('[Content_Types].xml', CONTENT_TYPES)
            package.writestr('_rels/.rels', RELS)
            package.writestr('word/_rels/document.xml.rels', DOCUMENT_RELS)
            package.writestr('word/styles.xml', STYLE_XML)
            package.writestr('word/document.xml', document_xml(result, mode))
        os.replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)
    if markdown_path:
        target = Path(markdown_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_markdown(result, mode), encoding='utf-8')
    print('已生成：' + str(output.resolve()))
    if auto_open and sys.platform == 'win32':
        os.startfile(str(output.resolve()))
    return result


def main():
    parser = argparse.ArgumentParser(description='校验漫剧剧本并导出分析稿/交稿 Word（零第三方依赖）')
    parser.add_argument('input', help='中间稿 .md')
    parser.add_argument('-o', '--output', help='输出 Word，默认同名 .docx')
    parser.add_argument('--config', help='项目 JSON 配置')
    parser.add_argument('--mode', choices=['analysis', 'submission'], default='analysis')
    parser.add_argument('--input-mode', choices=['analysis', 'submission'], default='analysis')
    parser.add_argument('--markdown', help='同时输出相应版本的 Markdown')
    parser.add_argument('--open', action='store_true', help='生成后用默认程序打开（Windows）')
    args = parser.parse_args()
    try:
        convert(args.input, args.output or str(Path(args.input).with_suffix('.docx')), args.open,
                args.config, args.mode, args.input_mode, args.markdown)
        return 0
    except (OSError, ValueError) as exc:
        print('无法导出：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
