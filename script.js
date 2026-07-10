// ===== PWA: サービスワーカー登録 =====
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// ===== テーマ切り替え =====
const themeToggle = document.getElementById('themeToggle');

function updateThemeLabel() {
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  themeToggle.textContent = isDark ? '☀️ ライトモード' : '🌙 ダークモード';
}
updateThemeLabel();

themeToggle.addEventListener('click', () => {
  const html = document.documentElement;
  const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('feynTheme', next);
  updateThemeLabel();
});


// ===== 要素の取得 =====
const messagesEl   = document.getElementById('messages');
const textarea     = document.querySelector('.input-area textarea');
const sendBtn      = document.querySelector('.send-btn');
const teacherInput = document.getElementById('teacherName');
const diffSelect   = document.getElementById('difficulty');
const subjectBadge = document.getElementById('subjectBadge');
const charRole     = document.getElementById('charRole');
const resetBtn     = document.querySelector('.reset-btn');
const hintBtn      = document.getElementById('hintBtn');
const revealBtn    = document.getElementById('revealBtn');

let sessionKey     = null;
let isBusy         = false;
let startAbortCtrl = null;
let startInFlight  = false;  // /api/start が完了するまで次の開始を防ぐ（無料枠の無駄遣い対策）
let currentSubject = '物理';
let currentEmoji   = '⚡';
let reviewGapId    = null;  // 苦手ノートからの復習セッション中はギャップIDが入る
let assignmentId   = null;  // 先生からの課題セッション中は課題IDが入る

const SUBJECT_COLORS = {
  '物理': { color: '#3B82F6', dark: '#1D4ED8' },
  '数学': { color: '#8B5CF6', dark: '#6D28D9' },
  '英語': { color: '#F97316', dark: '#C2410C' },
  '化学': { color: '#10B981', dark: '#047857' },
  '生物': { color: '#84CC16', dark: '#4D7C0F' },
  '国語': { color: '#EF4444', dark: '#B91C1C' },
  '歴史': { color: '#B45309', dark: '#78350F' },
};

// ===== 単元カリキュラム（科目 → 大分類 → 単元） =====
const UNIT_CURRICULUM = {
  '物理': {
    '力学':   ['運動の法則', 'エネルギーと運動量', '円運動・万有引力', '単振動'],
    '熱力学': ['熱と温度', '気体の性質', '熱力学の法則'],
    '波動':   ['波の性質', '音', '光'],
    '電磁気': ['電気', '磁気', '電磁誘導', '交流'],
    '原子':   ['電子と光', '原子と原子核'],
  },
  '数学': {
    '数学I':   ['数と式', '集合と命題', '二次関数', '図形と計量', 'データの分析'],
    '数学II':  ['いろいろな式', '図形と方程式', '指数関数・対数関数', '三角関数', '微分・積分の考え方'],
    '数学III': ['極限', '微分法', '積分法'],
    '数学A':   ['図形の性質', '場合の数と確率', '数学と人間の活動'],
    '数学B':   ['数列', '統計的な推測'],
    '数学C':   ['ベクトル', '平面上の曲線と複素数平面'],
  },
  '英語': {
    '文法':     ['時制', '仮定法', '関係詞', '比較', '不定詞・動名詞・分詞', '受動態', '話法'],
    '構文・語法': ['倒置・強調・省略', '前置詞・句動詞', '接続詞'],
  },
  '化学': {
    '理論化学': ['物質の構成', '化学結合', '物質量と化学反応式', '酸と塩基', '酸化還元', '化学反応と熱', '電池・電気分解', '反応速度・化学平衡'],
    '無機化学': ['非金属元素', '金属元素とイオン'],
    '有機化学': ['炭化水素', 'アルコール・カルボニル化合物', '芳香族化合物', '高分子化合物'],
  },
  '生物': {
    '細胞と分子':   ['細胞の構造', '代謝', '遺伝情報の発現'],
    '生殖と発生':   ['生殖', '発生のしくみ'],
    '生物の体内環境': ['恒常性', '免疫'],
    '生態と進化':   ['生態系', '進化と系統'],
  },
  '国語': {
    '現代文': ['評論の読解', '小説の読解', '随筆の読解'],
    '古文':   ['古文文法', '古文単語・古典常識'],
    '漢文':   ['句法', '漢詩'],
  },
  '歴史': {
    '日本史': ['原始・古代', '中世', '近世', '近代', '現代'],
    '世界史': ['古代文明', '中世', '近世', '近代', '現代'],
  },
};


