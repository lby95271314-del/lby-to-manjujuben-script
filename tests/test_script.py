"""Behavioral regressions for validation, shared text, timing and Word output."""
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from script_core import count_chars, load_config, parse_script, render_markdown
from build_docx import convert

SAMPLE = (ROOT / 'assets/sample-script.md').read_text(encoding='utf-8')
PLAIN = '# 第一集【0min10s】\n## 1-1 客厅-内-日\n出场人物：甲、乙（仅声音）\n【节拍】A｜同步动作=10｜独立动作=3｜停顿=2\n△ 甲举起纸条。\n甲（OS）：一二三四\n乙（VO，电话）：五六七八\n【钩子】来电尚未结束，下一集兑现。\n'


class ScriptTests(unittest.TestCase):
    def check_sample(self, text=SAMPLE):
        return parse_script(text, load_config())

    def codes(self, result):
        return {i['code'] for i in result['issues'] if i['severity'] == 'error'}

    def test_complete_sample_is_valid_and_timed(self):
        result = self.check_sample()
        self.assertTrue(result['ok'], result['issues'])
        self.assertFalse(result['issues'])
        row = result['summary'][0]
        self.assertTrue(row['timing_complete'])
        self.assertEqual(row['scene_count'], 3)
        self.assertEqual(row['unique_locations'], 2)
        self.assertEqual(row['spoken_chars'], 168)
        self.assertGreater(row['annotation_chars'], row['spoken_chars'])

    def test_cross_episode_scene_and_unknown_speaker_rejected(self):
        text = SAMPLE.replace('## 1-2', '## 2-9').replace('!苏晚晴：现在，分清你我了吗？', '!陌生人：现在，分清你我了吗？')
        self.assertTrue({'scene_episode', 'scene_sequence', 'unknown_speaker'} <= self.codes(self.check_sample(text)))

    def test_missing_headers_and_missing_pair_rejected(self):
        text = '\n'.join(line for line in SAMPLE.splitlines() if not line.startswith(('【场景目标】', '【红】')))
        self.assertTrue({'scene_headers', 'red_pair'} <= self.codes(self.check_sample(text)))

    def test_flashback_closed_within_episode(self):
        result = self.check_sample(SAMPLE.replace('【闪回结束】', ''))
        self.assertIn('unclosed_time', self.codes(result))
        nested = SAMPLE.replace('【闪回】', '【闪回】\n【想象】')
        self.assertIn('nested_time', self.codes(self.check_sample(nested)))

    def test_voice_only_and_narrator(self):
        text = PLAIN.replace('乙（VO，电话）', '乙').replace('甲（OS）', '旁白（OS）')
        codes = self.codes(parse_script(text, input_mode='submission'))
        self.assertTrue({'voice_only_role', 'narrator_voice'} <= codes)

    def test_timing_uses_parallel_max_and_counts_inner_remote(self):
        result = parse_script(PLAIN, input_mode='submission')
        self.assertTrue(result['ok'])
        # Eight spoken units / 4 + 2 pause = 4; parallel action 10, independent 3.
        self.assertEqual(result['summary'][0]['spoken_chars'], 8)
        self.assertEqual(result['summary'][0]['estimated_seconds'], 13)

    def test_unmeasured_script_reports_lower_bound(self):
        text = '\n'.join(l for l in PLAIN.splitlines() if not l.startswith('【节拍】'))
        result = parse_script(text, input_mode='submission')
        self.assertFalse(result['summary'][0]['timing_complete'])
        self.assertIn('timing_incomplete', {i['code'] for i in result['issues']})

    def test_closing_exception_only_at_last_beat(self):
        lines = SAMPLE.splitlines()
        last_red = max(i for i, line in enumerate(lines) if line.startswith('【红】'))
        lines[last_red] = '【收尾】'
        self.assertTrue(self.check_sample('\n'.join(lines))['ok'])
        first_red = next(i for i, line in enumerate(lines) if line.startswith('【红】'))
        lines[first_red] = '【收尾】'
        self.assertIn('close_position', self.codes(self.check_sample('\n'.join(lines))))

    def test_budget_can_be_warning_or_error(self):
        config = load_config()
        config['max_spoken_chars'] = 1
        self.assertTrue(parse_script(SAMPLE, config)['ok'])
        config['budget_policy'] = 'error'
        self.assertIn('spoken_budget', self.codes(parse_script(SAMPLE, config)))

    def test_alias_and_client_voice_convention(self):
        config = load_config()
        config['aliases'] = {'小甲': '甲'}
        config['voice_tags'] = {'inner': '心声', 'remote': 'VO', 'offscreen': 'OS'}
        text = PLAIN.replace('甲（OS）', '小甲（心声）')
        self.assertTrue(parse_script(text, config, 'submission')['ok'])
        config['voice_tags']['offscreen'] = '画外'
        self.assertIn('voice_convention', self.codes(parse_script(PLAIN, config, 'submission')))

    def test_configuration_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'config.json'
            for change in ({'speech_chars_per_second': 0}, {'max_scenes': True},
                           {'unknown': 1}, {'target_seconds': [120, 60]},
                           {'aliases': {'甲': '乙', '乙': '甲'}},
                           {'voice_tags': {'inner': 'OS', 'remote': 'OS', 'offscreen': '画外'}}):
                target.write_text(json.dumps(change), encoding='utf-8')
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_config(target)

    def test_submission_round_trip_preserves_actual_text(self):
        analysis = self.check_sample()
        text = render_markdown(analysis, 'submission')
        self.assertNotIn('【蓝】', text)
        self.assertNotIn('【节拍】', text)
        self.assertNotIn('【钩子】', text)
        self.assertIn('【闪回结束】', text)
        submitted = parse_script(text, input_mode='submission')
        self.assertTrue(submitted['ok'], submitted['issues'])
        left = [(e['kind'], e['payload']) for e in analysis['events'] if e['kind'] in {'dialogue', 'action', 'subtitle', 'time'}]
        right = [(e['kind'], e['payload']) for e in submitted['events'] if e['kind'] in {'dialogue', 'action', 'subtitle', 'time'}]
        self.assertEqual(left, right)

    def test_failed_export_does_not_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'bad.md', Path(folder) / 'existing.docx'
            source.write_text('# 第三集【1min1s】\n## 2-9 客厅-内-日\n幽灵：没有名单。\n【闪回】', encoding='utf-8')
            output.write_bytes(b'keep-existing')
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                convert(source, output)
            self.assertEqual(output.read_bytes(), b'keep-existing')

    def test_word_xml_layout_and_submission_content(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'script.md'
            source.write_text(SAMPLE.replace('现在，分清你我了吗？', '现在，分清你我了吗，A&B<确认>？'), encoding='utf-8')
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            for mode in ('analysis', 'submission'):
                output = Path(folder) / (mode + '.docx')
                with contextlib.redirect_stdout(io.StringIO()):
                    convert(source, output, mode=mode)
                with zipfile.ZipFile(output) as package:
                    for part in package.namelist():
                        ET.fromstring(package.read(part))
                    xml = ET.fromstring(package.read('word/document.xml'))
                    text = ''.join(node.text or '' for node in xml.findall('.//w:t', ns))
                    self.assertIn('A&B<确认>', text)
                    self.assertEqual(len(xml.findall('.//w:pgSz', ns)), 1)
                    self.assertTrue(xml.findall('.//w:keepNext', ns))
                    if mode == 'submission':
                        self.assertNotIn('付款证据遭到轻视', text)
                        self.assertNotIn('【钩子】', text)
                        self.assertFalse(xml.findall('.//w:shd', ns))
                    else:
                        self.assertTrue(xml.findall('.//w:shd', ns))

    def test_cli_status_and_json_location(self):
        with tempfile.TemporaryDirectory() as folder:
            source, report = Path(folder) / 'bad.md', Path(folder) / 'report.json'
            source.write_text(SAMPLE.replace('## 1-2', '## 2-2'), encoding='utf-8')
            run = subprocess.run([sys.executable, str(ROOT / 'scripts/validate_script.py'), str(source), '--json', str(report)], capture_output=True)
            self.assertEqual(run.returncode, 1)
            data = json.loads(report.read_text(encoding='utf-8'))
            issue = next(i for i in data['issues'] if i['code'] == 'scene_episode')
            self.assertEqual(issue['scene'], '2-2')
            self.assertGreater(issue['line'], 1)

    def test_duplicate_fields_and_control_characters(self):
        malformed = PLAIN.replace('独立动作=3', '同步动作=3')
        self.assertIn('beat_timing', self.codes(parse_script(malformed, input_mode='submission')))
        self.assertIn('control_character', self.codes(parse_script(PLAIN.replace('一二', '一\x00二'), input_mode='submission')))

    def test_hook_must_be_last(self):
        text = SAMPLE + '\n## 1-4 门口-内-日\n出场人物：苏晚晴\n苏晚晴：走吧。\n'
        self.assertIn('after_hook', self.codes(self.check_sample(text)))

    def test_second_episode_has_page_break_and_closed_time(self):
        second = SAMPLE.replace('# 第一集', '# 第二集').replace('## 1-', '## 2-')
        result = self.check_sample(SAMPLE + '\n' + second)
        self.assertTrue(result['ok'], result['issues'])
        from build_docx import document_xml
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        doc = ET.fromstring(document_xml(result))
        self.assertEqual(len(doc.findall('.//w:pageBreakBefore', ns)), 1)

    def test_output_cannot_replace_source_or_config(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.md'
            source.write_text(SAMPLE, encoding='utf-8')
            config = Path(folder) / 'config.json'
            config.write_text('{}', encoding='utf-8')
            with self.assertRaises(ValueError):
                convert(source, source)
            with self.assertRaises(ValueError):
                convert(source, config, config_path=config)
            self.assertEqual(source.read_text(encoding='utf-8'), SAMPLE)
            self.assertEqual(config.read_text(encoding='utf-8'), '{}')

    def test_punctuation_warning_preserves_dialogue_separator(self):
        text = PLAIN.replace('甲（OS）：', '甲（OS）:')
        result = parse_script(text, input_mode='submission')
        self.assertTrue(result['ok'])
        self.assertIn('punctuation', {i['code'] for i in result['issues']})
        from build_docx import document_xml
        self.assertIn('甲（OS）:', document_xml(result, 'submission'))


if __name__ == '__main__':
    unittest.main()
