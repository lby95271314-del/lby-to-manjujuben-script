#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared parser and checks for the Markdown script format (Python 3.8+)."""
import copy
import json
import math
import re
import unicodedata
from pathlib import Path

DEFAULT_CONFIG = {
    'target_seconds': [60, 120], 'speech_chars_per_second': 4,
    'pause_seconds_per_dialogue': 0.5, 'max_scenes': 3,
    'max_spoken_chars': 300, 'body_chars_range': None,
    'budget_policy': 'warn', 'aliases': {},
    'voice_tags': {'inner': 'OS', 'remote': 'VO', 'offscreen': '画外'},
}
BLUE = set('揭示 伏笔 冲突 关系转折 承诺 兑现 铺垫 反转 收束'.split())
RED = set('虐点 爽点 悬念 压迫 期待 反差'.split())
EPISODE_RE = re.compile(r'^# 第([0-9一二三四五六七八九十百零两]+)集【([0-9]+)min([0-9]+)s】$')
SCENE_RE = re.compile(r'^## ([0-9]+)-([0-9]+) (.+)-(内|外)-(日|夜|晨|傍晚|黄昏)$')
DIALOGUE_RE = re.compile(r'^(!?)([^：:（）]+)(?:（([^）]*)）)?([：:])(.*)$')
HEADS = {'【镜头语言】': 'camera', '【场景目标】': 'goal', '【情绪】': 'emotion'}
TIME_MARKERS = {'【闪回】': ('start', 'flashback'), '【闪回结束】': ('end', 'flashback'),
                '【闪出】': ('end', 'flashback'), '【想象】': ('start', 'imagination'),
                '【想象结束】': ('end', 'imagination')}
ANALYSIS_KINDS = {'camera', 'goal', 'emotion', 'blue', 'red', 'note', 'beat', 'close', 'hook'}


def count_chars(text):
    """Count Unicode letters and numbers; exclude whitespace and punctuation."""
    return sum(unicodedata.category(ch)[0] in 'LN' for ch in text)


