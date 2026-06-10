const policies = ["balanced", "farming", "war", "logistics", "defense", "diplomacy"];
const policyLabels = {
  balanced: "均衡",
  farming: "屯田",
  war: "征伐",
  logistics: "转运",
  defense: "固守",
  diplomacy: "外交",
};

const typeLabels = {
  army: "军",
  worker: "田官",
  scout: "斥候",
  caravan: "辎重",
};

const actionLabels = {
  rest: "休整",
  move: "移动",
  attack: "进攻",
  defend: "固守",
  scout: "侦察",
  farm: "屯田",
  transfer: "转运",
  reinforce: "增援",
  retreat: "撤退",
};

const routeLabels = {
  plain_road: "官道",
  mountain_pass: "山道",
  shu_road: "蜀道",
  river: "水陆",
  frontier: "边道",
};

const resourceLabels = {
  food: "粮",
  weapons: "兵",
  gold: "金",
  manpower: "丁",
  intel: "报",
};

let game = null;
let selectedPolicy = "balanced";
let orderDrafts = [];
let diplomacyDrafts = [];
let realMapBitmap = null;
let realMapSignature = "";
let portraitImages = {};
let animationStartedAt = 0;
let animationFrame = null;
let phaserGame = null;
let phaserScene = null;
let pendingPhaserRender = false;
let phaserMapSignature = "";
let phaserPortraitLoading = false;
let lastGeneralPositions = {};
const phaserPortraitKeys = new Set();

const elements = {
  canvas: document.getElementById("mapCanvas"),
  roundLine: document.getElementById("roundLine"),
  newGameBtn: document.getElementById("newGameBtn"),
  saveBtn: document.getElementById("saveBtn"),
  resolveBtn: document.getElementById("resolveBtn"),
  codexAdvisorBtn: document.getElementById("codexAdvisorBtn"),
  applyAdvisorBtn: document.getElementById("applyAdvisorBtn"),
  clearOrdersBtn: document.getElementById("clearOrdersBtn"),
  allyWuBtn: document.getElementById("allyWuBtn"),
  breakWuBtn: document.getElementById("breakWuBtn"),
  allyWeiBtn: document.getElementById("allyWeiBtn"),
  breakWeiBtn: document.getElementById("breakWeiBtn"),
  strategyInput: document.getElementById("strategyInput"),
  policyButtons: document.getElementById("policyButtons"),
  unitList: document.getElementById("unitList"),
  orderDrafts: document.getElementById("orderDrafts"),
  advisorSummary: document.getElementById("advisorSummary"),
  advisorList: document.getElementById("advisorList"),
  factionList: document.getElementById("factionList"),
  intentList: document.getElementById("intentList"),
  battleList: document.getElementById("battleList"),
  logList: document.getElementById("logList"),
  statusPill: document.getElementById("statusPill"),
  winnerBanner: document.getElementById("winnerBanner"),
  edictText: document.getElementById("edictText"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function startGame() {
  setStatus("开局");
  game = await api("/api/games", {
    method: "POST",
    body: JSON.stringify({ player_faction: "liu_bei" }),
  });
  selectedPolicy = game.current_player_policy || "balanced";
  orderDrafts = [];
  diplomacyDrafts = [];
  render();
  setStatus("待命");
}

async function saveCommand() {
  if (!game) return;
  setStatus("保存");
  game = await api(`/api/games/${game.game_id}/command`, {
    method: "POST",
    body: JSON.stringify(commandPayload()),
  });
  render();
  setStatus("已存");
}

async function resolveRound() {
  if (!game || game.finished) return;
  setStatus("推演");
  await saveCommand();
  game = await api(`/api/games/${game.game_id}/resolve`, { method: "POST" });
  selectedPolicy = game.current_player_policy || selectedPolicy;
  orderDrafts = [];
  diplomacyDrafts = [];
  render();
  setStatus(game.finished ? "终局" : "待命");
}

async function requestLocalCodexAdvisor() {
  if (!game) return;
  setStatus("本地Codex推演");
  elements.codexAdvisorBtn.disabled = true;
  elements.codexAdvisorBtn.textContent = "推演中";
  try {
    await saveCommand();
    game = await api(`/api/games/${game.game_id}/codex-advisor`, { method: "POST" });
    render();
    setStatus(game.advisor_source === "local_codex" ? "Codex建议" : "规则接管");
  } finally {
    elements.codexAdvisorBtn.disabled = false;
    elements.codexAdvisorBtn.textContent = "本地Codex军师";
  }
}

function commandPayload() {
  return {
    strategy_text: elements.strategyInput.value,
    policy: selectedPolicy,
    orders: orderDrafts,
    diplomacy: diplomacyDrafts,
  };
}

function setStatus(text) {
  elements.statusPill.textContent = text;
}

function render() {
  if (!game) return;
  window.__hwsimGame = game;
  elements.roundLine.textContent = `第${game.round} / ${game.max_rounds}回合`;
  elements.strategyInput.value = game.current_player_command || elements.strategyInput.value;
  elements.edictText.textContent = game.finished
    ? `${game.factions[game.winner]?.name || "天下"}定鼎，一百回合之争落幕。`
    : `蜀汉军议：${elements.strategyInput.value || "联吴或联魏皆可，守要地、夺城池，敌不灭则战不止。"}`;
  renderPolicies();
  renderAdvisor();
  renderUnits();
  renderDrafts();
  renderFactions();
  renderIntents();
  renderBattles();
  renderLogs();
  drawMap();
  if (!phaserGame) startAnimations();
  renderWinner();
}

function renderAdvisor() {
  const recommendation = game.advisor_recommendation || {};
  const orders = recommendation.orders || [];
  const diplomacy = recommendation.diplomacy || [];
  const sourceLabels = {
    deterministic: "规则军师",
    local_codex: "本地Codex",
    local_codex_fallback: "规则接管",
  };
  const source = sourceLabels[game.advisor_source] || "规则军师";
  const error = game.advisor_error ? ` 错误：${shortText(game.advisor_error, 90)}` : "";
  elements.advisorSummary.textContent = `${source}：${recommendation.summary || "军师正在等待新的局势。"}${error}`;
  elements.advisorList.innerHTML = "";
  const policyRow = document.createElement("article");
  policyRow.className = "advisor-row";
  policyRow.innerHTML = `<strong>政策</strong><span>${policyLabels[recommendation.policy] || recommendation.policy || "均衡"}</span>`;
  elements.advisorList.appendChild(policyRow);
  [...orders.slice(0, 10).map((order) => orderLabel(order)), ...diplomacy.map((order) => diplomacyLabel(order))].forEach((label) => {
    const row = document.createElement("article");
    row.className = "advisor-row";
    row.innerHTML = `<strong>建议</strong><span>${label}</span>`;
    elements.advisorList.appendChild(row);
  });
  if (orders.length > 10) {
    const row = document.createElement("article");
    row.className = "advisor-row";
    row.innerHTML = `<strong>还有</strong><span>${orders.length - 10} 条默认防务/补给建议</span>`;
    elements.advisorList.appendChild(row);
  }
}

function applyAdvisorRecommendation() {
  if (!game?.advisor_recommendation) return;
  const recommendation = game.advisor_recommendation;
  selectedPolicy = recommendation.policy || selectedPolicy;
  orderDrafts = cloneDrafts(recommendation.orders || []);
  diplomacyDrafts = cloneDrafts(recommendation.diplomacy || []);
  renderPolicies();
  renderDrafts();
  renderAdvisor();
  setStatus("已采用");
}

function cloneDrafts(items) {
  return items.map((item) => JSON.parse(JSON.stringify(item)));
}

function shortText(text, maxLength) {
  if (!text || text.length <= maxLength) return text || "";
  return `${text.slice(0, maxLength - 1)}…`;
}

function renderPolicies() {
  elements.policyButtons.innerHTML = "";
  policies.forEach((policy) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = policyLabels[policy];
    button.className = policy === selectedPolicy ? "active" : "";
    button.addEventListener("click", () => {
      selectedPolicy = policy;
      renderPolicies();
    });
    elements.policyButtons.appendChild(button);
  });
}

function renderUnits() {
  const player = game.player_faction;
  const units = game.units.filter((unit) => unit.faction_id === player);
  const generals = generalMap();
  elements.unitList.innerHTML = "";
  units.forEach((unit) => {
    const card = document.createElement("article");
    card.className = "unit-card";
    const city = cityMap()[unit.city_id];
    const general = unit.general_id ? generals[unit.general_id] : null;
    const top = document.createElement("div");
    top.className = "unit-top";
    const title = general ? `${general.name_cn}` : `${typeLabels[unit.unit_type]} ${unit.id.split("_").at(-1)}`;
    const soldiers = unit.unit_type === "army" ? ` · ${formatSoldiers(unit.soldiers)}` : "";
    top.innerHTML = `<span>${title}${soldiers}</span><span class="unit-meta">${city?.name_cn || unit.region_id} · ${unit.readiness}%</span>`;
    card.appendChild(top);

    if (unit.unit_type === "army") {
      const chooser = document.createElement("div");
      chooser.className = "general-order";
      const select = document.createElement("select");
      cityTargetsFor(unit).forEach((cityOption) => {
        const option = document.createElement("option");
        option.value = cityOption.id;
        const road = roadBetween(unit.city_id, cityOption.id);
        option.textContent = `${cityOption.name_cn} · ${game.factions[game.city_owners[cityOption.id]]?.name || ""}${road ? ` · ${routeLabels[road.route_type] || road.route_type}` : ""}`;
        select.appendChild(option);
      });
      chooser.appendChild(select);
      const preview = document.createElement("div");
      preview.className = "route-preview";
      const refreshPreview = () => {
        preview.textContent = routePreviewLabel(unit, select.value);
      };
      select.addEventListener("change", refreshPreview);
      refreshPreview();
      chooser.appendChild(preview);
      const row = document.createElement("div");
      row.className = "action-row";
      const actions = [
        { label: "移动", build: () => cityMoveOrder(unit, select.value) },
        { label: "进攻", build: () => cityAttackOrder(unit, [select.value]) },
        { label: "连战", build: () => cityAttackOrder(unit, chainTargets(unit, select.value)) },
        { label: "增援", build: () => reinforceOrder(unit) },
        { label: "固守", build: () => ({ unit_id: unit.id, general_id: unit.general_id, action: "defend", source_city_id: unit.city_id, region_id: unit.region_id }) },
      ];
      const actionButtons = [];
      actions.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.label;
        button.addEventListener("click", () => addOrder(item.build()));
        actionButtons.push([button, item]);
        row.appendChild(button);
      });
      const refreshButtons = () => {
        actionButtons.forEach(([button, item]) => {
          const built = item.build();
          button.disabled = !built || (!select.value && ["移动", "进攻", "连战"].includes(item.label));
        });
      };
      select.addEventListener("change", refreshButtons);
      refreshButtons();
      chooser.appendChild(row);
      card.appendChild(chooser);
    } else {
      const row = document.createElement("div");
      row.className = "action-row";
      actionButtonsFor(unit).forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.label;
        button.disabled = !item.order;
        button.addEventListener("click", () => {
          if (item.order) addOrder(item.order);
        });
        row.appendChild(button);
      });
      card.appendChild(row);
    }
    elements.unitList.appendChild(card);
  });
}