// ===== 科目カラー適用 =====
function applySubjectColor(subject) {
  const c = SUBJECT_COLORS[subject] || { color: '#58CC02', dark: '#46A302' };
  document.documentElement.style.setProperty('--subject-color',      c.color);
  document.documentElement.style.setProperty('--subject-color-dark', c.dark);

  const tie    = document.getElementById('feynTie');
  const tieTip = document.getElementById('feynTieTip');
  if (tie) {
    tie.setAttribute('fill',    c.color);
    tieTip.setAttribute('fill', c.dark);
  }
}


// ===== キャラパネル更新 =====
function updateCharPanel(subject, emoji) {
  subjectBadge.textContent = `${emoji} ${subject}`;
  charRole.textContent     = `${subject}担当のライバル`;
  document.querySelectorAll('.avatar').forEach(el => el.textContent = emoji);
  applySubjectColor(subject);
  updateStatsDisplay();
}


// ===== 設定値を読む =====
function getSettings() {
  const settings = {
    subject:      currentSubject,
    difficulty:   diffSelect.value,
    teacher_name: teacherInput.value.trim() || '先生',
  };
  if (reviewGapId)  settings.gap_id        = reviewGapId;
  if (assignmentId) settings.assignment_id = assignmentId;
  return settings;
}

function escText(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ===== 数式（LaTeX）をKaTeXで描画 =====
const KATEX_DELIMITERS = [
  { left: '$$', right: '$$', display: true },
  { left: '\\[', right: '\\]', display: true },
  { left: '$', right: '$', display: false },
  { left: '\\(', right: '\\)', display: false },
];

function renderMath(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, { delimiters: KATEX_DELIMITERS, throwOnError: false });
  }
}

// ===== メッセージを追加 =====
// raw: true の場合のみ text をHTMLとして扱う（開発者が書いた定型文専用。ユーザー入力/AI応答は必ずエスケープする）
function addMessage(role, text, { raw = false } = {}) {
  const el = document.createElement('div');
  el.classList.add('message', role, 'pop-in');
  const bubbleHtml = raw ? text : escText(text);
  el.innerHTML = role === 'feyn'
    ? `<div class="avatar">${currentEmoji}</div><div class="bubble">${bubbleHtml}</div>`
    : `<div class="bubble">${bubbleHtml}</div>`;
  messagesEl.appendChild(el);
  renderMath(el.querySelector('.bubble'));
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}

// ===== ヒント・答えの表示用メッセージ（kind: 'hint' | 'answer'） =====
function addHelpMessage(kind, label, text) {
  const el = document.createElement('div');
  el.classList.add('message', 'feyn', 'pop-in');
  el.innerHTML = `<div class="avatar">${currentEmoji}</div><div class="bubble ${kind}"><strong>${escText(label)}</strong><br>${escText(text)}</div>`;
  messagesEl.appendChild(el);
  renderMath(el.querySelector('.bubble'));
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}


// ===== 無料枠上限メッセージ（アップグレード導線つき） =====
function showUpgradePrompt(message) {
  const el = document.createElement('div');
  el.classList.add('message', 'feyn', 'pop-in');
  el.innerHTML = `
    <div class="avatar">${currentEmoji}</div>
    <div class="bubble">
      🔒 ${escText(message)}
      <div class="quick-replies">
        <button class="quick-reply" id="upgradeFromLimitBtn">⭐ 有料プランを見る</button>
      </div>
    </div>
  `;
  messagesEl.appendChild(el);
  document.getElementById('upgradeFromLimitBtn').addEventListener('click', () => {
    window.location.href = '/mypage';
  });
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}


// ===== 考え中アニメーション =====
function addThinking() {
  const el = document.createElement('div');
  el.classList.add('message', 'feyn', 'thinking-msg');
  el.innerHTML = `
    <div class="avatar">${currentEmoji}</div>
    <div class="bubble thinking"><span></span><span></span><span></span></div>
  `;
  messagesEl.appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}


// ===== Feynの表情変化 =====
function setExpression(state) {
  const mouth = document.getElementById('feynMouth');
  if (!mouth) return;
  if (state === 'thinking') {
    mouth.setAttribute('d', 'M 85 142 Q 100 144 115 142');
  } else if (state === 'happy') {
    mouth.setAttribute('d', 'M 74 132 Q 100 158 126 132');
  } else {
    mouth.setAttribute('d', 'M 80 136 Q 100 150 120 138');
  }
}


// ===== XPバー =====
function resetXP() {
  document.querySelector('.xp-fill').style.width = '0%';
  document.querySelector('.xp-percent').textContent = '0%';
  document.querySelectorAll('.star').forEach(s => s.classList.remove('active'));
}

