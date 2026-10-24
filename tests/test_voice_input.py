# -*- coding: utf-8 -*-
"""音声入力（script.js の Web Speech API 部分）のテスト。

ブラウザが無くても確かめられるよう、script.js から音声入力の部分だけを取り出し、
Node.js 上で「偽の画面部品」と「偽の音声認識」をつないで動かす。Node.js が無い環境ではスキップする。
"""
import json
import shutil
import subprocess

import pytest

NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='Node.js が無い環境ではスキップ')

START = '// ===== 音声入力（ブラウザ標準の Web Speech API） ====='
END = '// ===== PCでページ全体がずれたら元に戻す ====='

HARNESS = r'''
const log = [];
function el() {
  const classes = new Set();
  return {
    style: { display: 'none' }, title: '', attrs: {}, handlers: {},
    classList: { toggle(c, on) { on ? classes.add(c) : classes.delete(c); }, contains(c) { return classes.has(c); } },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    addEventListener(t, f) { this.handlers[t] = f; },
    click() { this.handlers.click && this.handlers.click(); },
  };
}
const micEl = el();
const textarea = { value: '' };
const document = { getElementById: (id) => (id === 'micBtn' ? micEl : null) };
const store = {};
const localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); } };
function addMessage(role, text) { log.push(text); }
let lastRec = null;
class FakeRec {
  constructor() { lastRec = this; this.stopped = false; }
  start() {}
  stop() { this.stopped = true; if (this.onend) this.onend(); }
}
const window = SUPPORTED ? { webkitSpeechRecognition: FakeRec } : {};
const res = (items) => ({ results: items.map(([t, f]) => Object.assign([{ transcript: t }], { isFinal: f })) });

CODE

const out = { visible: micEl.style.display !== 'none' };
if (SUPPORTED) {
  textarea.value = '答えは';
  micEl.click();
  out.recording = micEl.classList.contains('recording');
  out.lang = lastRec.lang;
  lastRec.onresult(res([['エネルギー', false]]));
  out.interim = textarea.value;
  lastRec.onresult(res([['エネルギー保存則です', true]]));
  out.final = textarea.value;
  const rec = lastRec;
  micEl.click();
  out.afterStop = { active: micActive, stopped: rec.stopped, detached: rec.onresult === null };
  rec.onerror && rec.onerror({ error: 'no-speech' });
  micEl.click();                                   // 2回目: お知らせは出ない
  lastRec.onerror({ error: 'not-allowed' });
  out.messages = log;
}
console.log(JSON.stringify(out));
'''


def _run(supported):
    js = open('script.js', encoding='utf-8').read()
    code = js[js.index(START):js.index(END)]
    program = HARNESS.replace('SUPPORTED', 'true' if supported else 'false').replace('CODE', code)
    result = subprocess.run([NODE, '-e', program], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_button_is_hidden_when_the_browser_does_not_support_speech():
    assert _run(supported=False) == {'visible': False}


def test_speech_is_added_after_existing_text_and_can_be_stopped():
    out = _run(supported=True)
    assert out['visible'] is True
    assert out['recording'] is True
    assert out['lang'] == 'ja-JP'
    assert out['interim'] == '答えはエネルギー'                 # 話している途中も見える
    assert out['final'] == '答えはエネルギー保存則です'
    assert out['afterStop'] == {'active': False, 'stopped': True, 'detached': True}


def test_privacy_notice_is_shown_once_and_errors_are_explained():
    messages = _run(supported=True)['messages']
    notices = [m for m in messages if '音声認識サービス' in m]
    assert len(notices) == 1
    assert any('マイクが使えない' in m for m in messages)


def test_sending_stops_voice_input_first():
    js = open('script.js', encoding='utf-8').read()
    send = js[js.index('async function sendMessage()'):]
    assert send.index('stopVoiceInput()') < send.index('textarea.value.trim()')
    html = open('index.html', encoding='utf-8').read()
    assert 'id="micBtn"' in html and 'style="display:none;"' in html.split('id="micBtn"')[1].split('>')[0]
