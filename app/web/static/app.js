"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function setStatus(online) {
  const el = $("status");
  el.textContent = online ? "● live" : "offline";
  el.className = "status " + (online ? "online" : "offline");
}

function render(state) {
  if (!state || !state.started) {
    $("scene-title").textContent = "No active game";
    $("scene-summary").innerHTML = "Start a session in Discord with <code>/start</code>.";
    $("scene-npcs").innerHTML = "";
    $("characters").innerHTML = "";
    $("combat").classList.add("hidden");
    $("log").innerHTML = "";
    return;
  }

  // Scene
  const scene = state.scene || {};
  $("scene-title").textContent = scene.title || "Scene";
  $("scene-summary").className = "scene-summary";
  $("scene-summary").textContent = scene.summary || "";
  $("scene-npcs").innerHTML = (scene.npcs || []).map((n) => `<span>${esc(n)}</span>`).join("");

  // Characters
  $("characters").innerHTML = (state.characters || []).map(renderCard).join("");

  // Combat
  renderCombat(state);

  // Log
  $("log").innerHTML = (state.log || []).map(renderLog).join("");
}

function renderCard(c) {
  const pct = Math.max(0, Math.min(100, Math.round((c.hp / Math.max(1, c.max_hp)) * 100)));
  const low = pct <= 35 ? " low" : "";
  const down = c.hp <= 0 ? " down" : "";
  const npc = c.is_pc ? "" : " npc";
  const conds = (c.conditions || []).length
    ? `<div class="conds">${c.conditions.map((x) => `<span>${esc(x)}</span>`).join("")}</div>` : "";
  const abil = Object.entries(c.abilities || {})
    .map(([k, v]) => `${k} ${v}`).join(" · ");
  return `
    <div class="card${down}${npc}">
      <div class="name">${esc(c.portrait || "•")} <span>${esc(c.name)}</span>
        <span class="lvl">Lv ${c.level}</span></div>
      <div class="blurb">${esc(c.blurb || "")}</div>
      <div class="hpbar${low}"><div style="width:${pct}%"></div>
        <span class="hptext">${c.hp} / ${c.max_hp} HP</span></div>
      <div class="meta"><span>AC ${c.ac}</span><span>${esc(abil)}</span></div>
      ${conds}
    </div>`;
}

function renderCombat(state) {
  const cs = state.combat;
  const panel = $("combat");
  if (!cs) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden");
  $("combat-round").textContent = cs.active ? `— Round ${cs.round}` : `— ${cs.outcome || "ended"}`;
  $("initiative").innerHTML = (cs.order || []).map((o) => {
    const ch = (state.characters || []).find((c) => c.id === o.id) || {};
    const active = cs.active && o.id === cs.current_id ? " active" : "";
    const dead = ch.conditions && ch.conditions.includes("dead") ? " dead" : "";
    const hp = ch.hp <= 0 ? "🩸" : `${ch.hp}/${ch.max_hp}`;
    return `<li class="${active}${dead}">
      <span class="init">${o.init}</span>
      <span>${esc(ch.portrait || "")} ${esc(o.name)}</span>
      <span class="hp">${hp}</span></li>`;
  }).join("");
}

function renderLog(e) {
  let cls = "";
  if (e.actor === "GM" || e.kind === "scene" || e.kind === "combat" || e.kind === "system") cls = "system";
  const d = e.summary || "";
  if (/SUCCESS|HIT|defeated|victor/i.test(d)) cls = cls || "success";
  if (/FAIL|MISS|dies|fallen/i.test(d)) cls = cls || "failure";
  const narration = e.narration ? `<div class="narration">${esc(e.narration)}</div>` : "";
  const actor = e.actor && e.actor !== "GM" ? `<span class="actor">${esc(e.actor)}</span> · ` : "";
  return `<li class="${cls}"><div class="summary">${actor}${esc(e.summary)}</div>${narration}</li>`;
}

function connect() {
  fetch("/api/state").then((r) => r.json()).then(render).catch(() => {});
  const es = new EventSource("/api/stream");
  es.addEventListener("state", (ev) => { setStatus(true); render(JSON.parse(ev.data)); });
  es.onopen = () => setStatus(true);
  es.onerror = () => { setStatus(false); };
}

connect();
