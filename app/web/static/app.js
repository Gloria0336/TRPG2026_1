"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const NAME_ZH = {
  "Bram Ironwood": "布拉姆·鐵木",
  "Lyra Dawnbringer": "萊拉·曦光使者",
  "Goblin": "哥布林",
  "Grix the Goblin Boss": "哥布林首領葛利克斯",
  "Old Perrin": "老佩林",
  "Dawnbridge": "晨橋村",
  "The Dawnbridge Caravan": "晨橋商隊",
  "The Gilded Tankard": "鎏金酒杯酒館",
  "GM": "GM",
};

const ABILITY_ZH = {
  STR: "力量",
  DEX: "敏捷",
  CON: "體質",
  INT: "智力",
  WIS: "感知",
  CHA: "魅力",
};

const CONDITION_ZH = {
  unconscious: "昏迷",
  stable: "穩定",
  dead: "死亡",
};

const SKILL_ZH = {
  Acrobatics: "特技",
  "Animal Handling": "馴獸",
  Arcana: "奧秘",
  Athletics: "運動",
  Deception: "欺瞞",
  History: "歷史",
  Insight: "洞悉",
  Intimidation: "威嚇",
  Investigation: "調查",
  Medicine: "醫藥",
  Nature: "自然",
  Perception: "察覺",
  Performance: "表演",
  Persuasion: "說服",
  Religion: "宗教",
  "Sleight Of Hand": "巧手",
  Stealth: "隱匿",
  Survival: "求生",
};

const ACTION_ZH = {
  Longsword: "長劍",
  "Heavy Crossbow": "重弩",
  "Second Wind": "回氣",
  Mace: "硬頭鎚",
  "Sacred Flame": "聖焰",
  "Guiding Bolt": "曳光彈",
  "Cure Wounds": "治療傷口",
  "Healing Word": "治療真言",
  Scimitar: "彎刀",
  Shortbow: "短弓",
  "Scimitar Flurry": "彎刀連擊",
  Javelin: "標槍",
};

function translateName(name) {
  const raw = String(name ?? "");
  if (NAME_ZH[raw]) return NAME_ZH[raw];
  return raw.replace(/\bGoblin (\d+)\b/g, "哥布林 $1");
}

function translateAction(name) {
  return ACTION_ZH[name] || name;
}

function translateText(text) {
  let out = String(text ?? "");
  for (const [en, zh] of Object.entries(NAME_ZH)) {
    out = out.replaceAll(en, zh);
  }
  out = out.replace(/\bGoblin (\d+)\b/g, "哥布林 $1");
  for (const [en, zh] of Object.entries(ACTION_ZH)) {
    out = out.replaceAll(en, zh);
  }
  out = out
    .replaceAll("The party is victorious!", "隊伍獲得勝利！")
    .replaceAll("The party has fallen...", "隊伍倒下了...")
    .replaceAll("Combat begins! Initiative:", "戰鬥開始！先攻順序：")
    .replaceAll("Combat ends", "戰鬥結束")
    .replaceAll("victory", "勝利")
    .replaceAll("defeat", "敗北")
    .replaceAll("ended", "已結束")
    .replaceAll("SUCCESS", "成功")
    .replaceAll("FAILURE", "失敗")
    .replaceAll("HIT", "命中")
    .replaceAll("MISS", "未命中")
    .replaceAll("SAVED", "豁免成功")
    .replaceAll("FAILED save", "豁免失敗")
    .replaceAll("critical!", "重擊！")
    .replaceAll("crit!", "大成功！")
    .replaceAll("fumble!", "大失敗！")
    .replaceAll("check vs DC", "檢定，DC")
    .replaceAll("attacks", "攻擊")
    .replaceAll("with", "使用")
    .replaceAll("casts", "施放")
    .replaceAll("on", "目標")
    .replaceAll("takes", "受到")
    .replaceAll("damage", "點傷害")
    .replaceAll("heals", "恢復")
    .replaceAll("HP", "生命值")
    .replaceAll("Death save", "死亡豁免")
    .replaceAll("death save", "死亡豁免")
    .replaceAll("success", "成功")
    .replaceAll("failure", "失敗");
  for (const [en, zh] of Object.entries(SKILL_ZH)) {
    out = out.replaceAll(en, zh);
  }
  return out;
}

function setStatus(online) {
  const el = $("status");
  el.textContent = online ? "即時連線" : "離線";
  el.className = "status " + (online ? "online" : "offline");
}

function renderAiStatus(ai) {
  const dot = $("ai-status-dot");
  const text = $("ai-status-text");
  const detail = $("ai-status-detail");
  const status = ai?.status || "unknown";
  const online = status === "online";
  const warning = status === "offline" || status === "missing_key";
  dot.className = `dot ${online ? "online" : warning ? "warning" : "offline"}`;
  const labels = {
    online: "已連線",
    offline: "離線模式",
    missing_key: "缺少 API Key",
    error: "連線異常",
    unknown: "未知",
  };
  text.textContent = labels[status] || status;
  const latency = ai?.latency_ms == null ? "" : ` ・ ${ai.latency_ms} ms`;
  const models = ai ? `Intent: ${ai.model_intent} ・ Narrate: ${ai.model_narrate}` : "";
  detail.textContent = `${ai?.message || ""}${latency}${models ? " ・ " + models : ""}`;
}