function actionButtonsFor(unit) {
  if (unit.unit_type === "army") {
    return [
      { label: "移动", order: moveOrder(unit) },
      { label: "进攻", order: attackOrder(unit) },
      { label: "增援", order: reinforceOrder(unit) },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  if (unit.unit_type === "worker") {
    return [
      { label: "移动", order: moveOrder(unit) },
      { label: "屯田", order: { unit_id: unit.id, action: "farm", region_id: unit.region_id } },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  if (unit.unit_type === "scout") {
    return [
      { label: "移动", order: moveOrder(unit) },
      { label: "侦察", order: scoutOrder(unit) },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  return [
    { label: "移动", order: moveOrder(unit) },
    { label: "武器", order: transferOrder(unit, "weapons") },
    { label: "粮草", order: transferOrder(unit, "food") },
    { label: "赏金", order: transferOrder(unit, "gold") },
  ];
}

function moveOrder(unit) {
  const target = firstFriendlyCity(unit.city_id, unit.faction_id);
  if (!target) return null;
  return cityMoveOrder(unit, target);
}

function cityMoveOrder(unit, target) {
  if (!target || !roadBetween(unit.city_id, target)) return null;
  const owner = game.city_owners[target];
  if (owner !== unit.faction_id && !isAllied(unit.faction_id, owner)) return null;
  return { unit_id: unit.id, general_id: unit.general_id, action: "move", source_city_id: unit.city_id, target_city_id: target };
}

function attackOrder(unit) {
  const target = firstEnemyCity(unit.city_id, unit.faction_id);
  if (target) return cityAttackOrder(unit, [target]);
  const regionTarget = firstEnemyNeighbor(unit.region_id, unit.faction_id);
  if (!regionTarget) return null;
  return { unit_id: unit.id, general_id: unit.general_id, action: "attack", target_region_id: regionTarget };
}

function cityAttackOrder(unit, targets) {
  const cleanTargets = targets.filter(Boolean);
  if (!cleanTargets.length) return null;
  const firstOwner = game.city_owners[cleanTargets[0]];
  if (firstOwner === unit.faction_id || isAllied(unit.faction_id, firstOwner)) return null;
  return { unit_id: unit.id, general_id: unit.general_id, action: "attack", source_city_id: unit.city_id, target_city_ids: cleanTargets };
}

function reinforceOrder(unit) {
  const battle = (game.active_battles || []).find((item) => {
    if (![item.attacker_faction, item.defender_faction].includes(unit.faction_id)) return false;
    if (item.attacker_unit_ids?.includes(unit.id) || item.defender_unit_ids?.includes(unit.id)) {
      return item.attacker_faction === unit.faction_id && item.odds < 0.22
        ? true
        : false;
    }
    const city = cityMap()[unit.city_id];
    return city && city.neighbors.includes(item.target_city_id);
  });
  if (!battle) return null;
  if (battle.attacker_unit_ids?.includes(unit.id) || battle.defender_unit_ids?.includes(unit.id)) {
    return { unit_id: unit.id, general_id: unit.general_id, action: "retreat", battle_id: battle.id };
  }
  return { unit_id: unit.id, general_id: unit.general_id, action: "reinforce", battle_id: battle.id, source_city_id: unit.city_id, target_city_id: battle.target_city_id };
}

function cityTargetsFor(unit) {
  const city = cityMap()[unit.city_id];
  if (!city) return [];
  return city.neighbors
    .map((id) => cityMap()[id])
    .filter(Boolean)
    .sort((a, b) => {
      const aEnemy = game.city_owners[a.id] !== unit.faction_id && !isAllied(unit.faction_id, game.city_owners[a.id]);
      const bEnemy = game.city_owners[b.id] !== unit.faction_id && !isAllied(unit.faction_id, game.city_owners[b.id]);
      return Number(bEnemy) - Number(aEnemy) || a.name_cn.localeCompare(b.name_cn, "zh-Hans-CN");
    });
}

function chainTargets(unit, firstTarget) {
  const result = [firstTarget].filter(Boolean);
  const first = cityMap()[firstTarget];
  if (!first) return result;
  const second = first.neighbors.find((id) => game.city_owners[id] !== unit.faction_id && !result.includes(id) && !isAllied(unit.faction_id, game.city_owners[id]));
  if (second) result.push(second);
  return result;
}

function firstEnemyCity(cityId, factionId) {
  const city = cityMap()[cityId];
  if (!city) return null;
  return city.neighbors.find((neighbor) => {
    const owner = game.city_owners[neighbor];
    return owner && owner !== factionId && !isAllied(factionId, owner);
  }) || null;
}

function firstFriendlyCity(cityId, factionId) {
  const city = cityMap()[cityId];
  if (!city) return null;
  return city.neighbors.find((neighbor) => {
    const owner = game.city_owners[neighbor];
    return owner === factionId || isAllied(factionId, owner);
  }) || null;
}

function scoutOrder(unit) {
  const cityTarget = firstEnemyCity(unit.city_id, unit.faction_id) || cityMap()[unit.city_id]?.neighbors?.[0];
  if (cityTarget) return { unit_id: unit.id, action: "scout", source_city_id: unit.city_id, target_city_id: cityTarget };
  const target = firstEnemyNeighbor(unit.region_id, unit.faction_id) || firstNeighbor(unit.region_id);
  if (!target) return null;
  return { unit_id: unit.id, action: "scout", target_region_id: target };
}

function transferOrder(unit, resource) {
  const target = firstSupplyTargetCity(unit) || unit.city_id;
  if (target) return { unit_id: unit.id, action: "transfer", source_city_id: unit.city_id, target_city_id: target, resource, amount: 16 };
  const regionTarget = firstBorderRegion(unit.faction_id) || unit.region_id;
  return { unit_id: unit.id, action: "transfer", target_region_id: regionTarget, resource, amount: 16 };
}

function firstSupplyTargetCity(unit) {
  const city = cityMap()[unit.city_id];
  if (!city) return null;
  const candidates = city.neighbors
    .filter((neighbor) => {
      const owner = game.city_owners[neighbor];
      return owner === unit.faction_id || isAllied(unit.faction_id, owner);
    })
    .map((neighbor) => {
      const enemyEdges = cityMap()[neighbor]?.neighbors?.filter((edge) => {
        const owner = game.city_owners[edge];
        return owner && owner !== unit.faction_id && !isAllied(unit.faction_id, owner);
      }).length || 0;
      const road = roadBetween(unit.city_id, neighbor);
      const cost = road ? road.food_cost * 9 + road.gold_cost * 6 + road.readiness_cost + road.soldier_loss_bps / 10 : 999;
      return { cityId: neighbor, score: enemyEdges * 30 - cost };
    })
    .sort((a, b) => b.score - a.score || a.cityId.localeCompare(b.cityId));
  return candidates[0]?.cityId || null;
}

function addOrder(order) {
  orderDrafts = orderDrafts.filter((item) => item.unit_id !== order.unit_id);
  orderDrafts.push(order);
  renderDrafts();
}

function addDiplomacy(order) {
  diplomacyDrafts = diplomacyDrafts.filter((item) => item.type !== order.type || item.target !== order.target);
  diplomacyDrafts.push(order);
  renderDrafts();
}

function renderDrafts() {
  elements.orderDrafts.innerHTML = "";
  [...orderDrafts.map((order, index) => ({ kind: "order", order, index })), ...diplomacyDrafts.map((order, index) => ({ kind: "diplomacy", order, index }))].forEach((draft) => {
    const row = document.createElement("div");
    row.className = "draft";
    const label = draft.kind === "order" ? orderLabel(draft.order) : diplomacyLabel(draft.order);
    row.innerHTML = `<span>${label}</span>`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "撤";
    remove.addEventListener("click", () => {
      if (draft.kind === "order") orderDrafts.splice(draft.index, 1);
      else diplomacyDrafts.splice(draft.index, 1);
      renderDrafts();
    });
    row.appendChild(remove);
    elements.orderDrafts.appendChild(row);
  });
}

function orderLabel(order) {
  const regions = regionMap();
  const cities = cityMap();
  const targetCity = order.target_city_id || order.target_city_ids?.at?.(-1);
  const target = targetCity || order.target_region_id || order.region_id;
  const cityName = targetCity && cities[targetCity] ? cities[targetCity].name_cn : "";
  const regionName = target && regions[target] ? regions[target].name_cn : "";
  const resource = order.resource ? ` ${resourceLabels[order.resource]}` : "";
  const unit = game?.units?.find((item) => item.id === order.unit_id);
  const general = unit?.general_id ? generalMap()[unit.general_id] : null;
  const unitName = general?.name_cn || supportUnitLabel(unit) || order.unit_id;
  const battle = order.battle_id ? (game.active_battles || []).find((item) => item.id === order.battle_id) : null;
  const battleName = battle ? cityMap()[battle.target_city_id]?.name_cn : "";
  return `${unitName}: ${actionLabels[order.action]}${resource}${cityName || regionName || battleName ? ` -> ${cityName || regionName || battleName}` : ""}`;
}

function supportUnitLabel(unit) {
  if (!unit) return "";
  const number = unit.id?.split("_").at(-1) || "";
  return `${typeLabels[unit.unit_type] || "部队"}${number}`;
}

function diplomacyLabel(order) {
  const faction = game.factions[order.target]?.name || order.target;
  return `${order.type === "propose_alliance" ? "结盟" : "破盟"} · ${faction}`;
}

function renderFactions() {
  const rows = Object.values(game.factions).sort((a, b) => b.score - a.score);
  elements.factionList.innerHTML = "";
  rows.forEach((faction, index) => {
    const row = document.createElement("article");
    row.className = "faction-row";
    row.innerHTML = `
      <div class="faction-title">
        <span><span class="swatch" style="background:${faction.color}"></span>${index + 1}. ${faction.name}</span>
        <span>${faction.region_count}州/${faction.unit_count}部</span>
      </div>
      <div class="resource-grid">
        <span>${resourceLabels.food}${faction.resources.food}</span>
        <span>${resourceLabels.weapons}${faction.resources.weapons}</span>
        <span>${resourceLabels.gold}${faction.resources.gold}</span>
        <span>${resourceLabels.manpower}${faction.resources.manpower}</span>
        <span>${resourceLabels.intel}${faction.resources.intel}</span>
      </div>
    `;
    elements.factionList.appendChild(row);
  });
}

function renderIntents() {
  elements.intentList.innerHTML = "";
  Object.values(game.factions).forEach((faction) => {
    const row = document.createElement("article");
    row.className = "intent-row";
    row.innerHTML = `<strong>${faction.name}</strong><br>${faction.ai_intent || "待下回合定策。"}`;
    elements.intentList.appendChild(row);
  });
}

function renderBattles() {
  if (!elements.battleList) return;
  const active = [...(game.active_battles || [])].reverse();
  const battles = [...(game.battle_events || [])].reverse().slice(0, 6);
  elements.battleList.innerHTML = "";
  if (!battles.length && !active.length) {
    const row = document.createElement("article");
    row.className = "battle-row";
    row.textContent = "等待交战。";
    elements.battleList.appendChild(row);
    return;
  }
  active.forEach((battle) => {
    const city = cityMap()[battle.target_city_id];
    const row = document.createElement("article");
    row.className = "battle-row active-battle";
    row.innerHTML = `<strong>${city?.name_cn || battle.target_city_id}鏖战中</strong><br>
      ${game.factions[battle.attacker_faction]?.name || battle.attacker_faction} 对阵 ${game.factions[battle.defender_faction]?.name || battle.defender_faction} · ${battle.elapsed_rounds}/${battle.duration_rounds}回合 · 胜势${Math.round((battle.odds || 0.5) * 100)}%<br>
      <span>${battle.summary || "双方仍可增援或撤退。"}</span>`;
    elements.battleList.appendChild(row);
  });
  battles.forEach((battle) => {
    const attacker = generalMap()[battle.attacker_general_id];
    const defender = battle.defender_general_id ? generalMap()[battle.defender_general_id] : null;
    const city = cityMap()[battle.target_city_id];
    const row = document.createElement("article");
    row.className = "battle-row";
    row.innerHTML = `<strong>${city?.name_cn || battle.target_city_id}</strong> · ${attacker?.name_cn || battle.attacker_general_id} 对阵 ${defender?.name_cn || "守军"}<br>
      ${formatSoldiers(battle.attacker_before)}→${formatSoldiers(battle.attacker_after)} / ${formatSoldiers(battle.defender_before)}→${formatSoldiers(battle.defender_after)} · 胜率${Math.round(battle.win_probability * 100)}%<br>
      <span>${battle.summary}</span>`;
    elements.battleList.appendChild(row);
  });
}

function renderLogs() {
  const logs = [...game.logs].reverse().slice(0, 28);
  const headline = logs.find((log) => log.title !== "Income") || logs[0];
  const ordered = headline ? [headline, ...logs.filter((log) => log !== headline)] : logs;
  elements.logList.innerHTML = "";
  ordered.forEach((log) => {
    const row = document.createElement("article");
    row.className = "log-row";
    row.dataset.tone = log.tone;
    row.innerHTML = `<strong>第${log.round}回合 · ${localizeLogTitle(log.title)}</strong><br>${localizeLogDetail(log.detail)}`;
    elements.logList.appendChild(row);
  });
}

function localizeLogTitle(title) {
  const titles = {
    "Round 1": "回合开始",
    "Policy shift": "政策变更",
    "Food spent": "粮草支出",
    "Defense set": "布防",
    "Transfer rejected": "转运被拒",
    "Scout rejected": "侦察被拒",
    "Farms expanded": "屯田扩建",
    "Scout report": "侦察回报",
    "Attack halted": "进军受阻",
    "City battle": "城池交战",
    "City spoils": "夺城缴获",
    "Income": "收入",
    "Recruitment": "募兵",
    "Treaty expired": "盟约期满",
    "AI fallback": "军机接管",
    "Order rejected": "军令被拒",
    "Attack rejected": "进攻被拒",
    "Defend rejected": "固守被拒",
    "Farm rejected": "屯田被拒",
    "Rest": "休整",
  };
  if (title?.startsWith("Round ")) return "回合开始";
  return titles[title] || title;
}

function localizeLogDetail(detail) {
  return String(detail || "")
    .replaceAll("shifts from", "由")
    .replaceAll("to", "改为")
    .replaceAll("spends", "支出")
    .replaceAll("food for", "粮用于")
    .replaceAll("from", "自")
    .replaceAll("fortifies", "固守")
    .replaceAll("guards nearby friendly cities, and steadies local farms", "并护卫邻近友城、安定屯田")
    .replaceAll("improves fields around", "整修田亩于")
    .replaceAll("cannot reach the target this round", "本回合无法抵达目标")
    .replaceAll("has no adjacent target", "没有邻近目标")
    .replaceAll("takes", "攻下")
    .replaceAll("holds", "守住")
    .replaceAll("retreats to", "退往")
    .replaceAll("outcome", "结局")
    .replaceAll("Spoils", "缴获")
    .replaceAll("food", "粮")
    .replaceAll("weapons", "兵械")
    .replaceAll("gold", "金")
    .replaceAll("manpower", "丁壮");
}

function renderWinner() {
  if (!game.finished || !game.winner) {
    elements.winnerBanner.hidden = true;
    return;
  }
  elements.winnerBanner.hidden = false;
  elements.winnerBanner.textContent = `${game.factions[game.winner].name}定鼎天下 · ${game.round}回合`;
}

function drawMap() {
  if (!window.Phaser) {
    elements.canvas.textContent = "Phaser 运行时未加载。";
    return;
  }
  ensurePhaserGame();
  if (!phaserScene?.ready) {
    pendingPhaserRender = true;
    return;
  }
  renderPhaserWorld();
}

function ensurePhaserGame() {
  if (phaserGame) {
    resizePhaserGame();
    return;
  }
  const rect = elements.canvas.getBoundingClientRect();
  phaserGame = new Phaser.Game({
    type: Phaser.AUTO,
    parent: elements.canvas,
    width: Math.max(320, Math.floor(rect.width || window.innerWidth)),
    height: Math.max(480, Math.floor(rect.height || window.innerHeight)),
    backgroundColor: "#251f19",
    render: { antialias: true, pixelArt: false, roundPixels: false },
    scale: { mode: Phaser.Scale.NONE },
    scene: {
      create() {
        phaserScene = this;
        this.ready = true;
        this.layers = {
          map: this.add.container(0, 0),
          influence: this.add.container(0, 0),
          roads: this.add.container(0, 0),
          labels: this.add.container(0, 0),
          units: this.add.container(0, 0),
          fx: this.add.container(0, 0),
        };
        setupPhaserCameraControls(this);
        if (pendingPhaserRender) {
          pendingPhaserRender = false;
          renderPhaserWorld();
        }
      },
    },
  });
}

function resizePhaserGame() {
  if (!phaserGame) return;
  const rect = elements.canvas.getBoundingClientRect();
  const width = Math.max(320, Math.floor(rect.width || window.innerWidth));
  const height = Math.max(480, Math.floor(rect.height || window.innerHeight));
  phaserGame.scale.resize(width, height);
}

function renderPhaserWorld() {
  if (!phaserScene || !game) return;
  resizePhaserGame();
  ensurePhaserPortraits();
  Object.values(phaserScene.layers).forEach((layer) => layer.removeAll(true));
  phaserScene.cameras.main.setBackgroundColor("#251f19");
  const map = game.real_map || { canvas_size: [1920, 1080] };
  const mapWidth = map.canvas_size[0];
  const mapHeight = map.canvas_size[1];
  drawPhaserMap(mapWidth, mapHeight);
  drawPhaserInfluence();
  drawPhaserStateLabels();
  drawPhaserCityNodes();
  drawPhaserSupply();
  drawPhaserSupportUnits();
  drawPhaserBattles();
  drawPhaserGenerals();
  drawPhaserAttribution(mapHeight);
  fitPhaserCamera(mapWidth, mapHeight);
  playPhaserAnimations();
}

function drawPhaserMap(mapWidth, mapHeight) {
  if (game.real_map?.region_polygons?.length) {
    drawPhaserVectorMap(game.real_map, mapWidth, mapHeight);
    return;
  }
  const scene = phaserScene;
  const textureKey = "sanguo-real-map";
  const bitmap = game.real_map ? realMapToCanvas() : simpleMapToCanvas();
  const signature = game.real_map ? realMapSignature : `simple:${JSON.stringify(game.region_owners)}`;
  if (!scene.textures.exists(textureKey) || phaserMapSignature !== signature) {
    if (scene.textures.exists(textureKey)) scene.textures.remove(textureKey);
    scene.textures.addCanvas(textureKey, bitmap);
    scene.textures.get(textureKey).setFilter(Phaser.Textures.FilterMode.NEAREST);
    phaserMapSignature = signature;
  }
  const backdrop = scene.add.rectangle(mapWidth / 2, mapHeight / 2, mapWidth * 1.6, mapHeight * 1.45, 0x251f19, 1);
  const mapImage = scene.add.image(mapWidth / 2, mapHeight / 2, textureKey).setDisplaySize(mapWidth, mapHeight);
  mapImage.setData("qaRole", "phaser-map");
  scene.layers.map.add([backdrop, mapImage]);
}

function drawPhaserVectorMap(map, mapWidth, mapHeight) {
  const scene = phaserScene;
  const backdrop = scene.add.rectangle(mapWidth / 2, mapHeight / 2, mapWidth * 1.6, mapHeight * 1.45, 0x1d1711, 1);
  const graphics = scene.add.graphics();

  graphics.fillStyle(0x221a12, 1);
  graphics.fillRect(0, 0, mapWidth, mapHeight);

  (map.region_polygons || []).forEach((polygon) => {
    const owner = game.region_owners[polygon.region_id] || provinceOwnerFallback(polygon.region_id);
    const color = Phaser.Display.Color.HexStringToColor(game.factions[owner]?.color || "#777777").color;
    const points = polygonPoints(polygon);
    graphics.fillStyle(color, 0.92);
    graphics.fillPoints(points, true);
  });

  (map.region_polygons || []).forEach((polygon) => {
    const points = polygonPoints(polygon);
    graphics.lineStyle(7, 0x130d08, 0.82);
    graphics.strokePoints(points, true);
  });
  (map.region_polygons || []).forEach((polygon) => {
    const points = polygonPoints(polygon);
    graphics.lineStyle(2.2, 0xe1c27a, 0.9);
    graphics.strokePoints(points, true);
  });

  (map.rivers || []).forEach((line) => {
    drawPhaserPolyline(graphics, line.points, 0x10283a, 8, 0.74);
    drawPhaserPolyline(graphics, line.points, 0x79bfe2, 3.2, 0.86);
  });
  (map.terrain_lines || []).forEach((line) => {
    const color = line.kind === "pass" ? 0xf2c36a : 0xd8b472;
    drawPhaserPolyline(graphics, line.points, 0x120d08, 5.5, 0.62);
    drawPhaserPolyline(graphics, line.points, color, 2.2, 0.82);
    if (line.kind === "mountain") drawMountainTicks(graphics, line.points, color);
  });

  scene.layers.map.add([backdrop, graphics]);
}

function polygonPoints(polygon) {
  return (polygon.points || []).map((point) => ({ x: point[0], y: point[1] }));
}

function drawPhaserPolyline(graphics, points, color, width, alpha) {
  if (!points || points.length < 2) return;
  graphics.lineStyle(width, color, alpha);
  graphics.beginPath();
  graphics.moveTo(points[0][0], points[0][1]);
  points.slice(1).forEach((point) => graphics.lineTo(point[0], point[1]));
  graphics.strokePath();
}

function drawMountainTicks(graphics, points, color) {
  if (!points || points.length < 2) return;
  graphics.lineStyle(1.6, color, 0.74);
  for (let i = 1; i < points.length; i += 1) {
    const [x1, y1] = points[i - 1];
    const [x2, y2] = points[i];
    const dx = x2 - x1;
    const dy = y2 - y1;
    const length = Math.hypot(dx, dy);
    if (length < 24) continue;
    const steps = Math.max(1, Math.floor(length / 46));
    const nx = -dy / length;
    const ny = dx / length;
    for (let step = 1; step <= steps; step += 1) {
      const t = step / (steps + 1);
      const x = x1 + dx * t;
      const y = y1 + dy * t;
      graphics.beginPath();
      graphics.moveTo(x, y);
      graphics.lineTo(x + nx * 11, y + ny * 11);
      graphics.strokePath();
    }
  }
}

function simpleMapToCanvas() {
  const canvas = document.createElement("canvas");
  canvas.width = 1920;
  canvas.height = 1080;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#251f19";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  game.regions.forEach((region) => {
    const owner = game.region_owners[region.id];
    drawPolygon(ctx, region.polygon, 0, 0, 1, game.factions[owner]?.color || "#777", region.id);
  });
  return canvas;
}

function drawPhaserInfluence() {
  const graphics = phaserScene.add.graphics();
  Object.values(cityMap()).forEach((city) => {
    const faction = game.factions[game.city_owners[city.id]];
    if (!faction) return;
    const color = Phaser.Display.Color.HexStringToColor(faction.color).color;
    const radius = Math.max(22, Math.min(68, 20 + city.population * 0.56));
    graphics.fillStyle(color, 0.12);
    graphics.fillCircle(city.position[0], city.position[1], radius);
    graphics.fillStyle(color, 0.08);
    graphics.fillCircle(city.position[0], city.position[1], radius * 0.58);
  });
  phaserScene.layers.influence.add(graphics);
}

function drawPhaserRoads() {
  const graphics = phaserScene.add.graphics();
  const cities = cityMap();
  (game.roads || []).forEach((road) => {
    const city = cities[road.from_city_id];
    const neighbor = cities[road.to_city_id];
    if (!city || !neighbor) return;
    const owner = game.city_owners[city.id];
    const neighborOwner = game.city_owners[neighbor.id];
    const sameOwner = owner === neighborOwner;
    const style = routeStyle(road.route_type, sameOwner ? game.factions[owner]?.color : null);
    graphics.lineStyle(style.width + 2.4, 0x120d08, Math.min(0.72, style.alpha + 0.12));
    graphics.beginPath();
    graphics.moveTo(city.position[0], city.position[1]);
    graphics.lineTo(neighbor.position[0], neighbor.position[1]);
    graphics.strokePath();
    graphics.lineStyle(style.width, style.color, style.alpha);
    graphics.beginPath();
    graphics.moveTo(city.position[0], city.position[1]);
    graphics.lineTo(neighbor.position[0], neighbor.position[1]);
    graphics.strokePath();
  });
  phaserScene.layers.roads.add(graphics);
}

function routeStyle(routeType, ownerColor) {
  if (ownerColor) {
    return { color: Phaser.Display.Color.HexStringToColor(ownerColor).color, width: 2.8, alpha: 0.48 };
  }
  const styles = {
    shu_road: { color: 0xffcc66, width: 4.0, alpha: 0.9 },
    mountain_pass: { color: 0xe4c186, width: 3.2, alpha: 0.78 },
    river: { color: 0x8fd4f0, width: 3.4, alpha: 0.78 },
    frontier: { color: 0xc6aa74, width: 2.8, alpha: 0.66 },
    plain_road: { color: 0xffe09a, width: 2.6, alpha: 0.72 },
  };
  return styles[routeType] || styles.plain_road;
}

function drawPhaserStateLabels() {
  if (!game.real_map?.state_labels) return;
  game.real_map.state_labels.filter((label) => game.region_owners[label.id]).forEach((label) => {
    const text = phaserScene.add.text(label.centroid[0], label.centroid[1], label.name_cn, {
      fontFamily: '"Songti SC", "STSong", serif',
      fontSize: "34px",
      fontStyle: "900",
      color: "#f3df9d",
      stroke: "#000000",
      strokeThickness: 6,
    }).setOrigin(0.5).setAlpha(0.72);
    phaserScene.layers.labels.add(text);
  });
}

function drawPhaserCityNodes() {
  const graphics = phaserScene.add.graphics();
  Object.values(cityMap()).forEach((city) => {
    const owner = game.city_owners[city.id];
    const faction = game.factions[owner];
    const color = Phaser.Display.Color.HexStringToColor(faction?.color || "#777777").color;
    const [x, y] = city.position;
    graphics.fillStyle(0x000000, 0.44);
    graphics.fillCircle(x + 1.8, y + 1.8, 7.2);
    graphics.fillStyle(color, 1);
    graphics.fillCircle(x, y, 5.6);
    graphics.lineStyle(2.2, 0xfff3cc, 0.94);
    graphics.strokeCircle(x, y, 5.6);
    const label = phaserScene.add.text(x, y + 7, city.name_cn, {
      fontFamily: '"Songti SC", "STSong", serif',
      fontSize: "15px",
      fontStyle: "900",
      color: "#fff0c2",
      stroke: "#000000",
      strokeThickness: 4,
    }).setOrigin(0.5, 0).setDepth(6);
    phaserScene.layers.labels.add(label);
  });
  phaserScene.layers.labels.add(graphics);
}

function drawPhaserSupply() {
  const graphics = phaserScene.add.graphics();
  Object.entries(game.city_supply || {}).forEach(([cityId, supply]) => {
    const total = Object.values(supply).reduce((sum, value) => sum + value, 0);
    const city = cityMap()[cityId];
    if (!city || total <= 0) return;
    const width = Math.min(62, 18 + total * 0.62);
    const x = city.position[0] - width / 2;
    const y = city.position[1] + 19;
    graphics.fillStyle(0x080705, 0.78);
    graphics.fillRoundedRect(x, y, width, 7, 3);
    graphics.fillStyle(0xd6b35f, 0.88);
    graphics.fillRoundedRect(x, y, width * 0.72, 7, 3);
  });
  phaserScene.layers.labels.add(graphics);
}

function drawPhaserSupportUnits() {
  const grouped = {};
  game.units.filter((unit) => unit.unit_type !== "army" && unit.city_id).forEach((unit) => {
    grouped[unit.city_id] ||= [];
    grouped[unit.city_id].push(unit);
  });
  Object.entries(grouped).forEach(([cityId, units]) => {
    const city = cityMap()[cityId];
    if (!city) return;
    units.forEach((unit, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, units.length);
      const x = city.position[0] + Math.cos(angle) * 18;
      const y = city.position[1] + Math.sin(angle) * 18 + 16;
      phaserScene.layers.units.add(phaserSupportToken(unit, x, y));
    });
  });
}

function phaserSupportToken(unit, x, y) {
  const faction = game.factions[unit.faction_id];
  const color = Phaser.Display.Color.HexStringToColor(faction?.color || "#777777").color;
  const container = phaserScene.add.container(x, y);
  const graphics = phaserScene.add.graphics();
  graphics.lineStyle(1.3, 0xfff3cc, 0.92);
  graphics.fillStyle(color, 0.96);
  if (unit.unit_type === "worker") {
    graphics.fillRoundedRect(-5, -5, 10, 10, 2);
    graphics.strokeRoundedRect(-5, -5, 10, 10, 2);
  } else if (unit.unit_type === "scout") {
    graphics.fillPoints([{ x: 0, y: -7 }, { x: 7, y: 0 }, { x: 0, y: 7 }, { x: -7, y: 0 }], true);
    graphics.strokePoints([{ x: 0, y: -7 }, { x: 7, y: 0 }, { x: 0, y: 7 }, { x: -7, y: 0 }], true);
  } else {
    graphics.fillCircle(0, 0, 6);
    graphics.strokeCircle(0, 0, 6);
  }
  const label = phaserScene.add.text(0, 10, typeLabels[unit.unit_type] || "部", {
    fontFamily: '"PingFang SC", system-ui, sans-serif',
    fontSize: "10px",
    fontStyle: "800",
    color: "#fff3cc",
    stroke: "#000000",
    strokeThickness: 2,
  }).setOrigin(0.5, 0);
  container.add([graphics, label]);
  return container;
}

function drawPhaserBattles() {
  (game.active_battles || []).forEach((battle) => {
    const city = cityMap()[battle.target_city_id];
    if (!city) return;
    const ring = phaserScene.add.circle(city.position[0], city.position[1], 25, 0xff553e, 0.08)
      .setStrokeStyle(4, 0xffd15a, 0.92);
    const text = phaserScene.add.text(city.position[0], city.position[1] - 34, "战", {
      fontFamily: '"Songti SC", serif',
      fontSize: "20px",
      fontStyle: "900",
      color: "#ffe7a0",
      stroke: "#000000",
      strokeThickness: 4,
    }).setOrigin(0.5);
    phaserScene.layers.fx.add([ring, text]);
    phaserScene.tweens.add({
      targets: ring,
      scale: { from: 0.85, to: 1.22 },
      alpha: { from: 0.98, to: 0.48 },
      duration: 680,
      yoyo: true,
      repeat: -1,
      ease: "Sine.easeInOut",
    });
  });
}

function drawPhaserGenerals() {
  const grouped = {};
  (game.city_stacks || []).forEach((stack) => {
    grouped[stack.city_id] ||= [];
    grouped[stack.city_id].push(stack);
  });
  const nextPositions = {};
  Object.entries(grouped).forEach(([cityId, stacks]) => {
    const city = cityMap()[cityId];
    if (!city) return;
    stacks.forEach((stack, index) => {
      const leader = generalMap()[stack.leader_general_id];
      if (!leader) return;
      const point = generalTokenPoint(city, stacks.length, index);
      const key = `${stack.city_id}:${stack.faction_id}`;
      const previous = lastGeneralPositions[key];
      const token = phaserGeneralToken(leader, point.x, point.y, stack);
      if (previous && (Math.abs(previous.x - point.x) > 1 || Math.abs(previous.y - point.y) > 1)) {
        token.setPosition(previous.x, previous.y);
        phaserScene.tweens.add({ targets: token, x: point.x, y: point.y, duration: 720, ease: "Sine.easeInOut" });
      }
      nextPositions[key] = { x: point.x, y: point.y };
      phaserScene.layers.units.add(token);
    });
  });
  lastGeneralPositions = nextPositions;
}

function generalTokenPoint(city, count, index) {
  const angle = (Math.PI * 2 * index) / Math.max(1, count);
  const orbit = count > 1 ? 21 + Math.floor(index / 5) * 9 : 0;
  return {
    x: city.position[0] + Math.cos(angle) * orbit,
    y: city.position[1] - 18 + Math.sin(angle) * orbit,
  };
}

function phaserGeneralToken(general, x, y, stack = null) {
  const faction = game.factions[general.faction_id];
  const color = Phaser.Display.Color.HexStringToColor(faction?.color || "#777777").color;
  const radius = stack ? Math.max(12, Math.min(24, 10 + Math.sqrt(stack.total_soldiers) / 120 + stack.general_count * 1.3)) : 11;
  const key = phaserPortraitKey(general);
  const container = phaserScene.add.container(x, y);
  const halo = phaserScene.add.circle(0, 0, radius + 2, color, 0.74).setStrokeStyle(1.6, 0xfff3cc, 0.92);
  container.add(halo);
  if (phaserScene.textures.exists(key)) {
    const portrait = phaserScene.add.image(0, 0, key).setDisplaySize(radius * 2, radius * 2).setAlpha(0.96);
    container.add(portrait);
  } else {
    container.add(phaserScene.add.circle(0, 0, radius, color, 0.95));
  }
  const name = general.name_cn.length > 2 ? general.name_cn.slice(0, 2) : general.name_cn;
  const nameText = phaserScene.add.text(0, -1, name, {
    fontFamily: '"Songti SC", "STSong", serif',
    fontSize: "12px",
    fontStyle: "900",
    color: "#fff3cc",
    stroke: "#000000",
    strokeThickness: 3,
  }).setOrigin(0.5);
  const stackLabel = stack && stack.general_count > 1 ? `将x${stack.general_count} · ${formatSoldiers(stack.total_soldiers)}` : formatSoldiers(stack?.total_soldiers || general.soldiers);
  const soldiers = phaserScene.add.text(radius + 6, 0, stackLabel, {
    fontFamily: '"PingFang SC", system-ui, sans-serif',
    fontSize: "13px",
    fontStyle: "900",
    color: "#fff3cc",
    stroke: "#000000",
    strokeThickness: 3,
  }).setOrigin(0, 0.5);
  container.add([nameText, soldiers]);
  return container;
}

function ensurePhaserPortraits() {
  if (!phaserScene || phaserPortraitLoading || !game?.generals) return;
  let queued = 0;
  game.generals.forEach((general) => {
    const key = phaserPortraitKey(general);
    if (!general.portrait_path || phaserPortraitKeys.has(key) || phaserScene.textures.exists(key)) return;
    phaserPortraitKeys.add(key);
    phaserScene.load.svg(key, general.portrait_path, { width: 64, height: 64 });
    queued += 1;
  });
  if (!queued) return;
  phaserPortraitLoading = true;
  phaserScene.load.once("complete", () => {
    phaserPortraitLoading = false;
    renderPhaserWorld();
  });
  phaserScene.load.start();
}

function phaserPortraitKey(general) {
  return `portrait:${general.id}`;
}

function drawPhaserAttribution(mapHeight) {
  if (!game.real_map?.attribution) return;
  const text = phaserScene.add.text(20, mapHeight - 20, game.real_map.attribution, {
    fontFamily: "Georgia, serif",
    fontSize: "13px",
    fontStyle: "700",
    color: "#bfaf8c",
  }).setAlpha(0.38);
  phaserScene.layers.labels.add(text);
}

function fitPhaserCamera(mapWidth, mapHeight) {
  const camera = phaserScene.cameras.main;
  camera.setBounds(0, 0, mapWidth, mapHeight);
  const viewportWidth = phaserGame.scale.width;
  const viewportHeight = phaserGame.scale.height;
  if (camera.userMoved && !camera.needsRefit) return;
  const compact = viewportWidth <= 760;
  const medium = viewportWidth > 760 && viewportWidth <= 1180;
  const zoom = Math.min(viewportWidth / mapWidth, viewportHeight / mapHeight) * (compact ? 0.82 : medium ? 0.9 : 0.98);
  camera.setZoom(Math.max(0.3, zoom));
  camera.centerOn(mapWidth / 2, mapHeight / 2 + (compact ? 110 : medium ? 52 : 0));
  camera.needsRefit = false;
}

function setupPhaserCameraControls(scene) {
  const camera = scene.cameras.main;
  let dragging = false;
  let last = null;
  scene.input.on("pointerdown", (pointer) => {
    dragging = true;
    last = { x: pointer.x, y: pointer.y };
  });
  scene.input.on("pointerup", () => {
    dragging = false;
    last = null;
  });
  scene.input.on("pointerupoutside", () => {
    dragging = false;
    last = null;
  });
  scene.input.on("pointermove", (pointer) => {
    if (!dragging || !last) return;
    camera.scrollX -= (pointer.x - last.x) / camera.zoom;
    camera.scrollY -= (pointer.y - last.y) / camera.zoom;
    camera.userMoved = true;
    last = { x: pointer.x, y: pointer.y };
  });
  scene.input.on("wheel", (_pointer, _objects, _dx, dy) => {
    const worldX = camera.scrollX + scene.input.activePointer.x / camera.zoom;
    const worldY = camera.scrollY + scene.input.activePointer.y / camera.zoom;
    camera.zoom = Phaser.Math.Clamp(camera.zoom * (dy > 0 ? 0.92 : 1.08), 0.34, 2.2);
    camera.scrollX = worldX - scene.input.activePointer.x / camera.zoom;
    camera.scrollY = worldY - scene.input.activePointer.y / camera.zoom;
    camera.userMoved = true;
  });
}

function playPhaserAnimations() {
  const events = game.animations || [];
  events.forEach((event) => {
    const from = event.from_city_id ? cityMap()[event.from_city_id] : null;
    const to = event.to_city_id ? cityMap()[event.to_city_id] : null;
    const city = event.city_id ? cityMap()[event.city_id] : to;
    if (from && to) {
      const color = event.type === "retreat" ? 0x78baff : 0xffd55d;
      const trail = phaserScene.add.graphics().lineStyle(3, color, 0.78);
      const dot = phaserScene.add.circle(from.position[0], from.position[1], 5, color, 0.95);
      phaserScene.layers.fx.add([trail, dot]);
      phaserScene.tweens.add({
        targets: dot,
        x: to.position[0],
        y: to.position[1],
        duration: 820,
        ease: "Sine.easeInOut",
        onUpdate: () => {
          trail.clear();
          trail.lineStyle(3, color, 0.72);
          trail.beginPath();
          trail.moveTo(from.position[0], from.position[1]);
          trail.lineTo(dot.x, dot.y);
          trail.strokePath();
        },
        onComplete: () => {
          phaserScene.tweens.add({ targets: [trail, dot], alpha: 0, duration: 320, onComplete: () => { trail.destroy(); dot.destroy(); } });
        },
      });
    }
    if (city && ["clash", "defend", "surrender", "incident", "discover", "capture", "warning"].includes(event.type)) {
      const colors = {
        defend: 0x8ee48a,
        surrender: 0xfff0b4,
        discover: 0x9bdcff,
        incident: 0xffac50,
        capture: 0xff523a,
        warning: 0xff3333,
        clash: 0xffd15a,
      };
      const ring = phaserScene.add.circle(city.position[0], city.position[1], 18, colors[event.type] || 0xffd15a, 0.08)
        .setStrokeStyle(4, colors[event.type] || 0xffd15a, 0.92);
      phaserScene.layers.fx.add(ring);
      phaserScene.tweens.add({
        targets: ring,
        scale: 2.4,
        alpha: 0,
        duration: 980,
        ease: "Cubic.easeOut",
        onComplete: () => ring.destroy(),
      });
    }
  });
}

function drawCityControlOverlay(ctx, offsetX, offsetY, scale) {
  const cities = Object.values(cityMap());
  ctx.save();
  ctx.globalCompositeOperation = "source-over";
  cities.forEach((city) => {
    const owner = game.city_owners[city.id];
    const faction = game.factions[owner];
    if (!faction) return;
    const x = offsetX + city.position[0] * scale;
    const y = offsetY + city.position[1] * scale;
    const base = Math.max(20, Math.min(54, (18 + city.population * 0.42) * scale));
    const gradient = ctx.createRadialGradient(x, y, Math.max(3, base * 0.18), x, y, base);
    gradient.addColorStop(0, hexToRgba(faction.color, 0.42));
    gradient.addColorStop(0.62, hexToRgba(faction.color, 0.2));
    gradient.addColorStop(1, hexToRgba(faction.color, 0));
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(x, y, base, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.restore();
}

function realMapToCanvas() {
  const ownerSignature = Object.entries(game.region_owners).sort().map(([region, owner]) => `${region}:${owner}`).join("|");
  if (realMapBitmap && realMapSignature === ownerSignature) {
    return realMapBitmap;
  }
  const map = game.real_map;
  const grid = map.province_id_grid;
  const gridHeight = grid.length;
  const gridWidth = grid[0].length;
  const provinceById = Object.fromEntries(map.provinces.map((province) => [province.id, province]));
  const canvas = document.createElement("canvas");
  canvas.width = gridWidth;
  canvas.height = gridHeight;
  const ctx = canvas.getContext("2d");
  const image = ctx.createImageData(gridWidth, gridHeight);
  const border = [212, 198, 161];

  for (let y = 0; y < gridHeight; y += 1) {
    for (let x = 0; x < gridWidth; x += 1) {
      const provinceId = grid[y][x];
      const index = (y * gridWidth + x) * 4;
      if (provinceId < 0) {
        image.data[index] = 37;
        image.data[index + 1] = 31;
        image.data[index + 2] = 25;
        image.data[index + 3] = 255;
        continue;
      }
      const province = provinceById[provinceId];
      const owner = game.region_owners[province.region_id] || provinceOwnerFallback(province.region_id);
      const color = hexToRgb(game.factions[owner]?.color || "#777777");
      const edge = realMapEdgeKind(grid, provinceById, x, y, provinceId, owner);
      const shade = edge === "state" ? 0.42 : edge === "owner" ? 0.62 : 0.82;
      const borderMix = edge === "state" ? 0.58 : edge === "owner" ? 0.38 : 0;
      image.data[index] = Math.round(color[0] * shade + border[0] * borderMix);
      image.data[index + 1] = Math.round(color[1] * shade + border[1] * borderMix);
      image.data[index + 2] = Math.round(color[2] * shade + border[2] * borderMix);
      image.data[index + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  realMapBitmap = canvas;
  realMapSignature = ownerSignature;
  return canvas;
}

function realMapEdgeKind(grid, provinceById, x, y, provinceId, owner) {
  const neighbors = [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]];
  const province = provinceById[provinceId];
  let ownerEdge = false;
  let stateEdge = false;
  for (const [nx, ny] of neighbors) {
    if (ny < 0 || nx < 0 || ny >= grid.length || nx >= grid[0].length) {
      ownerEdge = true;
      continue;
    }
    const otherId = grid[ny][nx];
    if (otherId !== provinceId) {
      if (otherId < 0) {
        ownerEdge = true;
        continue;
      }
      const other = provinceById[otherId];
      const otherOwner = game.region_owners[other.region_id] || provinceOwnerFallback(other.region_id);
      if (otherOwner !== owner) ownerEdge = true;
      if (other.region_id !== province.region_id) stateEdge = true;
    }
  }
  if (stateEdge) return "state";
  if (ownerEdge) return "owner";
  return "none";
}

function provinceOwnerFallback(regionId) {
  if (["yizhou", "hanzhong", "nanzhong"].includes(regionId)) return "liu_bei";
  if (["yangzhou", "jiaozhou", "jingzhou"].includes(regionId)) return "sun_quan";
  return "cao";
}

function drawRealMapStateLabels(ctx, offsetX, offsetY, scale) {
  const labels = game.real_map.state_labels.filter((label) => game.region_owners[label.id]);
  labels.forEach((label) => {
    const x = offsetX + label.centroid[0] * scale;
    const y = offsetY + label.centroid[1] * scale;
    const fontSize = Math.max(12, Math.min(22, 14 * scale + 6));
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `900 ${fontSize}px "Songti SC", serif`;
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0,0,0,0.42)";
    ctx.fillStyle = "rgba(238,220,170,0.66)";
    ctx.strokeText(label.name_cn, x, y);
    ctx.fillText(label.name_cn, x, y);
    ctx.restore();
  });
}

function drawRealCapitalLabels(ctx, offsetX, offsetY, scale) {
  Object.entries(game.factions).forEach(([factionId, faction]) => {
    const center = realFactionCentroid(factionId);
    if (!center) return;
    const x = offsetX + center[0] * scale;
    const y = offsetY + center[1] * scale;
    const fontSize = Math.max(12, Math.min(24, 15 * scale + 6));
    const label = faction.name;
    ctx.save();
    ctx.font = `900 ${fontSize}px "Songti SC", serif`;
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    const metrics = ctx.measureText(label);
    const textWidth = metrics.width;
    const textHeight = fontSize;
    const panelX = x - textWidth / 2 - 8;
    const panelY = y - textHeight - 18;
    drawRoundedPanel(ctx, panelX, panelY, textWidth + 16, textHeight + 9, 5, "rgba(9,8,6,0.82)", faction.color);
    ctx.fillStyle = "rgba(0,0,0,0.66)";
    ctx.fillText(label, x - textWidth / 2 + 1, panelY + textHeight + 1);
    ctx.fillStyle = "#f6eac9";
    ctx.fillText(label, x - textWidth / 2, panelY + textHeight);
    ctx.beginPath();
    ctx.arc(x, y, Math.max(3, 4 * scale), 0, Math.PI * 2);
    ctx.fillStyle = faction.color;
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(255,243,204,0.9)";
    ctx.stroke();
    ctx.restore();
  });
}

function realFactionCentroid(factionId) {
  let total = 0;
  let sumX = 0;
  let sumY = 0;
  game.real_map.provinces.forEach((province) => {
    const owner = game.region_owners[province.region_id] || provinceOwnerFallback(province.region_id);
    if (owner !== factionId) return;
    const weight = province.cell_count || 1;
    total += weight;
    sumX += province.centroid[0] * weight;
    sumY += province.centroid[1] * weight;
  });
  if (!total) return null;
  return [sumX / total, sumY / total];
}

function drawRealSupply(ctx, offsetX, offsetY, scale) {
  Object.entries(game.city_supply || {}).forEach(([cityId, supply]) => {
    const total = Object.values(supply).reduce((sum, value) => sum + value, 0);
    const city = cityMap()[cityId];
    if (!total || !city) return;
    const x = offsetX + city.position[0] * scale;
    const y = offsetY + city.position[1] * scale + 16;
    const width = Math.min(58, 18 + total * 0.7);
    ctx.fillStyle = "rgba(9,8,6,0.82)";
    ctx.fillRect(x - width / 2, y, width, 7);
    ctx.fillStyle = "#d6b35f";
    ctx.fillRect(x - width / 2, y, width * 0.72, 7);
  });
}

function drawRealUnits(ctx, offsetX, offsetY, scale) {
  const grouped = {};
  game.units.filter((unit) => unit.unit_type !== "army").forEach((unit) => {
    const key = unit.city_id || unit.region_id;
    grouped[key] ||= [];
    grouped[key].push(unit);
  });
  Object.entries(grouped).forEach(([key, units]) => {
    const city = cityMap()[key];
    const center = city ? city.position : displayCenterForRegion(key);
    if (!center) return;
    units.forEach((unit, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, units.length);
      const radius = 18 + Math.floor(index / 6) * 8;
      const x = offsetX + center[0] * scale + Math.cos(angle) * radius * scale;
      const y = offsetY + center[1] * scale + Math.sin(angle) * radius * scale;
      drawUnitShape(ctx, unit, x, y, Math.max(4, 7 * scale));
    });
  });
}

function drawCityRoads(ctx, offsetX, offsetY, scale) {
  const cities = cityMap();
  ctx.save();
  ctx.lineWidth = Math.max(0.8, 1.15 * scale);
  Object.values(cities).forEach((city) => {
    city.neighbors.forEach((neighborId) => {
      if (city.id > neighborId) return;
      const neighbor = cities[neighborId];
      if (!neighbor) return;
      const owner = game.city_owners[city.id];
      const neighborOwner = game.city_owners[neighbor.id];
      ctx.strokeStyle = owner === neighborOwner ? hexToRgba(game.factions[owner]?.color || "#d4c6a1", 0.28) : "rgba(255,224,150,0.42)";
      ctx.beginPath();
      ctx.moveTo(offsetX + city.position[0] * scale, offsetY + city.position[1] * scale);
      ctx.lineTo(offsetX + neighbor.position[0] * scale, offsetY + neighbor.position[1] * scale);
      ctx.stroke();
    });
  });
  ctx.restore();
}

function drawCityNodes(ctx, offsetX, offsetY, scale) {
  const fontSize = Math.max(9, Math.min(14, 9 * scale + 5));
  ctx.save();
  Object.values(cityMap()).forEach((city) => {
    const owner = game.city_owners[city.id];
    const faction = game.factions[owner];
    const x = offsetX + city.position[0] * scale;
    const y = offsetY + city.position[1] * scale;
    ctx.beginPath();
    ctx.arc(x, y, Math.max(2.5, 3.6 * scale), 0, Math.PI * 2);
    ctx.fillStyle = faction?.color || "#777";
    ctx.fill();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = "rgba(255,243,204,0.86)";
    ctx.stroke();
    ctx.font = `800 ${fontSize}px "Songti SC", serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.lineWidth = 3;
    ctx.strokeStyle = "rgba(0,0,0,0.68)";
    ctx.fillStyle = "rgba(255,239,195,0.88)";
    ctx.strokeText(city.name_cn, x, y + 5);
    ctx.fillText(city.name_cn, x, y + 5);
  });
  ctx.restore();
}

function drawGeneralTokens(ctx, offsetX, offsetY, scale) {
  const grouped = {};
  game.generals.filter((general) => general.city_id && general.soldiers > 0).forEach((general) => {
    grouped[general.city_id] ||= [];
    grouped[general.city_id].push(general);
    ensurePortrait(general);
  });
  Object.entries(grouped).forEach(([cityId, generals]) => {
    const city = cityMap()[cityId];
    if (!city) return;
    generals.forEach((general, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, generals.length);
      const orbit = generals.length > 1 ? 18 + Math.floor(index / 5) * 8 : 0;
      const x = offsetX + city.position[0] * scale + Math.cos(angle) * orbit * scale;
      const y = offsetY + city.position[1] * scale - 16 * scale + Math.sin(angle) * orbit * scale;
      drawPortraitToken(ctx, general, x, y, Math.max(8, 10.8 * scale));
    });
  });
}

function drawPortraitToken(ctx, general, x, y, radius) {
  const faction = game.factions[general.faction_id];
  const img = portraitImages[general.id];
  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = faction?.color || "#777";
  ctx.fill();
  ctx.clip();
  if (img?.complete) {
    ctx.drawImage(img, x - radius, y - radius, radius * 2, radius * 2);
  } else {
    ctx.fillStyle = faction?.color || "#777";
    ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    ctx.fillStyle = "#fff3cc";
    ctx.font = `900 ${radius * 0.72}px "Songti SC", serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(general.name_cn.slice(0, 1), x, y);
  }
  ctx.restore();
  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.lineWidth = 1.4;
  ctx.strokeStyle = "rgba(255,243,204,0.96)";
  ctx.stroke();
  ctx.font = `900 ${Math.max(8, radius * 0.54)}px "Songti SC", serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 2.2;
  ctx.strokeStyle = "rgba(0,0,0,0.82)";
  ctx.fillStyle = "#fff3cc";
  const name = general.name_cn.length > 2 ? general.name_cn.slice(0, 2) : general.name_cn;
  ctx.strokeText(name, x, y);
  ctx.fillText(name, x, y);
  ctx.font = `900 ${Math.max(8, radius * 0.7)}px "PingFang SC", sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  ctx.lineWidth = 2.5;
  ctx.strokeStyle = "rgba(0,0,0,0.78)";
  ctx.fillStyle = "#fff3cc";
  const label = formatSoldiers(general.soldiers);
  ctx.strokeText(label, x, y + radius + 1);
  ctx.fillText(label, x, y + radius + 1);
  ctx.restore();
}

function drawAnimationOverlay(ctx, offsetX, offsetY, scale) {
  const events = game.animations || [];
  if (!events.length || !animationStartedAt) return;
  const elapsed = performance.now() - animationStartedAt;
  const progress = Math.min(1, elapsed / 1250);
  events.forEach((event) => {
    const from = event.from_city_id ? cityMap()[event.from_city_id] : null;
    const to = event.to_city_id ? cityMap()[event.to_city_id] : null;
    const city = event.city_id ? cityMap()[event.city_id] : to;
    ctx.save();
    if (from && to) {
      const x1 = offsetX + from.position[0] * scale;
      const y1 = offsetY + from.position[1] * scale;
      const x2 = offsetX + to.position[0] * scale;
      const y2 = offsetY + to.position[1] * scale;
      ctx.strokeStyle = event.type === "retreat" ? "rgba(130,190,255,0.78)" : "rgba(255,218,95,0.82)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x1 + (x2 - x1) * progress, y1 + (y2 - y1) * progress);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x1 + (x2 - x1) * progress, y1 + (y2 - y1) * progress, 5 + 4 * Math.sin(progress * Math.PI), 0, Math.PI * 2);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.fill();
    }
    if (city && ["clash", "defend", "surrender", "incident", "discover", "capture"].includes(event.type)) {
      const x = offsetX + city.position[0] * scale;
      const y = offsetY + city.position[1] * scale;
      const radius = (18 + progress * 30) * scale;
      ctx.strokeStyle = event.type === "defend" ? "rgba(135,220,130,0.8)" : event.type === "surrender" ? "rgba(255,240,180,0.86)" : event.type === "discover" ? "rgba(155,220,255,0.86)" : event.type === "incident" ? "rgba(255,172,80,0.82)" : "rgba(255,88,58,0.86)";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  });
}

function ensurePortrait(general) {
  if (portraitImages[general.id]) return;
  const img = new Image();
  img.onload = () => drawMap();
  img.src = general.portrait_path;
  portraitImages[general.id] = img;
}

function startAnimations() {
  if (animationFrame) {
    cancelAnimationFrame(animationFrame);
    animationFrame = null;
  }
  if (!game?.animations?.length) return;
  animationStartedAt = performance.now();
  const tick = () => {
    drawMap();
    if (performance.now() - animationStartedAt < 1300) {
      animationFrame = requestAnimationFrame(tick);
    }
  };
  animationFrame = requestAnimationFrame(tick);
}

function drawRealAttribution(ctx, height) {
  if (!game.real_map.attribution) return;
  ctx.save();
  ctx.fillStyle = "rgba(191,175,140,0.32)";
  ctx.font = "700 13px Georgia, serif";
  ctx.fillText(game.real_map.attribution, 24, height - 10);
  ctx.restore();
}

function drawRoundedPanel(ctx, x, y, width, height, radius, fill, stroke) {
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(x, y, width, height, radius);
  } else {
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
  }
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = 1;
  ctx.strokeStyle = stroke;
  ctx.stroke();
}

function displayCenterForRegion(regionId) {
  const label = game.real_map?.state_labels.find((item) => item.id === regionId);
  if (label) return label.centroid;
  const virtualCenters = {
    hanzhong: [625, 384],
    nanzhong: [610, 574],
    xuzhou: [820, 396],
    yanzhou: [785, 332],
    liaodong: [910, 188],
  };
  if (virtualCenters[regionId]) return virtualCenters[regionId];
  const region = regionMap()[regionId];
  if (!region || !game.real_map) return null;
  return [
    (region.center[0] / 1920) * game.real_map.canvas_size[0],
    (region.center[1] / 1080) * game.real_map.canvas_size[1],
  ];
}

function drawPaperNoise(ctx, width, height) {
  ctx.save();
  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "rgba(58,48,37,0.46)");
  gradient.addColorStop(0.48, "rgba(37,31,25,0.12)");
  gradient.addColorStop(1, "rgba(10,8,6,0.24)");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "rgba(246,234,201,0.025)";
  for (let i = 0; i < 900; i += 1) {
    const x = (i * 97) % width;
    const y = (i * 193) % height;
    ctx.fillRect(x, y, 1, 1);
  }
  ctx.restore();
}

function drawPolygon(ctx, polygon, offsetX, offsetY, scale, color, regionId) {
  ctx.beginPath();
  polygon.forEach((point, index) => {
    const x = offsetX + point[0] * scale;
    const y = offsetY + point[1] * scale;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.closePath();
  ctx.fillStyle = hexToRgba(color, 0.82);
  ctx.strokeStyle = "rgba(245,238,206,0.62)";
  ctx.lineWidth = Math.max(1, scale * 2);
  ctx.fill();
  ctx.stroke();

  const pressure = Object.entries(game.region_pressure)
    .filter(([key, value]) => key.endsWith(`:${regionId}`) && value > 0)
    .reduce((sum, [, value]) => sum + value, 0);
  if (pressure > 0) {
    const region = regionMap()[regionId];
    const x = offsetX + region.center[0] * scale;
    const y = offsetY + region.center[1] * scale;
    ctx.strokeStyle = "rgba(255,225,130,0.86)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, 18 + pressure * 5, 0, Math.PI * 2);
    ctx.stroke();
  }
}

function drawRegionLabel(ctx, region, offsetX, offsetY, scale) {
  const owner = game.region_owners[region.id];
  const faction = game.factions[owner];
  const x = offsetX + region.center[0] * scale;
  const y = offsetY + region.center[1] * scale;
  const fontSize = Math.max(13, Math.min(22, 17 * scale + 4));
  ctx.save();
  ctx.font = `900 ${fontSize}px "Songti SC", serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 4;
  ctx.strokeStyle = "rgba(0,0,0,0.76)";
  ctx.fillStyle = "#f6eac9";
  ctx.strokeText(region.name_cn, x, y - fontSize * 0.4);
  ctx.fillText(region.name_cn, x, y - fontSize * 0.4);
  ctx.font = `800 ${Math.max(9, fontSize - 6)}px "PingFang SC", sans-serif`;
  ctx.fillStyle = "rgba(255,242,199,0.82)";
  ctx.fillText(faction.name, x, y + fontSize * 0.75);
  ctx.restore();
}

function drawSupply(ctx, offsetX, offsetY, scale) {
  const regions = regionMap();
  Object.entries(game.regional_supply).forEach(([regionId, supply]) => {
    const total = Object.values(supply).reduce((sum, value) => sum + value, 0);
    if (!total || !regions[regionId]) return;
    const x = offsetX + regions[regionId].center[0] * scale;
    const y = offsetY + regions[regionId].center[1] * scale + 24;
    const width = Math.min(58, 18 + total * 0.7);
    ctx.fillStyle = "rgba(9,8,6,0.82)";
    ctx.fillRect(x - width / 2, y, width, 7);
    ctx.fillStyle = "#d6b35f";
    ctx.fillRect(x - width / 2, y, width * 0.72, 7);
  });
}

function drawUnits(ctx, offsetX, offsetY, scale) {
  const grouped = {};
  game.units.forEach((unit) => {
    grouped[unit.region_id] ||= [];
    grouped[unit.region_id].push(unit);
  });
  Object.entries(grouped).forEach(([regionId, units]) => {
    const region = regionMap()[regionId];
    if (!region) return;
    units.forEach((unit, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, units.length);
      const radius = 20 + Math.floor(index / 6) * 9;
      const x = offsetX + region.center[0] * scale + Math.cos(angle) * radius * scale;
      const y = offsetY + region.center[1] * scale + Math.sin(angle) * radius * scale;
      drawUnitShape(ctx, unit, x, y, Math.max(5, 8 * scale));
    });
  });
}

function drawUnitShape(ctx, unit, x, y, size) {
  const faction = game.factions[unit.faction_id];
  ctx.save();
  ctx.fillStyle = faction.color;
  ctx.strokeStyle = "rgba(255,246,218,0.95)";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  if (unit.unit_type === "army") {
    ctx.moveTo(x, y - size);
    ctx.lineTo(x + size, y + size);
    ctx.lineTo(x - size, y + size);
    ctx.closePath();
  } else if (unit.unit_type === "worker") {
    ctx.rect(x - size, y - size, size * 2, size * 2);
  } else if (unit.unit_type === "scout") {
    ctx.moveTo(x, y - size);
    ctx.lineTo(x + size, y);
    ctx.lineTo(x, y + size);
    ctx.lineTo(x - size, y);
    ctx.closePath();
  } else {
    ctx.arc(x, y, size, 0, Math.PI * 2);
  }
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function firstEnemyNeighbor(regionId, factionId) {
  const region = regionMap()[regionId];
  if (!region) return null;
  return region.neighbors.find((neighbor) => {
    const owner = game.region_owners[neighbor];
    return owner && owner !== factionId && !isAllied(factionId, owner);
  }) || null;
}

function firstNeighbor(regionId) {
  return regionMap()[regionId]?.neighbors?.[0] || null;
}

function firstBorderRegion(factionId) {
  const regions = game.regions
    .filter((region) => game.region_owners[region.id] === factionId)
    .sort((a, b) => a.id.localeCompare(b.id));
  const border = regions.find((region) => firstEnemyNeighbor(region.id, factionId));
  return border ? border.id : regions[0]?.id || null;
}

function firstBorderCity(factionId) {
  const owned = game.cities
    .filter((city) => game.city_owners[city.id] === factionId)
    .sort((a, b) => a.id.localeCompare(b.id));
  const border = owned.find((city) => firstEnemyCity(city.id, factionId));
  return border ? border.id : owned[0]?.id || null;
}

function isAllied(a, b) {
  return game.alliances.some((alliance) => alliance.factions.includes(a) && alliance.factions.includes(b));
}

function regionMap() {
  return Object.fromEntries(game.regions.map((region) => [region.id, region]));
}

function cityMap() {
  return Object.fromEntries((game.cities || []).map((city) => [city.id, city]));
}

function generalMap() {
  return Object.fromEntries((game.generals || []).map((general) => [general.id, general]));
}

function roadBetween(sourceCityId, targetCityId) {
  if (!sourceCityId || !targetCityId) return null;
  return (game.roads || []).find((road) => (
    (road.from_city_id === sourceCityId && road.to_city_id === targetCityId)
    || (road.from_city_id === targetCityId && road.to_city_id === sourceCityId)
  )) || null;
}

function routePreviewLabel(unit, targetCityId) {
  if (!unit?.city_id || !targetCityId) return "选择相邻城市查看路费。";
  const road = roadBetween(unit.city_id, targetCityId);
  if (!road) return "无直达道路，需要经由中转城。";
  const target = cityMap()[targetCityId];
  const owner = game.factions[game.city_owners[targetCityId]]?.name || "";
  return `${routeLabels[road.route_type] || road.route_type} 约${road.distance_km}km · 至${target?.name_cn || targetCityId}(${owner}) · 粮${road.food_cost} 金${road.gold_cost} 损兵${road.soldier_loss_bps / 100}% 疲${road.readiness_cost}`;
}

function formatSoldiers(value) {
  const number = Number(value || 0);
  if (number >= 10000) return `${(number / 10000).toFixed(number >= 100000 ? 0 : 1)}万`;
  if (number >= 1000) return `${Math.round(number / 100) / 10}k`;
  return `${number}`;
}

function hexToRgba(hex, alpha) {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r},${g},${b},${alpha})`;
}

function hexToRgb(hex) {
  const clean = hex.replace("#", "");
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return [r, g, b];
}

elements.newGameBtn.addEventListener("click", startGame);
elements.saveBtn.addEventListener("click", () => saveCommand().catch(showError));
elements.resolveBtn.addEventListener("click", () => resolveRound().catch(showError));
elements.codexAdvisorBtn.addEventListener("click", () => requestLocalCodexAdvisor().catch(showError));
elements.applyAdvisorBtn.addEventListener("click", applyAdvisorRecommendation);
elements.clearOrdersBtn.addEventListener("click", () => {
  orderDrafts = [];
  diplomacyDrafts = [];
  renderDrafts();
});
elements.allyWuBtn.addEventListener("click", () => addDiplomacy({ type: "propose_alliance", target: "sun_quan", duration_rounds: 10 }));
elements.breakWuBtn.addEventListener("click", () => addDiplomacy({ type: "break_alliance", target: "sun_quan", duration_rounds: 1 }));
elements.allyWeiBtn.addEventListener("click", () => addDiplomacy({ type: "propose_alliance", target: "cao", duration_rounds: 10 }));
elements.breakWeiBtn.addEventListener("click", () => addDiplomacy({ type: "break_alliance", target: "cao", duration_rounds: 1 }));
window.addEventListener("resize", () => {
  if (phaserScene) {
    phaserScene.cameras.main.needsRefit = true;
    phaserScene.cameras.main.userMoved = false;
  }
  if (game) drawMap();
});

function showError(error) {
  console.error(error);
  setStatus("出错");
}

startGame().catch(showError);
