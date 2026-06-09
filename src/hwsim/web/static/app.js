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
  attack: "进攻",
  defend: "固守",
  scout: "侦察",
  farm: "屯田",
  transfer: "转运",
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

const elements = {
  canvas: document.getElementById("mapCanvas"),
  roundLine: document.getElementById("roundLine"),
  newGameBtn: document.getElementById("newGameBtn"),
  saveBtn: document.getElementById("saveBtn"),
  resolveBtn: document.getElementById("resolveBtn"),
  applyAdvisorBtn: document.getElementById("applyAdvisorBtn"),
  clearOrdersBtn: document.getElementById("clearOrdersBtn"),
  allyWuBtn: document.getElementById("allyWuBtn"),
  breakWuBtn: document.getElementById("breakWuBtn"),
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
  elements.roundLine.textContent = `${game.round} / ${game.max_rounds} 回合`;
  elements.strategyInput.value = game.current_player_command || elements.strategyInput.value;
  elements.edictText.textContent = game.finished
    ? `${game.factions[game.winner]?.name || "天下"}定鼎，一百回合之争落幕。`
    : `蜀汉军议：${elements.strategyInput.value || "结吴、守汉中、探荆州，一百回合内争天下。"}`;
  renderPolicies();
  renderAdvisor();
  renderUnits();
  renderDrafts();
  renderFactions();
  renderIntents();
  renderBattles();
  renderLogs();
  drawMap();
  startAnimations();
  renderWinner();
}

function renderAdvisor() {
  const recommendation = game.advisor_recommendation || {};
  const orders = recommendation.orders || [];
  const diplomacy = recommendation.diplomacy || [];
  elements.advisorSummary.textContent = recommendation.summary || "军师正在等待新的局势。";
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
        option.textContent = `${cityOption.name_cn} · ${game.factions[game.city_owners[cityOption.id]]?.name || ""}`;
        select.appendChild(option);
      });
      chooser.appendChild(select);
      const row = document.createElement("div");
      row.className = "action-row";
      [
        { label: "进攻", build: () => cityAttackOrder(unit, [select.value]) },
        { label: "连战", build: () => cityAttackOrder(unit, chainTargets(unit, select.value)) },
        { label: "固守", build: () => ({ unit_id: unit.id, general_id: unit.general_id, action: "defend", source_city_id: unit.city_id, region_id: unit.region_id }) },
      ].forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.label;
        button.disabled = !select.value && item.label !== "固守";
        button.addEventListener("click", () => addOrder(item.build()));
        row.appendChild(button);
      });
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
      { label: "进攻", order: attackOrder(unit) },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  if (unit.unit_type === "worker") {
    return [
      { label: "屯田", order: { unit_id: unit.id, action: "farm", region_id: unit.region_id } },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  if (unit.unit_type === "scout") {
    return [
      { label: "侦察", order: scoutOrder(unit) },
      { label: "固守", order: { unit_id: unit.id, action: "defend", region_id: unit.region_id } },
      { label: "休整", order: { unit_id: unit.id, action: "rest" } },
    ];
  }
  return [
    { label: "武器", order: transferOrder(unit, "weapons") },
    { label: "粮草", order: transferOrder(unit, "food") },
    { label: "赏金", order: transferOrder(unit, "gold") },
  ];
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
  return { unit_id: unit.id, general_id: unit.general_id, action: "attack", source_city_id: unit.city_id, target_city_ids: cleanTargets };
}

function cityTargetsFor(unit) {
  const city = cityMap()[unit.city_id];
  if (!city) return [];
  return city.neighbors
    .map((id) => cityMap()[id])
    .filter((item) => item && game.city_owners[item.id] !== unit.faction_id && !isAllied(unit.faction_id, game.city_owners[item.id]));
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

function scoutOrder(unit) {
  const cityTarget = firstEnemyCity(unit.city_id, unit.faction_id) || cityMap()[unit.city_id]?.neighbors?.[0];
  if (cityTarget) return { unit_id: unit.id, action: "scout", source_city_id: unit.city_id, target_city_id: cityTarget };
  const target = firstEnemyNeighbor(unit.region_id, unit.faction_id) || firstNeighbor(unit.region_id);
  if (!target) return null;
  return { unit_id: unit.id, action: "scout", target_region_id: target };
}

function transferOrder(unit, resource) {
  const target = firstBorderCity(unit.faction_id) || unit.city_id;
  if (target) return { unit_id: unit.id, action: "transfer", source_city_id: unit.city_id, target_city_id: target, resource, amount: 16 };
  const regionTarget = firstBorderRegion(unit.faction_id) || unit.region_id;
  return { unit_id: unit.id, action: "transfer", target_region_id: regionTarget, resource, amount: 16 };
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
  return `${order.unit_id}: ${actionLabels[order.action]}${resource}${cityName || regionName ? ` -> ${cityName || regionName}` : ""}`;
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
    row.innerHTML = `<strong>${faction.name}</strong><br>${faction.ai_intent || "Awaiting next plan."}`;
    elements.intentList.appendChild(row);
  });
}