function renderClaims(state) {
  const chars = (state?.characters || []).filter((c) => c.is_pc);
  const el = $("player-claims");
  if (!chars.length) {
    el.innerHTML = `<div class="empty">尚未建立角色</div>`;
    return;
  }
  el.innerHTML = chars.map((c) => {
    const claim = c.claim;
    const cls = claim ? "claimed" : "open";
    const status = claim ? `已選擇：${esc(claim.display_name)}` : "尚未被選擇";
    return `<div class="claim ${cls}">
      <span class="claim-name">${esc(c.portrait || "")} ${esc(translateName(c.name))}</span>
      <span class="claim-status">${status}</span>
    </div>`;
  }).join("");
}

function render(state) {
  renderAiStatus(state?.ai);
  renderClaims(state);
  if (!state || !state.started) {
    $("scene-title").textContent = "尚未開始遊戲";
    $("scene-summary").innerHTML = "在 Discord 使用 <code>/start</code> 開始一場冒險。";
    $("scene-npcs").innerHTML = "";
    $("characters").innerHTML = "";
    $("combat").classList.add("hidden");
    $("log").innerHTML = "";
    return;
  }

  const scene = state.scene || {};
  $("scene-title").textContent = translateText(scene.title || "場景");
  $("scene-summary").className = "scene-summary";
  $("scene-summary").textContent = translateText(scene.summary || "");
  $("scene-npcs").innerHTML = (scene.npcs || []).map((n) => `<span>${esc(translateText(n))}</span>`).join("");

  $("characters").innerHTML = (state.characters || []).map(renderCard).join("");
  renderCombat(state);
  $("log").innerHTML = (state.log || []).map(renderLog).join("");
}

function renderCard(c) {
  const pct = Math.max(0, Math.min(100, Math.round((c.hp / Math.max(1, c.max_hp)) * 100)));
  const low = pct <= 35 ? " low" : "";
  const down = c.hp <= 0 ? " down" : "";
  const npc = c.is_pc ? "" : " npc";
  const conds = (c.conditions || []).length
    ? `<div class="conds">${c.conditions.map((x) => `<span>${esc(CONDITION_ZH[x] || x)}</span>`).join("")}</div>` : "";
  const abil = Object.entries(c.abilities || {})
    .map(([k, v]) => `${ABILITY_ZH[k] || k} ${v}`).join(" ・ ");
  const claim = c.is_pc
    ? `<div class="claim-badge ${c.claim ? "claimed" : "open"}">${c.claim ? `已選擇：${esc(c.claim.display_name)}` : "尚未被選擇"}</div>`
    : "";
  return `
    <div class="card${down}${npc}">
      <div class="name">${esc(c.portrait || "")} <span>${esc(translateName(c.name))}</span>
        <span class="lvl">等級 ${c.level}</span></div>
      ${claim}
      <div class="blurb">${esc(translateText(c.blurb || ""))}</div>
      <div class="hpbar${low}"><div style="width:${pct}%"></div>
        <span class="hptext">${c.hp} / ${c.max_hp} 生命值</span></div>
      <div class="meta"><span>護甲 ${c.ac}</span><span>${esc(abil)}</span></div>
      ${conds}
    </div>`;
}

function renderCombat(state) {
  const cs = state.combat;
  const panel = $("combat");
  if (!cs) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  $("combat-round").textContent = cs.active ? `第 ${cs.round} 回合` : translateText(cs.outcome || "已結束");
  $("initiative").innerHTML = (cs.order || []).map((o) => {
    const ch = (state.characters || []).find((c) => c.id === o.id) || {};
    const active = cs.active && o.id === cs.current_id ? " active" : "";
    const dead = ch.conditions && ch.conditions.includes("dead") ? " dead" : "";
    const hp = ch.hp <= 0 ? "倒下" : `${ch.hp}/${ch.max_hp}`;
    return `<li class="${active}${dead}">
      <span class="init">${o.init}</span>
      <span>${esc(ch.portrait || "")} ${esc(translateName(o.name))}</span>
      <span class="hp">${hp}</span></li>`;
  }).join("");
}

function renderLog(e) {
  let cls = "";
  if (e.actor === "GM" || e.kind === "scene" || e.kind === "combat" || e.kind === "system") cls = "system";
  const d = e.summary || "";
  if (/SUCCESS|HIT|defeated|victor|成功|命中|擊敗|勝利/i.test(d)) cls = cls || "success";
  if (/FAIL|MISS|dies|fallen|失敗|未命中|死亡|倒下/i.test(d)) cls = cls || "failure";
  const narration = e.narration ? `<div class="narration">${esc(translateText(e.narration))}</div>` : "";
  const actor = e.actor && e.actor !== "GM" ? `<span class="actor">${esc(translateName(e.actor))}</span> ・ ` : "";
  return `<li class="${cls}"><div class="summary">${actor}${esc(translateText(e.summary))}</div>${narration}</li>`;
}

function connect() {
  fetch("/api/state").then((r) => r.json()).then(render).catch(() => {});
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (ev) => { setStatus(true); render(JSON.parse(ev.data)); });
  es.onopen = () => setStatus(true);
  es.onerror = () => { setStatus(false); };
}

connect();