function setXP(percent) {
  document.querySelector('.xp-fill').style.width = `${percent}%`;
  document.querySelector('.xp-percent').textContent = `${percent}%`;
  const stars = document.querySelectorAll('.star');
  const count = Math.floor(percent / 25);
  stars.forEach((s, i) => s.classList.toggle('active', i < count));
}


// ===== 紙吹雪 =====
function launchConfetti() {
  const canvas = document.getElementById('confettiCanvas');
  const ctx    = canvas.getContext('2d');
  canvas.width  = window.innerWidth;
  canvas.height = window.innerHeight;
  canvas.style.display = 'block';

  const colors = ['#58CC02', '#FFC200', '#FF4B4B', '#1CB0F6', '#8B5CF6', '#FF6B35'];
  const pieces = Array.from({ length: 130 }, () => ({
    x:        Math.random() * canvas.width,
    y:        Math.random() * -200 - 20,
    w:        Math.random() * 12 + 6,
    h:        Math.random() * 7 + 4,
    color:    colors[Math.floor(Math.random() * colors.length)],
    rot:      Math.random() * 360,
    rotSpeed: Math.random() * 6 - 3,
    speed:    Math.random() * 4 + 2,
    wobble:   Math.random() * Math.PI * 2,
  }));

  const startTime = Date.now();
  let frame;

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let allDone = true;
    pieces.forEach(p => {
      p.y       += p.speed;
      p.rot     += p.rotSpeed;
      p.wobble  += 0.05;
      p.x       += Math.sin(p.wobble) * 1.5;
      if (p.y < canvas.height + 20) allDone = false;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot * Math.PI / 180);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    });
    if (allDone || Date.now() - startTime > 4500) {
      canvas.style.display = 'none';
      return;
    }
    frame = requestAnimationFrame(animate);
  }
  animate();
}


// ===== サウンドエフェクト =====
let audioCtx = null;

function getAudioCtx() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  return audioCtx;
}

function playXPSound() {
  try {
    const ctx  = getAudioCtx();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(440, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.25);
  } catch (e) {}
}

function playCompleteSound() {
  try {
    const ctx   = getAudioCtx();
    const notes = [523, 659, 784, 1047];
    notes.forEach((freq, i) => {
      const osc  = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.frequency.value = freq;
      const t = ctx.currentTime + i * 0.14;
      gain.gain.setValueAtTime(0.18, t);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.4);
      osc.start(t);
      osc.stop(t + 0.4);
    });
  } catch (e) {}
}


// ===== 統計（サーバー保存。端末が変わっても引き継がれる） =====
let subjectStats = {};

function updateStatsDisplay() {
  const el = document.getElementById('statsDisplay');
  if (!el) return;
  const count = subjectStats[currentSubject] || 0;
  el.textContent = `🏆 ${currentSubject}クリア: ${count}回`;
}


// ===== 単元選択（科目を開いたら最初に表示する） =====
// clear:false を渡すと、既存の会話を消さずに選択肢メッセージを末尾に追加する
function renderPicker(promptText, buttons, { clear = true } = {}) {
  if (clear) messagesEl.innerHTML = '';
  const btnHtml = buttons.map((b, i) => `<button class="quick-reply" data-idx="${i}">${escText(b.label)}</button>`).join('');
  const el = document.createElement('div');
  el.classList.add('message', 'feyn', 'pop-in');
  el.innerHTML = `<div class="avatar">${currentEmoji}</div><div class="bubble">${promptText}<div class="quick-replies">${btnHtml}</div></div>`;
  messagesEl.appendChild(el);
  el.querySelectorAll('.quick-reply').forEach((btn, i) => {
    btn.addEventListener('click', () => {
      // 連打で同じボタンから二重にAPIが呼ばれ、無料枠を無駄に消費するのを防ぐ
      el.querySelectorAll('.quick-reply').forEach(b => b.disabled = true);
      buttons[i].onClick();
    });
  });
  el.scrollIntoView({ behavior: 'smooth' });
}

// ===== 先生からの課題を受け取る画面 =====
function showAssignmentPicker(assignments) {
  sessionKey = null;
  hintBtn.disabled   = true;
  revealBtn.disabled = true;
  resetXP();
  setExpression('normal');

  const buttons = assignments.map(a => ({
    label: `📋 ${a.subject}${a.unit ? '・' + a.unit : '（おまかせ）'}`,
    onClick: () => {
      const btn = document.querySelector(`.subject[data-subject="${a.subject}"]`);
      if (btn) {
        document.querySelectorAll('.subject').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentSubject = btn.dataset.subject;
        currentEmoji   = btn.dataset.emoji;
        updateCharPanel(currentSubject, currentEmoji);
      }
      assignmentId = a.id;
      startSession(a.unit || undefined);
    },
  }));
  buttons.push({ label: '↩️ 自分で科目を選ぶ', onClick: () => showUnitPicker(currentSubject) });

  const plural = assignments.length > 1 ? `（${assignments.length}件）` : '';
  renderPicker(`📋 先生から課題が届いています！${plural}`, buttons);
}

