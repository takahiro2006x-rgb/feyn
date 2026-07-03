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

let sessionKey     = null;
let isBusy         = false;
let startAbortCtrl = null;
let currentSubject = '物理';
let currentEmoji   = '⚡';
let reviewGapId    = null;  // 苦手ノートからの復習セッション中はギャップIDが入る

const SUBJECT_COLORS = {
  '物理': { color: '#3B82F6', dark: '#1D4ED8' },
  '数学': { color: '#8B5CF6', dark: '#6D28D9' },
  '英語': { color: '#F97316', dark: '#C2410C' },
  '化学': { color: '#10B981', dark: '#047857' },
  '生物': { color: '#84CC16', dark: '#4D7C0F' },
  '国語': { color: '#EF4444', dark: '#B91C1C' },
  '歴史': { color: '#B45309', dark: '#78350F' },
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
  if (reviewGapId) settings.gap_id = reviewGapId;
  return settings;
}

function escText(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}


// ===== メッセージを追加 =====
function addMessage(role, text) {
  const el = document.createElement('div');
  el.classList.add('message', role, 'pop-in');
  el.innerHTML = role === 'feyn'
    ? `<div class="avatar">${currentEmoji}</div><div class="bubble">${text}</div>`
    : `<div class="bubble">${text}</div>`;
  messagesEl.appendChild(el);
  el.scrollIntoView({ behavior: 'smooth' });
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


// ===== セッション開始 =====
async function startSession() {
  if (startAbortCtrl) startAbortCtrl.abort();
  startAbortCtrl = new AbortController();
  const signal = startAbortCtrl.signal;

  messagesEl.innerHTML = '';
  sessionKey = null;
  resetXP();
  setExpression('normal');

  const thinkingEl = addThinking();
  const settings   = getSettings();

  try {
    const response = await fetch('/api/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(settings),
      signal,
    });
    const data = await response.json();
    thinkingEl.remove();
    if (data.error) { addMessage('feyn', `⚠️ ${data.error}`); return; }
    sessionKey = data.session_key;
    addMessage('feyn', data.reply);
  } catch (e) {
    if (e.name !== 'AbortError') {
      thinkingEl.remove();
      addMessage('feyn', '⚠️ 接続エラーが発生しました。');
    } else {
      thinkingEl.remove();
    }
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
      addMessage('feyn', data.reply);
      setTimeout(() => {
        launchConfetti();
        playCompleteSound();
      }, 400);
      fetch('/api/complete', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ subject: currentSubject, difficulty: diffSelect.value, gap_id: reviewGapId }),
      }).then(async (res) => {
        const wasReview = !!reviewGapId;
        reviewGapId = null;
        history.replaceState(null, '', '/');

        // ギャップ分析の結果を「今日の気づき」として表示
        const c = await res.json().catch(() => ({}));
        if (wasReview) {
          addMessage('feyn', '✅ この苦手は<strong>解決済み</strong>にしたよ！ <a href="/gaps" style="color:inherit;">📝 苦手ノートを見る</a>');
        }
        if (c.analysis && c.analysis.gaps && c.analysis.gaps.length > 0) {
          const items = c.analysis.gaps.map(g => `・${escText(g.description)}`).join('<br>');
          addMessage('feyn',
            `📝 <strong>今日の気づき</strong>（テーマ: ${escText(c.analysis.topic)}）<br>${items}<br>` +
            `<a href="/gaps" style="color:inherit;">→ 苦手ノートに記録したよ。あとで復習しよう！</a>`);
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
    history.replaceState(null, '', '/');
    updateCharPanel(currentSubject, currentEmoji);
    startSession();
  });
});


// ===== リセットボタン =====
resetBtn.addEventListener('click', () => {
  reviewGapId = null;
  history.replaceState(null, '', '/');
  startSession();
});


// ===== 送信ボタン / Ctrl+Enter =====
sendBtn.addEventListener('click', sendMessage);
textarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.ctrlKey) {
    e.preventDefault();
    sendMessage();
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


// ===== 学習のきろく =====
document.getElementById('historyBtn').addEventListener('click', () => {
  window.location.href = '/history';
});


// ===== 苦手ノート =====
document.getElementById('gapsBtn').addEventListener('click', () => {
  window.location.href = '/gaps';
});


// ===== ログアウト =====
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  window.location.href = '/login';
});


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
  const btn = document.getElementById('gapsBtn');
  if (btn) btn.textContent = count > 0 ? `📝 苦手ノート 🔴${count}` : '📝 苦手ノート';
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

    // 苦手ノートの「復習する」から来た場合は復習モードで開始する
    const params       = new URLSearchParams(location.search);
    const reviewParam  = params.get('review');
    const subjectParam = params.get('subject');
    if (reviewParam) {
      reviewGapId = parseInt(reviewParam, 10) || null;
      const btn = subjectParam && document.querySelector(`.subject[data-subject="${subjectParam}"]`);
      if (btn) {
        document.querySelectorAll('.subject').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentSubject = btn.dataset.subject;
        currentEmoji   = btn.dataset.emoji;
        updateCharPanel(currentSubject, currentEmoji);
      }
    }

    startSession();
  } catch (e) {
    window.location.href = '/login';
  }
}

init();
