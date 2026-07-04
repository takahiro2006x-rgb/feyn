# -*- coding: utf-8 -*-
"""llm.py（Gemini/Groq共通チャットインターフェース）のテスト"""
from types import SimpleNamespace

from google.genai import types as genai_types

import llm


# ===== GroqChatSession =====

class _FakeGroqCompletions:
    def __init__(self, reply='了解！'):
        self.reply = reply
        self.calls = []

    def create(self, model, messages, **kwargs):
        self.calls.append({'model': model, 'messages': messages})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


class _FakeGroqClient:
    def __init__(self, reply='了解！'):
        self.chat = SimpleNamespace(completions=_FakeGroqCompletions(reply))


def test_groq_chat_session_first_turn_has_system_and_user_only():
    client = _FakeGroqClient()
    sess = llm.GroqChatSession(client, 'llama-3.3-70b-versatile', 'システム指示')
    sess.send_message('こんにちは')

    messages = client.chat.completions.calls[0]['messages']
    assert messages[0] == {'role': 'system', 'content': 'システム指示'}
    assert messages[1] == {'role': 'user', 'content': 'こんにちは'}
    assert len(messages) == 2


def test_groq_chat_session_accumulates_history_across_turns():
    client = _FakeGroqClient()
    sess = llm.GroqChatSession(client, 'llama-3.3-70b-versatile', 'システム指示')
    sess.send_message('1回目の発言')
    sess.send_message('2回目の発言')

    second_call_messages = client.chat.completions.calls[1]['messages']
    # system + (1回目のuser/assistant) + 2回目のuser
    assert second_call_messages == [
        {'role': 'system', 'content': 'システム指示'},
        {'role': 'user', 'content': '1回目の発言'},
        {'role': 'assistant', 'content': '了解！'},
        {'role': 'user', 'content': '2回目の発言'},
    ]


def test_groq_chat_session_get_history_returns_plain_role_message_dicts():
    sess = llm.GroqChatSession(_FakeGroqClient(), 'llama-3.3-70b-versatile', '指示')
    sess.send_message('質問')
    assert sess.get_history() == [
        {'role': 'user', 'message': '質問'},
        {'role': 'model', 'message': '了解！'},
    ]


def test_groq_chat_session_accepts_preexisting_plain_history():
    history = [{'role': 'user', 'message': '前回の発言'}, {'role': 'model', 'message': '前回の返事'}]
    client = _FakeGroqClient()
    sess = llm.GroqChatSession(client, 'llama-3.1-8b-instant', '指示', history=history)
    sess.send_message('続きの発言')

    messages = client.chat.completions.calls[0]['messages']
    assert messages == [
        {'role': 'system', 'content': '指示'},
        {'role': 'user', 'content': '前回の発言'},
        {'role': 'assistant', 'content': '前回の返事'},
        {'role': 'user', 'content': '続きの発言'},
    ]


# ===== GeminiChatSession =====

class _FakeGeminiNativeChat:
    def __init__(self, history=None):
        self._history = history or []

    def send_message(self, text):
        return SimpleNamespace(text=f'reply to {text}')

    def get_history(self):
        return self._history


def test_gemini_chat_session_converts_native_history_to_plain_dicts():
    native_history = [
        genai_types.Content(role='user', parts=[genai_types.Part(text='学生の発言')]),
        genai_types.Content(role='model', parts=[genai_types.Part(text='Feynの返事')]),
    ]
    sess = llm.GeminiChatSession(_FakeGeminiNativeChat(native_history))
    assert sess.get_history() == [
        {'role': 'user', 'message': '学生の発言'},
        {'role': 'model', 'message': 'Feynの返事'},
    ]


def test_gemini_chat_session_send_message_delegates_to_native_chat():
    sess = llm.GeminiChatSession(_FakeGeminiNativeChat())
    response = sess.send_message('こんにちは')
    assert response.text == 'reply to こんにちは'


# ===== create_chat のプロバイダ振り分け =====

class _FakeGeminiChats:
    def __init__(self):
        self.create_calls = []

    def create(self, model, config, history=None):
        self.create_calls.append({'model': model, 'history': history})
        return _FakeGeminiNativeChat()


class _FakeGeminiClient:
    def __init__(self):
        self.chats = _FakeGeminiChats()


def test_create_chat_dispatches_groq_models_to_groq_session():
    groq_client = _FakeGroqClient()
    chat = llm.create_chat(_FakeGeminiClient(), groq_client, 'llama-3.3-70b-versatile', '指示')
    assert isinstance(chat, llm.GroqChatSession)


def test_create_chat_dispatches_gemini_models_to_gemini_session():
    gemini_client = _FakeGeminiClient()
    chat = llm.create_chat(gemini_client, _FakeGroqClient(), 'gemini-2.5-flash', '指示')
    assert isinstance(chat, llm.GeminiChatSession)
    assert gemini_client.chats.create_calls[0]['model'] == 'gemini-2.5-flash'


def test_create_chat_converts_plain_history_to_gemini_content_objects():
    gemini_client = _FakeGeminiClient()
    plain_history = [{'role': 'user', 'message': 'こんにちは'}, {'role': 'model', 'message': 'やあ'}]
    llm.create_chat(gemini_client, _FakeGroqClient(), 'gemini-2.0-flash', '指示', history=plain_history)

    passed_history = gemini_client.chats.create_calls[0]['history']
    assert passed_history[0].role == 'user'
    assert passed_history[0].parts[0].text == 'こんにちは'
    assert passed_history[1].role == 'model'
    assert passed_history[1].parts[0].text == 'やあ'
