"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
));

const API_BASE = (() => {
  const params = new URLSearchParams(window.location.search);
  const fromQuery = params.get("api");
  if (fromQuery) {
    const normalized = fromQuery.replace(/\/+$/, "");
    window.localStorage.setItem("trpgPortalApiBase", normalized);
    return normalized;
  }
  const fromConfig = window.TRPG_PORTAL_API_BASE || "";
  const fromStorage = window.localStorage.getItem("trpgPortalApiBase") || "";
  return String(fromConfig || fromStorage).replace(/\/+$/, "");
})();

let portalState = null;

function apiUrl(path) {
  return API_BASE ? `${API_BASE}${path}` : path;
}

function statusText(status) {
  const labels = {
    available: "待接",
    awaiting_check: "待檢定",
    accepted: "已接取",
    completed: "完成",
    failed: "失敗",
    expired: "過期",
  };
  return labels[status] || status || "未知";
}

function setApiStatus(online) {
  const el = $("api-status");
  el.textContent = online ? "API 已連線" : "API 未連線";
  el.className = `pill ${online ? "online" : "offline"}`;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || body.error || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function renderIdentity(state) {
  const viewer = state?.viewer;
  const status = state?.player_status || {};
  $("login-btn").classList.toggle("hidden", Boolean(viewer));
  $("logout-btn").classList.toggle("hidden", !viewer);
  if (!viewer) {
    $("avatar").innerHTML = "?";
    $("player-name").textContent = "訪客";
    $("player-status").textContent = "登入後可認領角色、查看個人狀態，或建立新角色。";
    $("turn-card").textContent = "目前沒有行動提示";
    $("turn-card").className = "turn-card";
    return;
  }

  const displayName = viewer.global_name || viewer.username || viewer.id;
  $("player-name").textContent = displayName;
  if (viewer.avatar) {
    $("avatar").innerHTML = `<img alt="" src="https://cdn.discordapp.com/avatars/${esc(viewer.id)}/${esc(viewer.avatar)}.png?size=128" />`;
  } else {
    $("avatar").textContent = displayName.slice(0, 1).toUpperCase();
  }

  const pc = status.character;
  $("player-status").textContent = pc
    ? `目前角色：${pc.name}，生命值 ${pc.hp}/${pc.max_hp}，護甲 ${pc.ac}`
    : "尚未綁定角色，可在角色頁認領或建立新角色。";

  const turn = status.turn;
  if (turn?.is_yours) {
    $("turn-card").textContent = "輪到你行動。回到 Discord 輸入 /action 推進劇情。";
    $("turn-card").className = "turn-card active";
  } else if (turn?.current_pc_id) {
    $("turn-card").textContent = `等待 ${turn.current_pc_id} 行動。`;
    $("turn-card").className = "turn-card";
  } else {
    $("turn-card").textContent = "目前沒有行動提示";
    $("turn-card").className = "turn-card";
  }
}

function renderCampaign(state) {
  const campaign = state?.campaign || {};
  const scene = campaign.scene;
  $("campaign-line").textContent = campaign.started && scene
    ? `${scene.title} · ${scene.summary || "冒險進行中"}`
    : "尚未開始冒險。";
}

function renderQuests(state) {
  const quests = state?.quests || [];
  $("quest-count").textContent = `${quests.length} 個任務`;
  if (!quests.length) {
    $("quests").innerHTML = `<div class="empty">目前沒有任務。</div>`;
    return;
  }
  $("quests").innerHTML = quests.map((quest) => {
    const tags = Object.values(quest.tags || {})
      .filter(Boolean)
      .map((tag) => `<span>${esc(tag)}</span>`)
      .join("");
    const detail = quest.details?.next_steps?.length
      ? `<div class="tags">${quest.details.next_steps.map((x) => `<span>${esc(x)}</span>`).join("")}</div>`
      : "";
    return `<article class="quest ${esc(quest.status)}">
      <div class="quest-head">
        <strong>${esc(quest.title || "未命名任務")}</strong>
        <span class="muted">${esc(statusText(quest.status))}</span>
      </div>
      <p>${esc(quest.summary || quest.objective || "沒有任務描述。")}</p>
      <div class="muted">${esc(quest.giver || "未知委託人")}${quest.reward ? ` · 報酬：${esc(quest.reward)}` : ""}</div>
      ${tags ? `<div class="tags">${tags}</div>` : ""}
      ${detail}
    </article>`;
  }).join("");
}

function renderCharacters(state) {
  const viewer = state?.viewer;
  const mine = state?.player_status?.claimed_pc_id;
  const characters = state?.characters || [];
  if (!characters.length) {
    $("characters").innerHTML = `<div class="empty">目前沒有可用角色。</div>`;
    return;
  }
  $("characters").innerHTML = characters.map((pc) => {
    const claim = pc.claim;
    const isMine = pc.id === mine;
    const canClaim = viewer && (!claim || isMine);
    const action = isMine
      ? `<button type="button" disabled>已綁定</button>`
      : `<button type="button" data-claim="${esc(pc.id)}" ${canClaim ? "" : "disabled"}>${claim ? "已被認領" : "認領角色"}</button>`;
    const abilities = Object.entries(pc.abilities || {})
      .map(([key, value]) => `<span>${esc(key)} ${esc(value)}</span>`)
      .join("");
    return `<article class="character ${isMine ? "mine" : ""}">
      <div class="character-head">
        <strong>${esc(pc.portrait || "")} ${esc(pc.name)}</strong>
        <span class="muted">Lv ${esc(pc.level)}</span>
      </div>
      <p>${esc(pc.blurb || "")}</p>
      <div class="stats">
        <span>HP ${esc(pc.hp)}/${esc(pc.max_hp)}</span>
        <span>AC ${esc(pc.ac)}</span>
        ${abilities}
      </div>
      <p>${claim ? `玩家：${esc(claim.display_name)}` : "尚未被選擇"}</p>
      ${action}
    </article>`;
  }).join("");
}

function renderAll(state) {
  portalState = state;
  setApiStatus(true);
  renderCampaign(state);
  renderIdentity(state);
  renderQuests(state);
  renderCharacters(state);
}

async function refresh() {
  try {
    const state = await fetchJson("/api/portal/me");
    renderAll(state);
  } catch (error) {
    setApiStatus(false);
    $("campaign-line").textContent = API_BASE
      ? `無法讀取 API：${error.message}`
      : "尚未設定 API。可用 ?api=https://你的後端網址 指定。";
  }
}

async function claimCharacter(pcId) {
  await fetchJson(`/api/portal/characters/${encodeURIComponent(pcId)}/claim`, {
    method: "POST",
    body: "{}",
  }).then(renderAll);
}

async function createCharacter(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  $("form-message").textContent = "建立中...";
  try {
    const state = await fetchJson("/api/portal/characters", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    form.reset();
    form.elements.portrait.value = "星";
    $("form-message").textContent = "角色已建立並綁定。";
    renderAll(state);
    activateTab("characters");
  } catch (error) {
    $("form-message").textContent = error.message;
  }
}

function activateTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.add("hidden");
  }
  $(`${name}-panel`).classList.remove("hidden");
}

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.matches(".tab")) {
    activateTab(target.dataset.tab);
  }
  if (target.id === "login-btn") {
    window.location.href = apiUrl("/api/portal/auth/discord/login");
  }
  if (target.id === "logout-btn") {
    fetchJson("/api/portal/auth/logout", { method: "POST", body: "{}" }).then(refresh);
  }
  const pcId = target.dataset.claim;
  if (pcId) {
    claimCharacter(pcId).catch((error) => {
      $("campaign-line").textContent = error.message;
    });
  }
});

$("character-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!portalState?.viewer) {
    $("form-message").textContent = "請先用 Discord 登入。";
    return;
  }
  createCharacter(event.currentTarget);
});

refresh();
window.setInterval(refresh, 5000);
