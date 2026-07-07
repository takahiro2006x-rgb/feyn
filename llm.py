# =====================================================
# LLMプロバイダの抽象化層
#
# Gemini（google-genai）とGroq（OpenAI互換）を、同じ
# .send_message(text) -> オブジェクト(.textを持つ) / .get_history() のインターフェースで
# 扱えるようにする。会話履歴はプレーンな {'role': 'user'|'model', 'message': str}
# の辞書リストに統一し、フォールバックでプロバイダが切り替わっても
# 会話の文脈を引き継げるようにする。
# =====================================================
from types import SimpleNamespace

from google.genai import types as genai_types

# Groqは無料枠がGeminiとは別カウントのため、Gemini全モデル枯渇時の
# 最終フォールバックとして使う（GROQ_API_KEY未設定なら自動的に使われない）
GROQ_MODELS = [
    'llama-3.3-70b-versatile',
    'llama-3.1-8b-instant',
]


class GeminiChatSession:
    """google-genaiのネイティブなチャットオブジェクトを共通インターフェースに合わせる"""

    def __init__(self, native_chat):
        self._chat = native_chat

    def send_message(self, text, image=None):
        # image: (bytes, mime_type) のタプル。写真モード（自分の問題をアップロード）用。
        if image:
            data, mime_type = image
            parts = [genai_types.Part.from_bytes(data=data, mime_type=mime_type)]
            if text:
                parts.append(genai_types.Part.from_text(text=text))
            return self._chat.send_message(parts)
        return self._chat.send_message(text)

    def get_history(self):
        plain = []
        for content in self._chat.get_history():
            role = 'user' if content.role == 'user' else 'model'
            text = ''.join(p.text for p in content.parts if getattr(p, 'text', None))
            plain.append({'role': role, 'message': text})
        return plain


class GroqChatSession:
    """Groq（OpenAI互換API）はステートレスなので、毎回メッセージ全履歴を組み立てて送る"""

    def __init__(self, groq_client, model, instruction, history=None):
        self._client      = groq_client
        self._model       = model
        self._instruction = instruction
        self._history      = list(history) if history else []

    def send_message(self, text, image=None):
        # Groqのモデルは画像非対応（写真モードは常にGeminiだけを使うため呼ばれない想定）
        messages = [{'role': 'system', 'content': self._instruction}]
        for h in self._history:
            role = 'user' if h['role'] == 'user' else 'assistant'
            messages.append({'role': role, 'content': h['message']})
        messages.append({'role': 'user', 'content': text})

        resp  = self._client.chat.completions.create(model=self._model, messages=messages)
        reply = resp.choices[0].message.content or ''

        self._history.append({'role': 'user', 'message': text})
        self._history.append({'role': 'model', 'message': reply})
        return SimpleNamespace(text=reply)

    def get_history(self):
        return list(self._history)


def create_chat(gemini_client, groq_client, model, instruction, history=None):
    """model名に応じてGemini/Groqいずれかのチャットセッションを作る。
    history はプロバイダ非依存のプレーン辞書リスト（{'role','message'}）。
    """
    if model in GROQ_MODELS:
        return GroqChatSession(groq_client, model, instruction, history=history)

    config_kwargs = {'system_instruction': instruction}
    if model.startswith('gemini-2.5'):
        # 2.5系は思考モードが既定でONになり応答が遅くなるためOFFにする
        config_kwargs['thinking_config'] = genai_types.ThinkingConfig(thinking_budget=0)

    gemini_history = None
    if history:
        gemini_history = [
            genai_types.Content(
                role=('user' if h['role'] == 'user' else 'model'),
                parts=[genai_types.Part(text=h['message'])],
            )
            for h in history
        ]

    native = gemini_client.chats.create(
        model=model,
        config=genai_types.GenerateContentConfig(**config_kwargs),
        history=gemini_history,
    )
    return GeminiChatSession(native)