def chinese_number(value):
    if value.isascii() and value.isdigit():
        return int(value)
    digits = dict(zip('零一二三四五六七八九两', [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))
    result, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in ('十', '百'):
            result += (current or 1) * (10 if char == '十' else 100)
            current = 0
        else:
            raise ValueError('不支持的集号：' + value)
    return result + current


def load_config(path=None):
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        override = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(override, dict):
            raise ValueError('项目配置必须是 JSON 对象')
        unknown = set(override) - set(config)
        if unknown:
            raise ValueError('未知配置字段：' + '、'.join(sorted(unknown)))
        config.update(override)
    def number(value, name, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(name + ' 必须是有限数值')
        if value < 0 or (positive and value == 0):
            raise ValueError(name + ' 数值范围不合法')
    number(config['speech_chars_per_second'], 'speech_chars_per_second', True)
    number(config['pause_seconds_per_dialogue'], 'pause_seconds_per_dialogue')
    for name in ('max_scenes', 'max_spoken_chars'):
        value = config[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise ValueError(name + ' 必须是正整数或 null')
    for name in ('target_seconds', 'body_chars_range'):
        value = config[name]
        if value is not None:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError(name + ' 必须是长度为 2 的数组或 null')
            for entry in value:
                number(entry, name)
            if value[0] > value[1]:
                raise ValueError(name + ' 下限不能超过上限')
    if config['budget_policy'] not in ('warn', 'error'):
        raise ValueError('budget_policy 必须是 warn 或 error')
    aliases = config['aliases']
    if not isinstance(aliases, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           or not k.strip() or not v.strip() for k, v in aliases.items()):
        raise ValueError('aliases 必须是非空名字到非空标准名的映射')
    for key, value in aliases.items():
        if value in aliases and aliases[value] != value:
            raise ValueError('aliases 不允许链式映射或循环')
    voices = config['voice_tags']
    if not isinstance(voices, dict) or set(voices) != {'inner', 'remote', 'offscreen'}:
        raise ValueError('voice_tags 需要 inner、remote、offscreen 三个字段')
    if any(not isinstance(v, str) or not v.strip() or any(ch in v for ch in '，、｜（）') for v in voices.values()):
        raise ValueError('声音标签必须是无分隔符的非空文本')
    if len(set(voices.values())) != 3:
        raise ValueError('三种声音标签不能相同')
    return config


def canonical(name, config):
    return config['aliases'].get(name.strip(), name.strip())


def parse_script(text, config=None, input_mode='analysis'):
    config = load_config() if config is None else config
    if input_mode not in ('analysis', 'submission'):
        raise ValueError('input_mode 必须是 analysis 或 submission')
    issues, events, episodes = [], [], []
    episode = scene = beat = None
    timeline = None
    scene_ids, episode_ids = set(), set()

    def issue(code, message, line, severity='error', ep=None, sc=None):
        issues.append({'severity': severity, 'code': code, 'line': line,
                       'episode': ep if ep is not None else (episode['number'] if episode else None),
                       'scene': sc if sc is not None else (scene['id'] if scene else None),
                       'message': message})

    def event(kind, raw, line, payload='', **extra):
        item = {'kind': kind, 'raw': raw, 'line': line, 'payload': payload,
                'episode': episode['number'] if episode else None,
                'scene': scene['id'] if scene else None}
        item.update(extra)
        events.append(item)
        if kind in ANALYSIS_KINDS and episode:
            episode['annotation_chars'] += count_chars(payload)
        return item

    def new_beat(line, explicit=False, identifier=None, timing=None):
        nonlocal beat
        beat = {'line': line, 'explicit': explicit, 'id': identifier,
                'timing': timing, 'spoken_chars': 0, 'dialogue_lines': 0,
                'content': False, 'blue': [], 'red': [], 'close': False}
        scene['beats'].append(beat)
        return beat

    def content_beat(line):
        if beat is None or beat['blue'] or beat['red'] or beat['close']:
            return new_beat(line)
        return beat

    def finish_scene():
        if not scene:
            return
        if not scene['roles']:
            issue('missing_roles', '本场缺少非空出场人物名单', scene['line'])
        if input_mode == 'analysis' and scene['heads'] != ['camera', 'goal', 'emotion']:
            issue('scene_headers', '分析稿场头必须按顺序各写一次镜头语言、场景目标、情绪', scene['line'])
        content_beats = [b for b in scene['beats'] if b['content']]
        for b in scene['beats']:
            if not b['content']:
                if b['explicit'] or b['blue'] or b['red'] or b['close']:
                    issue('empty_beat', '节拍没有动作、对白或字幕内容', b['line'])
                continue
            if input_mode == 'analysis':
                if len(b['blue']) != 1:
                    issue('blue_pair', '内容节拍需要且只能有一条蓝注', b['line'])
                allow_close = b['close'] and b is content_beats[-1]
                if len(b['red']) != 1 and not (allow_close and not b['red']):
                    issue('red_pair', '内容节拍需要一条红注；末拍可用【收尾】明示例外', b['line'])
            if b['close'] and b is not content_beats[-1]:
                issue('close_position', '【收尾】只能用于本场最后一个内容节拍', b['line'])
        if not content_beats:
            issue('empty_scene', '本场没有正文内容', scene['line'])

    def finish_episode():
        nonlocal timeline
        if not episode:
            return
        if not episode['scenes']:
            issue('empty_episode', '本集没有场次', episode['line'])
        if timeline:
            issue('unclosed_time', '本集时间段未闭合：' + timeline[0], timeline[1])
            timeline = None
        if not episode['hook']:
            issue('missing_hook', '缺少【钩子】说明；人工核对实际正文的集尾悬念与兑现计划', episode['line'], 'warning')

    for line_no, raw_line in enumerate(text.splitlines(), 1):
        raw = raw_line.strip()
        if not raw:
            continue
        if any(ord(ch) < 32 and ch != '\t' for ch in raw):
            issue('control_character', '正文含不能写入 Word XML 的控制字符', line_no)
            continue
        match = EPISODE_RE.fullmatch(raw)
        if match:
            finish_scene()
            finish_episode()
            number = chinese_number(match[1])
            if number < 1 or number in episode_ids:
                issue('episode_number', '集号必须为不重复的正整数', line_no, ep=number)
            if episodes and number != episodes[-1]['number'] + 1:
                issue('episode_sequence', '多集稿的集号需连续递增', line_no, ep=number)
            if int(match[3]) > 59:
                issue('duration_format', '标题秒部分必须为 0–59', line_no, ep=number)
            episode_ids.add(number)
            episode = {'number': number, 'line': line_no, 'scenes': [], 'hook': False,
                       'declared_seconds': int(match[2]) * 60 + int(match[3]),
                       'body_chars': 0, 'annotation_chars': 0}
            episodes.append(episode)
            scene = beat = None
            event('episode', raw, line_no, raw[2:])
            continue
        if raw.startswith('# ') and not raw.startswith('## '):
            issue('episode_format', '集标题格式：# 第X集【XminYs】', line_no)
            continue
        match = SCENE_RE.fullmatch(raw)
        if match:
            finish_scene()
            if not episode:
                issue('scene_without_episode', '场次前需要集标题', line_no)
                continue
            if episode['hook']:
                issue('after_hook', '【钩子】后不能再增加本集场次', line_no)
            ep_number, seq = int(match[1]), int(match[2])
            identifier = '{}-{}'.format(ep_number, seq)
            scene = {'id': identifier, 'line': line_no, 'location': match[3], 'roles': {},
                     'heads': [], 'beats': [], 'seen_content': False, 'beat_ids': set(), 'roles_seen': False}
            beat = None
            if ep_number != episode['number']:
                issue('scene_episode', '场号所属集与集标题不一致', line_no)
            if seq != len(episode['scenes']) + 1:
                issue('scene_sequence', '每集场序需从 1 连续递增', line_no)
            if identifier in scene_ids:
                issue('scene_duplicate', '场号重复', line_no)
            scene_ids.add(identifier)
            episode['scenes'].append(scene)
            event('scene', raw, line_no, raw[3:])
            continue
        if raw.startswith('## '):
            issue('scene_format', '场标题需为 ## 集号-场序 地点-内/外-时段', line_no)
            continue
        if not scene:
            issue('outside_scene', '正文或标注必须位于场次内，交稿附录另存', line_no)
            continue
        if episode['hook']:
            issue('after_hook', '【钩子】说明之后不能继续写本集正文或标注', line_no)
        if raw.startswith('出场人物：'):
            if scene['roles_seen'] or scene['seen_content'] or scene['heads']:
                issue('roles_position', '出场人物应只在场头标注前出现一次', line_no)
            scene['roles_seen'] = True
            value = raw[len('出场人物：'):]
            for role in value.split('、'):
                name = re.sub(r'（仅声音）$', '', role.strip())
                if not name:
                    continue
                name = canonical(name, config)
                if name in scene['roles']:
                    issue('role_duplicate', '人物名单中角色重复：' + name, line_no)
                scene['roles'][name] = '（仅声音）' in role
            event('roles', raw, line_no, value)
            continue
        head = next((key for key in HEADS if raw.startswith(key)), None)
        if head:
            kind, value = HEADS[head], raw[len(head):].strip()
            if scene['seen_content']:
                issue('header_position', '场头分析应位于正文之前', line_no)
            if not value:
                issue('empty_header', '场头分析内容不能为空', line_no)
            scene['heads'].append(kind)
            event(kind, raw, line_no, value)
            continue
        if raw.startswith('【节拍】'):
            value = raw[len('【节拍】'):].strip()
            parts, timing = value.split('｜'), {}
            identifier = parts[0].strip()
            if not identifier or identifier in scene['beat_ids']:
                issue('beat_id', '节拍编号需非空且在本场唯一', line_no)
            scene['beat_ids'].add(identifier)
            for part in parts[1:]:
                field = re.fullmatch(r'(同步动作|独立动作|停顿)=([0-9]+(?:\.[0-9]+)?)', part.strip())
                if not field or field[1] in timing:
                    issue('beat_timing', '节拍时间项不合法或重复：' + part, line_no)
                    continue
                value_number = float(field[2])
                if not math.isfinite(value_number):
                    issue('beat_timing', '节拍时间必须是有限数值', line_no)
                else:
                    timing[field[1]] = value_number
            if set(timing) != {'同步动作', '独立动作', '停顿'}:
                issue('beat_timing', '显式节拍需要同步动作、独立动作、停顿三项秒数', line_no)
            new_beat(line_no, True, identifier, timing)
            event('beat', raw, line_no, raw[len('【节拍】'):])
            continue
        prefix = next((p for p in ('【蓝】', '【红】', '【边注】', '【钩子】') if raw.startswith(p)), None)
        if prefix:
            kind = {'【蓝】': 'blue', '【红】': 'red', '【边注】': 'note', '【钩子】': 'hook'}[prefix]
            value = raw[len(prefix):].strip()
            if not value:
                issue('empty_annotation', '标注不能为空', line_no)
            if kind in ('blue', 'red'):
                if beat is None:
                    new_beat(line_no)
                beat[kind].append(line_no)
                fields = value.split('｜', 1)
                allowed = BLUE if kind == 'blue' else RED
                if len(fields) != 2 or not fields[1].strip() or any(tag not in allowed for tag in fields[0].split('、')):
                    issue('annotation_label', '标签需在词表内且说明非空，使用 标签｜说明', line_no)
            elif kind == 'note':
                if beat is None or not beat['content']:
                    issue('note_position', '边注需对应已存在的内容节拍', line_no)
                if not 6 <= count_chars(value) <= 14:
                    issue('note_length', '边注建议 6–14 个计数单位', line_no, 'warning')
            elif kind == 'hook':
                episode['hook'] = True
            event(kind, raw, line_no, value)
            continue
        if raw == '【收尾】':
            if beat is None:
                new_beat(line_no)
            beat['close'] = True
            event('close', raw, line_no)
            continue
        if raw in TIME_MARKERS:
            operation, kind = TIME_MARKERS[raw]
            if operation == 'start':
                if timeline:
                    issue('nested_time', '时间段不得嵌套或交叉', line_no)
                else:
                    timeline = (kind, line_no)
            elif not timeline or timeline[0] != kind:
                issue('time_end', '结束标记没有对应的开始', line_no)
            else:
                timeline = None
            event('time', raw, line_no, raw)
            continue
        if raw.startswith('△'):
            value = raw[1:].strip()
            if not value:
                issue('empty_action', '动作行不能为空', line_no)
            content_beat(line_no)['content'] = True
            scene['seen_content'] = True
            episode['body_chars'] += count_chars(value)
            event('action', raw, line_no, value)
            continue
        if raw.startswith('【字幕：') and raw.endswith('】'):
            value = raw[len('【字幕：'):-1].strip()
            if not value:
                issue('empty_subtitle', '字幕内容不能为空', line_no)
            content_beat(line_no)['content'] = True
            scene['seen_content'] = True
            episode['body_chars'] += count_chars(value)
            event('subtitle', raw, line_no, value)
            continue
        match = DIALOGUE_RE.fullmatch(raw)
        if match:
            name, direction, body = match[2].strip(), match[3] or '', match[5].strip()
            actual = canonical(name, config)
            if actual not in scene['roles'] and actual != '旁白':
                issue('unknown_speaker', '说话人未列入本场人物：' + name, line_no)
            if not body:
                issue('empty_dialogue', '台词不能为空', line_no)
            voice = direction.split('，', 1)[0]
            tags = config['voice_tags']
            if voice in {'OS', 'VO', '画外'} and voice not in tags.values():
                issue('voice_convention', '该声音标签与项目配置不一致：' + voice, line_no)
            if scene['roles'].get(actual) and voice not in (tags['remote'], tags['offscreen']):
                issue('voice_only_role', '仅声音角色需要远端或现场画外标记', line_no)
            if actual == '旁白' and voice != tags['remote']:
                issue('narrator_voice', '旁白应使用项目远端/旁白声音标签', line_no)
            if match[4] == ':' or '?' in body or '!' in body:
                issue('punctuation', '对白建议使用全角冒号、问号和感叹号', line_no, 'warning')
            b = content_beat(line_no)
            b['content'] = True
            b['spoken_chars'] += count_chars(body)
            b['dialogue_lines'] += 1
            scene['seen_content'] = True
            episode['body_chars'] += count_chars(body)
            event('dialogue', raw, line_no, body, name=name, direction=direction,
                  emphasis=bool(match[1]), separator=match[4])
            continue
        issue('unknown_line', '未识别行，请转换为规定的动作、对白或标记', line_no)

    finish_scene()
    finish_episode()
    if not episodes:
        issue('missing_episode', '至少需要一个有效集标题', 1)
    summaries = []
    budget_severity = 'error' if config['budget_policy'] == 'error' else 'warning'
    for ep in episodes:
        beats = [b for s in ep['scenes'] for b in s['beats'] if b['content']]
        spoken = sum(b['spoken_chars'] for b in beats)
        estimate = 0.0
        complete = bool(beats) and all(b['explicit'] and b['timing'] is not None
                                     and set(b['timing']) == {'同步动作', '独立动作', '停顿'} for b in beats)
        for b in beats:
            timing = b['timing'] or {}
            pause = timing.get('停顿', b['dialogue_lines'] * config['pause_seconds_per_dialogue'])
            speech = b['spoken_chars'] / config['speech_chars_per_second'] + pause
            estimate += max(speech, timing.get('同步动作', 0)) + timing.get('独立动作', 0)
        summary = {'episode': ep['number'], 'scene_count': len(ep['scenes']),
                   'unique_locations': len(set(s['location'] for s in ep['scenes'])),
                   'spoken_chars': spoken, 'body_chars': ep['body_chars'],
                   'annotation_chars': ep['annotation_chars'], 'estimated_seconds': round(estimate, 2),
                   'declared_seconds': ep['declared_seconds'], 'timing_complete': complete}
        summaries.append(summary)
        def ep_issue(code, message, severity=budget_severity):
            issue(code, message, ep['line'], severity, ep['number'], '')
        if not complete:
            ep_issue('timing_incomplete', '缺少完整节拍计时；当前估算为配音下限，需补独立动作与停顿', 'warning')
        if config['max_scenes'] is not None and len(ep['scenes']) > config['max_scenes']:
            ep_issue('scene_budget', '场次数超出项目预算：{}'.format(len(ep['scenes'])))
        if config['max_spoken_chars'] is not None and spoken > config['max_spoken_chars']:
            ep_issue('spoken_budget', '配音量超出项目预算：{}'.format(spoken))
        if config['body_chars_range'] is not None and not config['body_chars_range'][0] <= ep['body_chars'] <= config['body_chars_range'][1]:
            ep_issue('body_budget', '正文量偏离项目范围：{}'.format(ep['body_chars']))
        target = config['target_seconds']
        if target and (estimate > target[1] or (complete and estimate < target[0])):
            ep_issue('duration_budget', '估算时长偏离目标范围：{:.2f} 秒'.format(estimate))
        if complete and abs(estimate - ep['declared_seconds']) > max(5, ep['declared_seconds'] * 0.15):
            ep_issue('duration_title', '标题时长与估算差距较大，请核对：{:.2f} 秒'.format(estimate))
    return {'events': events, 'episodes': episodes, 'issues': issues, 'summary': summaries,
            'ok': not any(i['severity'] == 'error' for i in issues), 'input_mode': input_mode,
            'config': config}


def report_data(result):
    return {key: result[key] for key in ('ok', 'input_mode', 'config', 'summary', 'issues')}


def render_markdown(result, mode='analysis'):
    if mode not in ('analysis', 'submission'):
        raise ValueError('mode 必须是 analysis 或 submission')
    lines = []
    for item in result['events']:
        if mode == 'submission' and item['kind'] in ANALYSIS_KINDS:
            continue
        raw = item['raw']
        if mode == 'submission' and item['kind'] == 'dialogue' and item['emphasis']:
            raw = raw[1:]
        lines.append(raw)
    return '\n\n'.join(lines) + '\n'


def print_report(result):
    for row in result['summary']:
        status = '节拍估算' if row['timing_complete'] else '配音下限'
        print('第{}集：{}场/{}地点，配音{}，正文{}，注释{}，{} {:.2f}s，标题{}s'.format(
            row['episode'], row['scene_count'], row['unique_locations'], row['spoken_chars'],
            row['body_chars'], row['annotation_chars'], status, row['estimated_seconds'], row['declared_seconds']))
    for item in result['issues']:
        print('{} [{}] 集{} 场{} 行{}：{}'.format(item['severity'].upper(), item['code'],
              item['episode'], item['scene'], item['line'], item['message']))
    print('结构校验通过' if result['ok'] else '结构校验失败')