function showUnitPicker(subject) {
  sessionKey = null;
  assignmentId = null;
  hintBtn.disabled   = true;
  revealBtn.disabled = true;
  resetXP();
  setExpression('normal');

  const categories = Object.keys(UNIT_CURRICULUM[subject] || {});
  const buttons = [
    { label: '🎲 Feynにおまかせ', onClick: () => startSession() },
    { label: '📷 今解いてる問題を見せる', onClick: () => showPhotoUploadPrompt() },
    ...categories.map(cat => ({ label: cat, onClick: () => showUnitSubPicker(subject, cat) })),
  ];
  renderPicker('今日はどの単元を教えてくれる？📖', buttons);
}

function showUnitSubPicker(subject, category) {
  const units = (UNIT_CURRICULUM[subject] || {})[category] || [];
  const buttons = [
    { label: '🎲 この中でおまかせ', onClick: () => startSession(category) },
    ...units.map(u => ({ label: u, onClick: () => startSession(u) })),
    { label: '← 戻る', onClick: () => showUnitPicker(subject) },
  ];
  renderPicker(`「${escText(category)}」のどこがいい？`, buttons);
}


// ===== セッション開始 =====
async function startSession(unit) {
  // すでに /api/start が実行中なら無視する（無駄なGemini呼び出しを防ぐ）
  if (startInFlight) return;
  startInFlight = true;

  if (startAbortCtrl) startAbortCtrl.abort();
  startAbortCtrl = new AbortController();
  const signal = startAbortCtrl.signal;

  messagesEl.innerHTML = '';
  sessionKey = null;
  resetXP();
  setExpression('normal');

  const thinkingEl = addThinking();
  const settings   = getSettings();
  if (unit) settings.unit = unit;

  try {
    const response = await fetch('/api/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(settings),
      signal,
    });
    const data = await response.json();
    thinkingEl.remove();
    refreshLives();
    if (data.error) {
      if (data.upgrade_required) showUpgradePrompt(data.error);
      else addMessage('feyn', `⚠️ ${data.error}`);
      return;
    }
    sessionKey = data.session_key;
    hintBtn.disabled   = false;
    revealBtn.disabled = false;
    addMessage('feyn', data.reply);
  } catch (e) {
    if (e.name !== 'AbortError') {
      thinkingEl.remove();
      addMessage('feyn', '⚠️ 接続エラーが発生しました。');
    } else {
      thinkingEl.remove();
    }
  } finally {
    startInFlight = false;
  }
}


// ===== 写真アップロード（自分が今解いてる問題について質問してもらう） =====
function showPhotoUploadPrompt() {
  const input = document.getElementById('photoUploadInput');
  input.value = ''; // 同じファイルを選び直しても change が発火するようにする
  input.click();
}

document.getElementById('photoUploadInput').addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => showPhotoConfirm(reader.result.split(',')[1], file.type, reader.result);
  reader.readAsDataURL(file);
});

function showPhotoConfirm(base64, mimeType, dataUrl) {
  messagesEl.innerHTML = '';
  const el = document.createElement('div');
  el.classList.add('message', 'feyn', 'pop-in');
  el.innerHTML = `
    <div class="avatar">${currentEmoji}</div>
    <div class="bubble">
      この写真でよければ質問するね！
      <div class="photo-preview"><img src="${dataUrl}" alt="アップロードした問題"></div>
      <div class="quick-replies">
        <button class="quick-reply" id="photoSendBtn">🚀 これで質問してもらう</button>
        <button class="quick-reply" id="photoRetryBtn">↩️ 選び直す</button>
      </div>
    </div>
  `;
  messagesEl.appendChild(el);
  document.getElementById('photoSendBtn').addEventListener('click', () => {
    el.querySelectorAll('.quick-reply').forEach(b => b.disabled = true);
    startSessionWithPhoto(base64, mimeType, dataUrl);
  });
  document.getElementById('photoRetryBtn').addEventListener('click', () => showPhotoUploadPrompt());
  el.scrollIntoView({ behavior: 'smooth' });
}

// アップロードした写真を会話に残しておく（あとで見返せるように）
function addImageMessage(dataUrl) {
  const el = document.createElement('div');
  el.classList.add('message', 'user', 'pop-in');
  el.innerHTML = `<div class="bubble"><div class="photo-preview" style="margin-top:0;"><img src="${dataUrl}" alt="アップロードした問題"></div></div>`;
  messagesEl.appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
  return el;
}

