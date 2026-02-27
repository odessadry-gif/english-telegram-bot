import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm";

const CFG = window.GAME_CONFIG;
const tg = window.Telegram?.WebApp;

function safeReadyTelegram() {
  try {
    tg?.ready?.();
    tg?.expand?.();
  } catch (_) {}
}
safeReadyTelegram();

function getTgUser() {
  const u = tg?.initDataUnsafe?.user;
  if (!u) return null;
  return {
    user_id: u.id,
    username: u.username || null,
    first_name: u.first_name || null
  };
}

function roundLabel() {
  if (!CFG.roundEndsAt) return `Round #${CFG.roundId} · live`;
  const ms = new Date(CFG.roundEndsAt) - new Date();
  const days = Math.max(0, Math.ceil(ms / 86400000));
  return `Round #${CFG.roundId} · ${days} days left`;
}

// ===== Supabase =====
const hasSupabase =
  typeof CFG.SUPABASE_URL === "string" && CFG.SUPABASE_URL.startsWith("http") &&
  typeof CFG.SUPABASE_ANON_KEY === "string" && CFG.SUPABASE_ANON_KEY.length > 20;

const supabase = hasSupabase ? createClient(CFG.SUPABASE_URL, CFG.SUPABASE_ANON_KEY) : null;

// ===== DOM =====
const $ = (sel) => document.querySelector(sel);
const root = $("#main");

