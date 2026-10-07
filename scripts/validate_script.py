#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a script; exit 1 for script errors, 2 for configuration/I/O errors."""
import argparse
import json
import sys
from pathlib import Path
from script_core import load_config, parse_script, print_report, report_data


def main():
    parser = argparse.ArgumentParser(description='漫剧剧本结构校验与时长估算')
    parser.add_argument('input', help='中间稿 Markdown')
    parser.add_argument('--config', help='项目 JSON 配置')
    parser.add_argument('--input-mode', choices=['analysis', 'submission'], default='analysis')
    parser.add_argument('--json', dest='json_path', help='保存校验 JSON 报告')
    args = parser.parse_args()
    try:
        source = Path(args.input)
        protected = {source.resolve()}
        if args.config:
            protected.add(Path(args.config).resolve())
        if args.json_path and Path(args.json_path).resolve() in protected:
            raise ValueError('报告路径不能覆盖输入稿或项目配置')
        result = parse_script(source.read_text(encoding='utf-8-sig'), load_config(args.config), args.input_mode)
        print_report(result)
        if args.json_path:
            target = Path(args.json_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report_data(result), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return 0 if result['ok'] else 1
    except (OSError, ValueError) as exc:
        print('无法校验：' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