async function startSessionWithPhoto(base64, mimeType, dataUrl) {
  if (startInFlight) return;
  startInFlight = true;

  if (startAbortCtrl) startAbortCtrl.abort();
  startAbortCtrl = new AbortController();
  const signal = startAbortCtrl.signal;

  messagesEl.innerHTML = '';
  sessionKey = null;
  resetXP();
  setExpression('normal');
  addImageMessage(dataUrl);

  const thinkingEl = addThinking();
  const settings   = getSettings();
  settings.photo      = base64;
  settings.photo_mime = mimeType;

  try {
    const response = await fetch('/api/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(settings),
      signal,
    });
    const data = await response.json();
    thinkingEl.remove();
    refreshLives();
    if (data.error) {
      if (data.upgrade_required) showUpgradePrompt(data.error);
      else addMessage('feyn', `⚠️ ${data.error}`);
      return;
    }
    sessionKey = data.session_key;
    hintBtn.disabled   = false;
    revealBtn.disabled = false;
    addMessage('feyn', data.reply);
  } catch (e) {
    if (e.name !== 'AbortError') {
      thinkingEl.remove();
      addMessage('feyn', '⚠️ 接続エラーが発生しました。');
    } else {
      thinkingEl.remove();
    }
  } finally {
    startInFlight = false;
  }
}


// ===== 中断した学習を続きから再開する（学習のきろくから） =====
async function resumeSession(subject, dateStr, sessionId) {
  if (startInFlight) return;
  startInFlight = true;

  messagesEl.innerHTML = '';
  sessionKey = null;
  resetXP();
  setExpression('normal');

  const thinkingEl = addThinking();

  try {
    const response = await fetch('/api/resume', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ subject, date: dateStr, session_id: sessionId || null }),
    });
    const data = await response.json();
    thinkingEl.remove();
    if (data.error) { addMessage('feyn', `⚠️ ${data.error}`); return; }

    sessionKey = data.session_key;
    hintBtn.disabled   = false;
    revealBtn.disabled = false;
    data.messages.forEach(m => addMessage(m.role === 'user' ? 'user' : 'feyn', m.message));
    setXP(data.progress || 0);
    messagesEl.lastElementChild?.scrollIntoView({ behavior: 'smooth' });
    history.replaceState(null, '', '/');
  } catch (e) {
    thinkingEl.remove();
    addMessage('feyn', '⚠️ 接続エラーが発生しました。');
  } finally {
    startInFlight = false;
  }
}


// ===== メッセージ送信 =====
async function sendMessage() {
  const text = textarea.value.trim();
  if (!text || isBusy || !sessionKey) return;

  isBusy = true;
  sendBtn.disabled = true;
  setExpression('thinking');

  addMessage('user', text);
  textarea.value = '';

  const thinkingEl = addThinking();

  const response = await fetch('/api/chat', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ message: text, session_key: sessionKey }),
  });

  const data = await response.json();
  thinkingEl.remove();

  if (response.status === 429) {
    setExpression('normal');
    addMessage('feyn', '本日のAPI利用上限に達しました。明日また試してみてね！');
  } else if (response.status === 503) {
    setExpression('normal');
    addMessage('feyn', 'ごめん、今AIサーバーが混み合ってる。少し待ってからもう一回送ってみて！');
  } else if (data.error) {
    setExpression('normal');
    if (data.error.includes('セッションが見つかりません')) {
      addMessage('feyn', '⚠️ セッションが切れていたので再開します...');
      startSession();
    } else {
      addMessage('feyn', `⚠️ ${data.error}`);
    }
  } else {
    setXP(data.progress ?? 0);
    playXPSound();

    if (data.is_done) {
      setExpression('happy');
      hintBtn.disabled   = true;
      revealBtn.disabled = true;
      addMessage('feyn', data.reply);
      setTimeout(() => {
        launchConfetti();
        playCompleteSound();
      }, 400);
      fetch('/api/complete', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          subject: currentSubject, difficulty: diffSelect.value,
          gap_id: reviewGapId, assignment_id: assignmentId,
          session_key: sessionKey,
        }),
      }).then(async (res) => {
        const wasReview     = !!reviewGapId;
        const wasAssignment = !!assignmentId;
        reviewGapId  = null;
        assignmentId = null;
        history.replaceState(null, '', '/');

        // ギャップ分析の結果を「今日の気づき」として表示
        const c = await res.json().catch(() => ({}));
        if (wasReview) {
          addMessage('feyn', '✅ この苦手は<strong>解決済み</strong>にしたよ！ <a href="/mypage?tab=gaps" style="color:inherit;">📝 苦手ノートを見る</a>', { raw: true });
        }
        if (wasAssignment) {
          addMessage('feyn', '✅ 先生からの<strong>課題</strong>を終えたよ！お疲れさま！', { raw: true });
        }
        if (c.analysis && c.analysis.gaps && c.analysis.gaps.length > 0) {
          const items = c.analysis.gaps.map(g => `・${escText(g.description)}`).join('<br>');
          addMessage('feyn',
            `📝 <strong>今日の気づき</strong>（テーマ: ${escText(c.analysis.topic)}）<br>${items}<br>` +
            `<a href="/mypage?tab=gaps" style="color:inherit;">→ 苦手ノートに記録したよ。あとで復習しよう！</a>`,
            { raw: true });
        }

        fetchStreak();
        const r = await fetch('/api/me');
        if (r.ok) {
          const u = await r.json();
          updateStatus(u.total_clears || 0);
          subjectStats = u.subject_clears || {};
          updateStatsDisplay();
          updateGapsBadge(u.due_reviews || 0);
        }
      }).catch(() => {});
    } else {
      setExpression('normal');
      addMessage('feyn', data.reply);
    }
  }

  isBusy = false;
  sendBtn.disabled = false;
}