// ===== Utils =====
function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function formatMMSS(total) {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function render(html) {
  root.innerHTML = html;
}

// ===== Question pool =====
function buildPool() {
  const levels = Array.isArray(CFG.levels) ? CFG.levels : ["A1", "A2", "B1"];
  return window.QUESTIONS.filter(q => levels.includes(q.level));
}

function normalizeQ(q) {
  // defensive: support both `correctIndex` and old `correct`
  const correctIndex = Number.isInteger(q.correctIndex) ? q.correctIndex : (Number.isInteger(q.correct) ? q.correct : 0);
  return {
    level: q.level || "A1",
    word: String(q.word || "").trim(),
    clue: String(q.clue || "").trim(),
    options: Array.isArray(q.options) ? q.options.map(String) : [],
    correctIndex
  };
}

function mixOptions(q) {
  // shuffle options but keep correct index accurate
  const items = q.options.map((text, idx) => ({ text, idx }));
  const mixed = shuffle(items);
  const newCorrectIndex = mixed.findIndex(x => x.idx === q.correctIndex);
  return {
    ...q,
    options: mixed.map(x => x.text),
    correctIndex: newCorrectIndex
  };
}

function buildRunQuestions() {
  const pool = shuffle(buildPool().map(normalizeQ)).filter(q => q.options.length === 4);
  const picked = pool.slice(0, CFG.totalWords);
  return picked.map(mixOptions);
}

// ===== Game state =====
let run = null;

const SCORE_FAST = 100;       // <5s
const SCORE_NORMAL = 60;      // >=5s
const PENALTY_HINT = 30;      // -30 per hint
const PENALTY_WRONG = 10;     // -10
const BONUS_FINISH = 200;     // +200 if finished all words

function startNewRun() {
  const user = getTgUser();
  if (!user) {
    render(notInTelegramScreen());
    return;
  }

  run = {
    user,
    questions: buildRunQuestions(),
    idx: 0,
    score: 0,
    hintsLeft: CFG.maxHints,
    startTs: Date.now(),
    qStartTs: Date.now(),
    timerId: null,
    revealed: new Set() // letter indexes (for current word)
  };

  showCountdown();
}

// ===== Screens =====
function lobbyScreen() {
  return `
    <div class="page-label">${roundLabel()}</div>
    <div class="card glass">
      <div class="lobby">
        <div class="logo">⚡</div>
        <div class="h1">WORD RUSH</div>
        <div class="sub">Guess ${CFG.totalWords} words in ${Math.floor(CFG.timeTotalSec/60)} minutes</div>

        <div class="rules">
          <div class="rule">⏱ <span><b>${Math.floor(CFG.timeTotalSec/60)} minutes</b> total</span></div>
          <div class="rule">📖 <span>Read the clue — choose the word</span></div>
          <div class="rule">💡 <span>Up to <b>${CFG.maxHints} hints</b> per run</span></div>
          <div class="rule">⭐ <span>Faster answer = <b>more points</b></span></div>
          <div class="rule">🏆 <span>Top-${CFG.topSize} leaderboard</span></div>
        </div>

        <button class="btn" id="btnStart">Play →</button>
      </div>
    </div>
  `;
}

function notInTelegramScreen() {
  return `
    <div class="page-label">${roundLabel()}</div>
    <div class="card glass">
      <div class="lobby">
        <div class="logo">⚡</div>
        <div class="h1">WORD RUSH</div>
        <div class="sub">Open this game inside Telegram</div>
        <div class="rules">
          <div class="rule">✅ <span>Open via the WebApp button in your bot/group</span></div>
        </div>
      </div>
    </div>
  `;
}

function countdownScreen(n) {
  return `
    <div class="page-label">Get ready</div>
    <div class="card glass">
      <div class="countdown">
        <div class="small" style="margin-bottom:20px">Starting in</div>
        <div class="ring"><div class="big" id="cdNum">${n}</div></div>
        <div class="small" style="margin-top:16px;color:rgba(77,141,255,.75)">Go!</div>
      </div>
    </div>
  `;
}

function gameScreen() {
  const q = run.questions[run.idx];
  const left = getRemainingSeconds();
  return `
    <div class="page-label">${roundLabel()}</div>
    <div class="card glass">
      <div class="game">
        <div class="toprow">
          <span class="counter">Word ${run.idx + 1} / ${CFG.totalWords} · ${q.level}</span>
          <span class="timer" id="timerVal">${formatMMSS(left)}</span>
        </div>
        <div class="bar"><div class="fill" id="timerFill" style="width:${(left / CFG.timeTotalSec) * 100}%"></div></div>

        <div style="margin-bottom:12px">
          <span class="badge">⭐ <span id="scoreVal">${run.score}</span> pts <span class="delta" id="deltaVal"></span></span>
        </div>

        <div class="clue">
          <div class="label">CLUE</div>
          <div class="text">${escapeHtml(q.clue)}</div>
        </div>

        <div class="word" id="wordBoxes"></div>

        <div class="grid" id="optGrid">
          ${q.options.map((t, i) => `<button class="opt" data-i="${i}">${escapeHtml(t)}</button>`).join("")}
        </div>

        <button class="hintbtn" id="hintBtn">${hintLabel()}</button>
      </div>
    </div>
  `;
}

function resultsScreen({ topRows, finalScore, wordsSolved, secondsLeft, outOfTime }) {
  const rowsHtml = (topRows || []).map((r, i) => {
    const name = r.username ? `@${r.username}` : (r.first_name || `User ${r.user_id}`);
    const you = r.user_id === run.user.user_id;
    return `
      <div class="row">
        <span class="rank ${i < 3 ? "gold" : ""}">${i + 1}</span>
        <span class="name">${escapeHtml(name)}${you ? " · you" : ""}</span>
        <span class="pts">${r.score} pts</span>
      </div>
    `;
  }).join("");

  const note = hasSupabase ? "" : `<div class="rsub" style="margin-top:-6px;color:rgba(255,215,64,.7)">Demo leaderboard (Supabase not connected yet)</div>`;

  return `
    <div class="page-label">${roundLabel()}</div>
    <div class="card glass">
      <div class="results">
        <div class="trophy">🏆</div>
        <div class="rtitle">${outOfTime ? "Time's up!" : "Nice run!"}</div>
        <div class="rsub">Solved: ${wordsSolved}/${CFG.totalWords} · Time left: ${secondsLeft}s</div>
        ${note}

        <div class="stats">
          <div class="sbox"><div class="sval gold">${finalScore}</div><div class="slabel">POINTS</div></div>
          <div class="sbox"><div class="sval green">${wordsSolved}/${CFG.totalWords}</div><div class="slabel">WORDS</div></div>
          <div class="sbox"><div class="sval">${secondsLeft}s</div><div class="slabel">LEFT</div></div>
        </div>

        <div class="lb">
          <div class="ttl">Top ${CFG.topSize}</div>
          ${rowsHtml || `<div style="color:rgba(180,200,255,.65);font-size:13px">No scores yet</div>`}
        </div>

        <button class="btn-green" id="btnAgain">Play again</button>
      </div>
    </div>
  `;
}

// ===== Timer =====
function getRemainingSeconds() {
  const elapsed = Math.floor((Date.now() - run.startTs) / 1000);
  return clamp(CFG.timeTotalSec - elapsed, 0, CFG.timeTotalSec);
}

function startTimer() {
  run.timerId = setInterval(() => {
    const left = getRemainingSeconds();
    updateTimerUI(left);
    if (left <= 0) finishRun(true);
  }, 250);
}

function updateTimerUI(secondsLeft) {
  const elTimer = $("#timerVal");
  const elFill = $("#timerFill");
  if (!elTimer || !elFill) return;

  elTimer.textContent = formatMMSS(secondsLeft);
  elFill.style.width = `${(secondsLeft / CFG.timeTotalSec) * 100}%`;

  if (secondsLeft <= 20) {
    elTimer.classList.add("urgent");
    elFill.classList.add("urgent");
  } else {
    elTimer.classList.remove("urgent");
    elFill.classList.remove("urgent");
  }
}

// ===== Letters / hints =====
function renderLetters(word) {
  const letters = word.toUpperCase().split("");
  const html = letters.map((ch, i) => {
    const revealed = run.revealed.has(i);
    const cls = revealed ? "box hint" : "box empty";
    return `<div class="${cls}">${revealed ? escapeHtml(ch) : "_"}</div>`;
  }).join("");
  $("#wordBoxes").innerHTML = html;
}

function hintLabel() {
  return run.hintsLeft > 0 ? `Hint (left: ${run.hintsLeft})` : `No hints left`;
}

function useHint() {
  if (run.hintsLeft <= 0) return;

  const q = run.questions[run.idx];
  const word = q.word || q.options[q.correctIndex];
  const len = word.length;

  const candidates = [];
  for (let i = 0; i < len; i++) {
    if (!run.revealed.has(i) && word[i] !== " ") candidates.push(i);
  }
  if (candidates.length === 0) return;

  const idx = candidates[Math.floor(Math.random() * candidates.length)];
  run.revealed.add(idx);
  run.hintsLeft -= 1;

  run.score = Math.max(0, run.score - PENALTY_HINT);
  $("#scoreVal").textContent = String(run.score);
  $("#deltaVal").textContent = `(-${PENALTY_HINT})`;

  renderLetters(word);

  const hb = $("#hintBtn");
  hb.textContent = hintLabel();
  if (run.hintsLeft <= 0) hb.disabled = true;
}

// ===== Answer picking =====
function scoreForSpeed(sec) {
  return sec < 5 ? SCORE_FAST : SCORE_NORMAL;
}

function onPickOption(pickedIndex, btn) {
  const q = run.questions[run.idx];
  const correct = pickedIndex === q.correctIndex;

  const buttons = [...$("#optGrid").querySelectorAll("button")];
  buttons.forEach(b => b.classList.add("disabled"));

  if (correct) {
    const sec = (Date.now() - run.qStartTs) / 1000;
    const add = scoreForSpeed(sec);
    run.score += add;
    $("#scoreVal").textContent = String(run.score);
    $("#deltaVal").textContent = `(+${add})`;
    btn.classList.remove("disabled");
    btn.classList.add("correct");

    // show solved letters
    const word = (q.word || q.options[q.correctIndex]).toUpperCase();
    $("#wordBoxes").innerHTML = word.split("").map(ch => `<div class="box correct">${escapeHtml(ch)}</div>`).join("");

    setTimeout(() => {
      run.idx++;
      if (run.idx >= run.questions.length) finishRun(false);
      else showGame();
    }, 650);
  } else {
    run.score = Math.max(0, run.score - PENALTY_WRONG);
    $("#scoreVal").textContent = String(run.score);
    $("#deltaVal").textContent = `(-${PENALTY_WRONG})`;

    btn.classList.remove("disabled");
    btn.classList.add("wrong");

    setTimeout(() => {
      // allow retry on same word: enable others, keep wrong disabled
      buttons.forEach((b) => {
        if (b === btn) b.classList.add("disabled");
        else b.classList.remove("disabled");
      });
    }, 450);
  }
}

// ===== Supabase IO =====
async function submitScore(finalScore) {
  if (!supabase) return;

  const payload = {
    round_id: CFG.roundId,
    user_id: run.user.user_id,
    username: run.user.username,
    first_name: run.user.first_name,
    score: finalScore
  };

  await supabase.from("leaderboard").insert(payload);
}

async function fetchTop() {
  if (!supabase) return [];

  const { data } = await supabase
    .from("leaderboard")
    .select("user_id, username, first_name, score, created_at")
    .eq("round_id", CFG.roundId)
    .order("score", { ascending: false })
    .order("created_at", { ascending: true })
    .limit(CFG.topSize);

  return data || [];
}

// ===== Flow =====
function showLobby() {
  render(lobbyScreen());
  $("#btnStart").addEventListener("click", startNewRun);
}

function showCountdown() {
  let n = 3;
  render(countdownScreen(n));
  const el = $("#cdNum");

  const id = setInterval(() => {
    n--;
    if (n <= 0) {
      clearInterval(id);
      showGame();
      return;
    }
    el.textContent = String(n);
  }, 850);
}

function showGame() {
  run.revealed = new Set();

  render(gameScreen());
  const q = run.questions[run.idx];
  const word = (q.word || q.options[q.correctIndex]).toUpperCase();
  renderLetters(word);

  // bind options
  $("#optGrid").querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => onPickOption(parseInt(btn.dataset.i, 10), btn));
  });

  // bind hint
  const hb = $("#hintBtn");
  hb.addEventListener("click", useHint);
  hb.textContent = hintLabel();
  hb.disabled = run.hintsLeft <= 0;

  // question timer start
  run.qStartTs = Date.now();

  // global timer once
  if (!run.timerId) startTimer();
}

async function finishRun(outOfTime) {
  clearInterval(run.timerId);

  const wordsSolved = outOfTime ? run.idx : CFG.totalWords;
  const secondsLeft = getRemainingSeconds();

  let finalScore = run.score;
  if (!outOfTime && wordsSolved === CFG.totalWords) finalScore += BONUS_FINISH;

  try { await submitScore(finalScore); } catch (_) {}
  let topRows = [];
  try { topRows = await fetchTop(); } catch (_) {}

  render(resultsScreen({ topRows, finalScore, wordsSolved, secondsLeft, outOfTime }));
  $("#btnAgain").addEventListener("click", () => {
    // reset timer
    run.timerId = null;
    startNewRun();
  });
}

// boot
showLobby();