function renderBattles() {
  if (!elements.battleList) return;
  const battles = [...(game.battle_events || [])].reverse().slice(0, 6);
  elements.battleList.innerHTML = "";
  if (!battles.length) {
    const row = document.createElement("article");
    row.className = "battle-row";
    row.textContent = "等待交战。";
    elements.battleList.appendChild(row);
    return;
  }
  battles.forEach((battle) => {
    const attacker = generalMap()[battle.attacker_general_id];
    const defender = battle.defender_general_id ? generalMap()[battle.defender_general_id] : null;
    const city = cityMap()[battle.target_city_id];
    const row = document.createElement("article");
    row.className = "battle-row";
    row.innerHTML = `<strong>${city?.name_cn || battle.target_city_id}</strong> · ${attacker?.name_cn || battle.attacker_general_id} vs ${defender?.name_cn || "守军"}<br>
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
    row.innerHTML = `<strong>R${log.round} · ${log.title}</strong><br>${log.detail}`;
    elements.logList.appendChild(row);
  });
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
  if (game.real_map) {
    drawRealMap();
    return;
  }
  drawSimpleMap();
}

function drawSimpleMap() {
  const canvas = elements.canvas;
  const parent = canvas.parentElement;
  const rect = parent.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);

  const mapWidth = 1920;
  const mapHeight = 1080;
  const uiReserve = rect.width > 1180 ? 180 : 250;
  const scale = Math.min(rect.width / mapWidth, (rect.height - uiReserve) / mapHeight) * (rect.width > 1180 ? 1.12 : 1);
  const offsetX = (rect.width - mapWidth * scale) / 2 - (rect.width > 1180 ? 40 : 0);
  const offsetY = (rect.height - uiReserve - mapHeight * scale) / 2 + (rect.width > 1180 ? 60 : 170);

  ctx.fillStyle = "#251f19";
  ctx.fillRect(0, 0, rect.width, rect.height);
  drawPaperNoise(ctx, rect.width, rect.height);

  game.regions.forEach((region) => {
    const owner = game.region_owners[region.id];
    const faction = game.factions[owner];
    drawPolygon(ctx, region.polygon, offsetX, offsetY, scale, faction.color, region.id);
  });
  game.regions.forEach((region) => drawRegionLabel(ctx, region, offsetX, offsetY, scale));
  drawSupply(ctx, offsetX, offsetY, scale);
  drawUnits(ctx, offsetX, offsetY, scale);
}

function drawRealMap() {
  const canvas = elements.canvas;
  const parent = canvas.parentElement;
  const rect = parent.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = "#251f19";
  ctx.fillRect(0, 0, rect.width, rect.height);
  drawPaperNoise(ctx, rect.width, rect.height);

  const map = game.real_map;
  const mapWidth = map.canvas_size[0];
  const mapHeight = map.canvas_size[1];
  const compact = rect.width <= 760;
  const medium = rect.width > 760 && rect.width <= 1180;
  const scale = Math.min(rect.width / mapWidth, rect.height / mapHeight) * (compact ? 0.88 : medium ? 0.9 : 0.98);
  const offsetX = (rect.width - mapWidth * scale) / 2 + (rect.width > 1180 ? -10 : 0);
  let offsetY = (rect.height - mapHeight * scale) / 2 + (rect.width > 1180 ? 20 : 86);
  if (compact) {
    offsetY = 382;
  }

  const bitmap = realMapToCanvas();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(bitmap, offsetX, offsetY, mapWidth * scale, mapHeight * scale);
  drawCityRoads(ctx, offsetX, offsetY, scale);
  drawRealMapStateLabels(ctx, offsetX, offsetY, scale);
  drawCityNodes(ctx, offsetX, offsetY, scale);
  drawRealSupply(ctx, offsetX, offsetY, scale);
  drawRealUnits(ctx, offsetX, offsetY, scale);
  drawGeneralTokens(ctx, offsetX, offsetY, scale);
  drawAnimationOverlay(ctx, offsetX, offsetY, scale);
  drawRealAttribution(ctx, rect.height);
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
  ctx.font = `900 ${Math.max(10, radius * 0.95)}px "Songti SC", serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(0,0,0,0.82)";
  ctx.fillStyle = "#fff3cc";
  ctx.strokeText(general.name_cn, x, y - radius - 1);
  ctx.fillText(general.name_cn, x, y - radius - 1);
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
    if (city && ["clash", "defend", "surrender"].includes(event.type)) {
      const x = offsetX + city.position[0] * scale;
      const y = offsetY + city.position[1] * scale;
      const radius = (18 + progress * 30) * scale;
      ctx.strokeStyle = event.type === "defend" ? "rgba(135,220,130,0.8)" : event.type === "surrender" ? "rgba(255,240,180,0.86)" : "rgba(255,88,58,0.86)";
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
elements.applyAdvisorBtn.addEventListener("click", applyAdvisorRecommendation);
elements.clearOrdersBtn.addEventListener("click", () => {
  orderDrafts = [];
  diplomacyDrafts = [];
  renderDrafts();
});
elements.allyWuBtn.addEventListener("click", () => addDiplomacy({ type: "propose_alliance", target: "sun_quan", duration_rounds: 5 }));
elements.breakWuBtn.addEventListener("click", () => addDiplomacy({ type: "break_alliance", target: "sun_quan", duration_rounds: 1 }));
window.addEventListener("resize", () => {
  if (game) drawMap();
});

function showError(error) {
  console.error(error);
  setStatus("出错");
}

startGame().catch(showError);