// ===== 科目ボタン =====
document.querySelectorAll('.subject').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.subject').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentSubject = btn.dataset.subject;
    currentEmoji   = btn.dataset.emoji;
    reviewGapId    = null;
    assignmentId   = null;
    history.replaceState(null, '', '/');
    updateCharPanel(currentSubject, currentEmoji);
    showUnitPicker(currentSubject);
  });
});


// ===== リセットボタン =====
resetBtn.addEventListener('click', () => {
  reviewGapId  = null;
  assignmentId = null;
  history.replaceState(null, '', '/');
  showUnitPicker(currentSubject);
});


// ===== 送信ボタン / Ctrl+Enter =====
sendBtn.addEventListener('click', sendMessage);
textarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.ctrlKey) {
    e.preventDefault();
    sendMessage();
  }
});


// ===== 数式入力モーダル（MathLiveで組み立ててLaTeXとして挿入） =====
const mathBtn          = document.getElementById('mathBtn');
const mathModalOverlay = document.getElementById('mathModalOverlay');
const mathField        = document.getElementById('mathField');
const mathCancelBtn    = document.getElementById('mathCancelBtn');
const mathInsertBtn    = document.getElementById('mathInsertBtn');

function openMathModal() {
  mathModalOverlay.style.display = 'flex';
  mathField.value = '';
  setTimeout(() => mathField.focus(), 50);
}

function closeMathModal() {
  mathModalOverlay.style.display = 'none';
}

function insertAtCursor(text) {
  const start  = textarea.selectionStart;
  const end    = textarea.selectionEnd;
  const before = textarea.value.slice(0, start);
  const after  = textarea.value.slice(end);
  textarea.value = `${before}${text}${after}`;
  const cursor = start + text.length;
  textarea.focus();
  textarea.setSelectionRange(cursor, cursor);
}

mathBtn.addEventListener('click', openMathModal);
mathCancelBtn.addEventListener('click', closeMathModal);
mathModalOverlay.addEventListener('click', (e) => {
  if (e.target === mathModalOverlay) closeMathModal();
});
mathField.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    e.preventDefault();
    mathInsertBtn.click();
  }
});
mathInsertBtn.addEventListener('click', () => {
  // 未入力のプレースホルダーはKaTeXが解釈できないので取り除く
  const latex = mathField.value.trim().replace(/\\placeholder\{\}/g, '');
  if (latex) insertAtCursor(`$${latex}$`);
  closeMathModal();
});

// よく使う記号・構造をワンタップで挿入するクイックボタン
document.querySelectorAll('.math-quick-row button').forEach(btn => {
  btn.addEventListener('click', () => {
    mathField.insert(btn.dataset.insert);
    mathField.focus();
  });
});


// ===== ヒント =====
hintBtn.addEventListener('click', async () => {
  if (!sessionKey || hintBtn.disabled) return;
  hintBtn.disabled   = true;
  revealBtn.disabled = true;

  const thinkingEl = addThinking();
  try {
    const res  = await fetch('/api/hint', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_key: sessionKey }),
    });
    const data = await res.json();
    thinkingEl.remove();
    if (data.error) {
      addMessage('feyn', `⚠️ ${data.error}`);
    } else {
      addHelpMessage('hint', '💡 ヒント', data.hint);
    }
  } catch (e) {
    thinkingEl.remove();
    addMessage('feyn', '⚠️ 接続エラーが発生しました。');
  } finally {
    hintBtn.disabled   = false;
    revealBtn.disabled = false;
  }
});


// ===== 答えを見る =====
revealBtn.addEventListener('click', async () => {
  if (!sessionKey || revealBtn.disabled) return;
  if (!window.confirm('答えを見ると、自分で考える機会が減っちゃうよ。それでも見る？')) return;

  hintBtn.disabled   = true;
  revealBtn.disabled = true;

  const thinkingEl = addThinking();
  try {
    const res  = await fetch('/api/reveal', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_key: sessionKey }),
    });
    const data = await res.json();
    thinkingEl.remove();
    if (data.error) {
      addMessage('feyn', `⚠️ ${data.error}`);
      hintBtn.disabled   = false;
      revealBtn.disabled = false;
      return;
    }
    addHelpMessage('answer', '🔍 答え', data.answer);
    renderPicker('答えを確認したね！このあとどうする？', [
      { label: '💪 今から説明してみる', onClick: () => { textarea.focus(); } },
      { label: '📌 今日はここまでにする', onClick: () => { window.location.href = '/mypage?tab=history'; } },
    ], { clear: false });
  } catch (e) {
    thinkingEl.remove();
    addMessage('feyn', '⚠️ 接続エラーが発生しました。');
  } finally {
    hintBtn.disabled   = false;
    revealBtn.disabled = false;
  }
});


// ===== ニックネーム保存 =====
let nameTimer = null;
teacherInput.addEventListener('input', () => {
  clearTimeout(nameTimer);
  nameTimer = setTimeout(async () => {
    const name = teacherInput.value.trim();
    if (!name) return;
    await fetch('/api/update-name', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ name }),
    });
  }, 800);
});


// ===== 秘密の質問設定 =====
const securityModal = document.getElementById('securityModal');
document.getElementById('securityBtn').addEventListener('click', () => {
  securityModal.style.display = 'flex';
});
document.getElementById('sqClose').addEventListener('click', () => {
  securityModal.style.display = 'none';
});
document.getElementById('sqSave').addEventListener('click', async () => {
  const question = document.getElementById('sqSelect').value;
  const answer   = document.getElementById('sqAnswer').value.trim();
  const errEl    = document.getElementById('sqError');
  errEl.textContent = '';
  if (!question) { errEl.textContent = '質問を選んでください'; return; }
  if (!answer)   { errEl.textContent = '答えを入力してください'; return; }

  const res  = await fetch('/api/set-security-question', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ security_question: question, security_answer: answer }),
  });
  const data = await res.json();
  if (!res.ok) { errEl.textContent = data.error; return; }
  securityModal.style.display = 'none';
  document.getElementById('securityBtn').style.display = 'none';
});


// ===== マイページ（偏差値・苦手ノート・学習のきろく） =====
document.getElementById('mypageBtn').addEventListener('click', () => {
  window.location.href = '/mypage';
});


// ===== ログアウト =====
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
});


// ===== 自動ログアウト（30分操作がなければログアウトする） =====
const AUTO_LOGOUT_MS = 30 * 60 * 1000;
let autoLogoutTimer = null;

function resetAutoLogoutTimer() {
  if (autoLogoutTimer) clearTimeout(autoLogoutTimer);
  autoLogoutTimer = setTimeout(async () => {
    await fetch('/api/logout', { method: 'POST' }).catch(() => {});
    window.location.href = '/login?timeout=1';
  }, AUTO_LOGOUT_MS);
}

['mousemove', 'keydown', 'mousedown', 'touchstart', 'scroll'].forEach(evt => {
  document.addEventListener(evt, resetAutoLogoutTimer, { passive: true });
});
resetAutoLogoutTimer();


// ===== レベル計算 =====
function getLevel(total) {
  if (total >= 50) return { level: 6, label: '伝説',       color: '#FF6B35' };
  if (total >= 30) return { level: 5, label: 'マスター',   color: '#8B5CF6' };
  if (total >= 15) return { level: 4, label: 'エキスパート', color: '#F97316' };
  if (total >= 8)  return { level: 3, label: '中級者',     color: '#3B82F6' };
  if (total >= 3)  return { level: 2, label: '初学者',     color: '#10B981' };
  return           { level: 1, label: 'ビギナー',          color: '#AFAFAF' };
}

function updateGapsBadge(count) {
  const btn = document.getElementById('mypageBtn');
  if (btn) btn.textContent = count > 0 ? `📊 マイページ 🔴${count}` : '📊 マイページ';
}

function updateStatus(totalClears) {
  const xpEl    = document.getElementById('xpCount');
  const lvEl    = document.getElementById('levelBadge');
  if (xpEl) xpEl.textContent = totalClears * 10;
  if (lvEl) {
    const lv = getLevel(totalClears);
    lvEl.textContent        = `Lv.${lv.level} ${lv.label}`;
    lvEl.style.color        = lv.color;
    lvEl.style.borderColor  = lv.color;
  }
}

// ===== ストリーク =====
async function fetchStreak() {
  try {
    const res = await fetch('/api/streak');
    const data = await res.json();
    const el = document.getElementById('streakCount');
    if (el) el.textContent = data.streak ?? 0;
  } catch (e) {}
}

// ===== 無料プランのライフ表示（❤️3個・2時間で1回復） =====
function renderLivesDisplay(status) {
  const el = document.getElementById('livesDisplay');
  if (!el) return;

  if (!status) {
    el.innerHTML = '<div class="lives-hearts">👑 無制限</div>';
    el.style.display = 'block';
    return;
  }

  const hearts = Array.from({ length: status.max_lives }, (_, i) =>
    i < status.lives ? '❤️' : '🖤'
  ).join('');

  let timerHtml = '';
  if (status.next_recovery_seconds != null) {
    const mins = Math.max(1, Math.ceil(status.next_recovery_seconds / 60));
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    const timerText = h > 0 ? `次の回復まで${h}時間${m}分` : `次の回復まで${m}分`;
    timerHtml = `<div class="lives-timer">${timerText}</div>`;
  }

  el.innerHTML = `<div class="lives-hearts">${hearts}</div>${timerHtml}`;
  el.style.display = 'block';
}

async function refreshLives() {
  try {
    const res = await fetch('/api/me');
    if (!res.ok) return;
    const data = await res.json();
    renderLivesDisplay(data.lives_status);
  } catch (e) {}
}


// ===== 起動 =====
applySubjectColor(currentSubject);

async function init() {
  try {
    const res = await fetch('/api/me');
    if (!res.ok) { window.location.href = '/login'; return; }
    const user = await res.json();
    teacherInput.value = user.name;
    document.getElementById('userDisplay').textContent = `ログイン中: ${user.name}`;
    updateStatus(user.total_clears || 0);
    subjectStats = user.subject_clears || {};
    updateGapsBadge(user.due_reviews || 0);
    renderLivesDisplay(user.lives_status);
    setInterval(refreshLives, 60000);

    if (!user.has_security_question) {
      document.getElementById('securityBtn').style.display = 'block';
    }

    if (user.role === 'teacher') {
      const dashBtn = document.createElement('button');
      dashBtn.className = 'logout-btn';
      dashBtn.style.cssText = 'border-color:#58CC02;color:#58CC02;';
      dashBtn.textContent = '📊 ダッシュボード';
      dashBtn.addEventListener('click', () => { window.location.href = '/dashboard'; });
      document.querySelector('.user-info').insertBefore(dashBtn, document.getElementById('logoutBtn'));
    }

    updateStatsDisplay();
    fetchStreak();

    // 苦手ノートの「復習する」・学習のきろくの「続きから再開する」から来た場合の処理
    const params        = new URLSearchParams(location.search);
    const reviewParam   = params.get('review');
    const resumeParam   = params.get('resume');
    const dateParam     = params.get('date');
    const sessionIdParam = params.get('session_id');
    const subjectParam  = params.get('subject') || resumeParam;

    if (reviewParam || resumeParam) {
      reviewGapId = reviewParam ? (parseInt(reviewParam, 10) || null) : null;
      const btn = subjectParam && document.querySelector(`.subject[data-subject="${subjectParam}"]`);
      if (btn) {
        document.querySelectorAll('.subject').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentSubject = btn.dataset.subject;
        currentEmoji   = btn.dataset.emoji;
        updateCharPanel(currentSubject, currentEmoji);
      }
    }

    if (resumeParam && dateParam) {
      resumeSession(resumeParam, dateParam, sessionIdParam);
    } else if (reviewGapId) {
      startSession();
    } else {
      // 先生からの課題があれば、通常の単元選択より先に見せる
      let pendingAssignments = [];
      try {
        const aRes = await fetch('/api/assignments');
        if (aRes.ok) pendingAssignments = (await aRes.json()).assignments || [];
      } catch (e) {}

      if (pendingAssignments.length > 0) {
        showAssignmentPicker(pendingAssignments);
      } else {
        showUnitPicker(currentSubject);
      }
    }
  } catch (e) {
    window.location.href = '/login';
  }
}

init();
