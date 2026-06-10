from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Protocol

from hwsim.agentic.models import AdvisorRecommendation, AgentObservation, AgentOrder, AgentPlan, DiplomacyOrder
from hwsim.agentic.models import POLICIES, RESOURCE_TYPES, UNIT_ACTIONS
from hwsim.utils.file_utils import project_root


class AgentProvider(Protocol):
    def decide(self, observation: AgentObservation) -> AgentPlan:
        ...


class AdvisorProvider(Protocol):
    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        ...


class LocalCodexAdvisorError(RuntimeError):
    """Raised when the local Codex CLI cannot produce a valid advisor plan."""


class MockAgentProvider:
    """Deterministic strategy provider for tests and offline demos."""

    def decide(self, observation: AgentObservation) -> AgentPlan:
        if observation.faction_id == "cao":
            return self._cao_plan(observation)
        if observation.faction_id == "sun_quan":
            return self._wu_plan(observation)
        return self._balanced_plan(observation)

    def _cao_plan(self, observation: AgentObservation) -> AgentPlan:
        orders = self._conquest_orders(observation, prefer_resource="weapons")
        return AgentPlan(
            policy="war",
            orders=orders,
            reasoning_summary="魏军以夺城为先，优先压迫边境并把兵械送往前线。",
        )

    def _wu_plan(self, observation: AgentObservation) -> AgentPlan:
        diplomacy: list[DiplomacyOrder] = []
        if "liu_bei" not in observation.alliances and observation.round <= 8:
            diplomacy.append(DiplomacyOrder(type="propose_alliance", target="liu_bei", duration_rounds=10))
        elif "liu_bei" in observation.alliances and observation.round > 26:
            diplomacy.append(DiplomacyOrder(type="break_alliance", target="liu_bei", duration_rounds=1))
        orders = self._conquest_orders(observation, prefer_resource="food")
        return AgentPlan(
            policy="farming" if observation.round <= 4 else "war",
            orders=orders,
            diplomacy=diplomacy,
            reasoning_summary="吴军先以盟约遮护江东，再从水网城池向薄弱邻城扩张。",
        )

    def _balanced_plan(self, observation: AgentObservation) -> AgentPlan:
        orders = self._conquest_orders(observation, prefer_resource="food")
        return AgentPlan(policy="war" if any(order.action == "attack" for order in orders) else "balanced", orders=orders, reasoning_summary="以夺城为目标，同时让无战机部队屯田、侦察或固守。")

    def _conquest_orders(self, observation: AgentObservation, prefer_resource: str) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        ordered_units = sorted(observation.units, key=lambda item: (item.unit_type != "army", -item.readiness, item.id))
        for unit in ordered_units:
            if unit.unit_type == "army":
                battle_order = self._battle_order(observation, unit)
                if battle_order:
                    orders.append(battle_order)
                    continue
                orders.append(self._first_city_attack_order(observation, unit) or self._defend_order(unit))
            elif unit.unit_type == "worker":
                orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id, source_city_id=unit.city_id))
            elif unit.unit_type == "scout":
                orders.append(self._scout_order(observation, unit) or self._defend_order(unit))
            elif unit.unit_type == "caravan":
                orders.append(self._transfer_order(observation, unit, prefer_resource))
        return orders

    def _battle_order(self, observation: AgentObservation, unit) -> AgentOrder | None:
        for battle in observation.active_battles:
            if unit.id in battle.attacker_unit_ids or unit.id in battle.defender_unit_ids:
                if battle.odds < 0.18 and battle.attacker_faction == observation.faction_id:
                    return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="retreat", battle_id=battle.id)
                return None
            if observation.faction_id not in {battle.attacker_faction, battle.defender_faction}:
                continue
            road = self._road_between(observation, unit.city_id or "", battle.target_city_id)
            if unit.city_id and road and self._route_affordable(observation, road):
                return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="reinforce", battle_id=battle.id, source_city_id=unit.city_id, target_city_id=battle.target_city_id)
        return None

    def _border_attack_orders(self, observation: AgentObservation, limit: int) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        armies = [unit for unit in observation.units if unit.unit_type == "army"]
        for unit in sorted(armies, key=lambda item: (-item.readiness, item.id)):
            if unit.city_id and observation.city_neighbors:
                for neighbor in observation.city_neighbors.get(unit.city_id, []):
                    owner = observation.visible_cities.get(neighbor)
                    if owner and owner != observation.faction_id and owner not in observation.alliances:
                        orders.append(
                            AgentOrder(
                                unit_id=unit.id,
                                general_id=unit.general_id,
                                action="attack",
                                source_city_id=unit.city_id,
                                target_city_ids=[neighbor],
                            )
                        )
                        break
                if len(orders) >= limit:
                    break
                if orders and orders[-1].unit_id == unit.id:
                    continue
            for neighbor in observation.neighbors.get(unit.region_id, []):
                owner = observation.visible_regions.get(neighbor)
                if owner and owner != observation.faction_id and owner not in observation.alliances:
                    orders.append(AgentOrder(unit_id=unit.id, action="attack", target_region_id=neighbor))
                    break
            if len(orders) >= limit:
                break
        return orders

    def _first_city_attack_order(self, observation: AgentObservation, unit) -> AgentOrder | None:
        if unit.city_id and observation.city_neighbors:
            best_target: str | None = None
            best_score = -9999.0
            for neighbor in observation.city_neighbors.get(unit.city_id, []):
                owner = observation.visible_cities.get(neighbor)
                if owner and owner != observation.faction_id and owner not in observation.alliances:
                    road = self._road_between(observation, unit.city_id, neighbor)
                    if not road:
                        continue
                    score = len(observation.city_neighbors.get(neighbor, [])) * 10 - self._route_cost_score(road)
                    if score > best_score:
                        best_target = neighbor
                        best_score = score
            if best_target:
                return AgentOrder(
                    unit_id=unit.id,
                    general_id=unit.general_id,
                    action="attack",
                    source_city_id=unit.city_id,
                    target_city_ids=[best_target],
                )
            return None
        for neighbor in observation.neighbors.get(unit.region_id, []):
            owner = observation.visible_regions.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="attack", target_region_id=neighbor)
        return None

    def _defend_order(self, unit) -> AgentOrder:
        return AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="defend", region_id=unit.region_id, source_city_id=unit.city_id)

    def _scout_order(self, observation: AgentObservation, unit) -> AgentOrder | None:
        target = self._first_enemy_city_neighbor(observation, unit.city_id or "") or self._first_enemy_neighbor(observation, unit.region_id)
        if not target:
            return None
        if target in observation.visible_cities:
            return AgentOrder(unit_id=unit.id, action="scout", source_city_id=unit.city_id, target_city_id=target)
        return AgentOrder(unit_id=unit.id, action="scout", target_region_id=target)

    def _transfer_order(self, observation: AgentObservation, unit, resource: str) -> AgentOrder:
        target = self._transfer_target_city(observation, unit) or self._first_border_region(observation) or unit.region_id
        kwargs = {"target_city_id": target} if target in observation.visible_cities else {"target_region_id": target}
        return AgentOrder(
            unit_id=unit.id,
            action="transfer",
            source_city_id=unit.city_id,
            resource=resource,  # type: ignore[arg-type]
            amount=18,
            **kwargs,
        )

    def _transfer_target_city(self, observation: AgentObservation, unit) -> str | None:
        if not unit.city_id:
            return None
        candidates: list[tuple[float, str]] = []
        for road in observation.city_roads.get(unit.city_id, []):
            target = road.other(unit.city_id)
            if not target:
                continue
            owner = observation.visible_cities.get(target)
            if owner != observation.faction_id and owner not in observation.alliances:
                continue
            enemy_edges = sum(
                1
                for neighbor in observation.city_neighbors.get(target, [])
                if (neighbor_owner := observation.visible_cities.get(neighbor))
                and neighbor_owner != observation.faction_id
                and neighbor_owner not in observation.alliances
            )
            affordable_bonus = 18 if self._route_affordable(observation, road) else -24
            candidates.append((enemy_edges * 20 + affordable_bonus - self._route_cost_score(road), target))
        if not candidates:
            return unit.city_id
        return max(candidates, key=lambda item: (item[0], item[1]))[1]

    def _support_orders(self, observation: AgentObservation, prefer_resource: str) -> list[AgentOrder]:
        orders: list[AgentOrder] = []
        for unit in sorted(observation.units, key=lambda item: item.id):
            if unit.unit_type == "worker":
                orders.append(AgentOrder(unit_id=unit.id, action="farm", region_id=unit.region_id, source_city_id=unit.city_id))
            elif unit.unit_type == "scout":
                target = self._first_enemy_city_neighbor(observation, unit.city_id or "") or self._first_enemy_neighbor(observation, unit.region_id)
                if target:
                    if target in observation.visible_cities:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", source_city_id=unit.city_id, target_city_id=target))
                    else:
                        orders.append(AgentOrder(unit_id=unit.id, action="scout", target_region_id=target))
            elif unit.unit_type == "caravan":
                target = self._transfer_target_city(observation, unit) or self._first_border_region(observation) or unit.region_id
                kwargs = {"target_city_id": target} if target in observation.visible_cities else {"target_region_id": target}
                orders.append(
                    AgentOrder(
                        unit_id=unit.id,
                        action="transfer",
                        source_city_id=unit.city_id,
                        resource=prefer_resource,  # type: ignore[arg-type]
                        amount=14,
                        **kwargs,
                    )
                )
        return orders

    def _first_enemy_city_neighbor(self, observation: AgentObservation, city_id: str) -> str | None:
        for neighbor in observation.city_neighbors.get(city_id, []):
            owner = observation.visible_cities.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return neighbor
        return None

    def _first_enemy_neighbor(self, observation: AgentObservation, region_id: str) -> str | None:
        for neighbor in observation.neighbors.get(region_id, []):
            owner = observation.visible_regions.get(neighbor)
            if owner and owner != observation.faction_id and owner not in observation.alliances:
                return neighbor
        return None

    def _first_border_region(self, observation: AgentObservation) -> str | None:
        for region_id in sorted(observation.owned_regions):
            if self._first_enemy_neighbor(observation, region_id):
                return region_id
        return observation.owned_regions[0] if observation.owned_regions else None

    def _first_border_city(self, observation: AgentObservation) -> str | None:
        owned = [unit.city_id for unit in observation.units if unit.city_id]
        for city_id in sorted(set(owned)):
            if self._first_enemy_city_neighbor(observation, city_id):
                return city_id
        return owned[0] if owned else None

    def _road_between(self, observation: AgentObservation, source_city_id: str, target_city_id: str):
        if not source_city_id or not target_city_id:
            return None
        for road in observation.city_roads.get(source_city_id, []):
            if road.other(source_city_id) == target_city_id:
                return road
        return None

    def _route_affordable(self, observation: AgentObservation, road) -> bool:
        return observation.resources.food >= road.food_cost and observation.resources.gold >= road.gold_cost

    def _route_cost_score(self, road) -> float:
        return road.food_cost * 9 + road.gold_cost * 6 + road.readiness_cost * 1.4 + road.soldier_loss_bps * 0.15


class OpenAIAgentProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_AGENT_MODEL", "gpt-5.4-mini")

    def decide(self, observation: AgentObservation) -> AgentPlan:
        from openai import OpenAI

        client = OpenAI()
        schema = AgentPlan.model_json_schema()
        prompt = (
            "You control one Three Kingdoms faction in a 100-round city conquest game. "
            "Your goal is to defeat the other factions by controlling the most valuable cities. "
            "Return only legal JSON matching the schema. Each unit may receive at most one order. "
            "Cities are connected by explicit roads; move, attack, scout, transfer, and reinforce can only use one adjacent road per round "
            "and should account for food, gold, soldier attrition, and readiness cost in the observation. "
            "Prefer attacks that capture cities, defend exposed fronts, and move supplies toward active generals."
        )
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": observation.model_dump_json()},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "agent_plan",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            output_text = _extract_output_text(response)
        return AgentPlan.model_validate_json(output_text)


class DeterministicAdvisorProvider:
    """Free local Shu advisor used by the web UI and tests."""

    def __init__(self) -> None:
        self._planner = MockAgentProvider()

    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        orders = self._planner._conquest_orders(observation, prefer_resource="weapons")
        attack_count = sum(1 for order in orders if order.action == "attack")
        reinforce_count = sum(1 for order in orders if order.action == "reinforce")
        defend_count = sum(1 for order in orders if order.action == "defend")
        exposed = sum(
            1
            for unit in observation.units
            if unit.unit_type == "army"
            and unit.city_id
            and self._planner._first_enemy_city_neighbor(observation, unit.city_id)
        )
        policy = "war" if attack_count else "defense" if exposed else "farming"
        if any(token in observation.player_command.lower() for token in ("屯田", "粮", "farm", "food")):
            policy = "farming" if attack_count == 0 else policy
        if any(token in observation.player_command.lower() for token in ("魏", "wei", "曹")):
            orders = self._prioritize_target_owner(observation, orders, "cao")
        if any(token in observation.player_command.lower() for token in ("吴", "wu", "孙")):
            orders = self._prioritize_target_owner(observation, orders, "sun_quan")
        diplomacy: list[DiplomacyOrder] = []
        if "sun_quan" not in observation.alliances and observation.round <= 8:
            diplomacy.append(DiplomacyOrder(type="propose_alliance", target="sun_quan", duration_rounds=10))
        if any(token in observation.player_command.lower() for token in ("联魏", "结魏", "ally wei", "alliance wei")) and "cao" not in observation.alliances:
            diplomacy = [DiplomacyOrder(type="propose_alliance", target="cao", duration_rounds=10)]
        summary = (
            f"建议采用{self._policy_cn(policy)}：本回合为每个可行动对象安排军令，"
            f"含进攻{attack_count}路、增援{reinforce_count}路、固守{defend_count}路；"
            f"依据邻城归属、兵力、战役胶着度、粮金补给与主公方略修正。"
        )
        return AdvisorRecommendation(policy=policy, orders=orders, diplomacy=diplomacy, summary=summary)

    def _prioritize_target_owner(self, observation: AgentObservation, orders: list[AgentOrder], owner_id: str) -> list[AgentOrder]:
        prioritized: list[AgentOrder] = []
        for order in orders:
            if order.action != "attack" or not order.unit_id:
                prioritized.append(order)
                continue
            unit = next((item for item in observation.units if item.id == order.unit_id), None)
            if not unit or not unit.city_id:
                prioritized.append(order)
                continue
            replacement = None
            for neighbor in observation.city_neighbors.get(unit.city_id, []):
                if observation.visible_cities.get(neighbor) == owner_id and owner_id not in observation.alliances:
                    replacement = AgentOrder(unit_id=unit.id, general_id=unit.general_id, action="attack", source_city_id=unit.city_id, target_city_ids=[neighbor])
                    break
            prioritized.append(replacement or order)
        return prioritized

    def _policy_cn(self, policy: str) -> str:
        return {"balanced": "均衡", "farming": "屯田", "war": "征伐", "logistics": "转运", "defense": "固守", "diplomacy": "外交"}.get(policy, policy)


class LocalCodexAdvisorProvider:
    """Manual Shu advisor backed by the local Codex CLI."""

    def __init__(
        self,
        model: str | None = None,
        timeout_seconds: float | None = None,
        cwd: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.model = model or os.environ.get("LOCAL_CODEX_ADVISOR_MODEL", "gpt-5.4-mini")
        self.timeout_seconds = timeout_seconds or float(os.environ.get("LOCAL_CODEX_ADVISOR_TIMEOUT", "90"))
        self.cwd = cwd or project_root()
        self.runner = runner or subprocess.run

    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        codex_command = self._codex_command()
        schema = self._advisor_output_schema()
        with tempfile.TemporaryDirectory(prefix="hwsim-codex-advisor-") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "advisor_schema.json"
            output_path = temp_path / "advisor_output.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            prompt = self._prompt(observation)
            command = [
                codex_command,
                "exec",
                "--model",
                self.model,
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--cd",
                str(temp_path),
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                prompt,
            ]
            env = dict(os.environ)
            env["RUST_LOG"] = "error"
            try:
                completed = self.runner(
                    command,
                    cwd=str(temp_path),
                    text=True,
                    capture_output=True,
                    timeout=self.timeout_seconds,
                    env=env,
                )
            except subprocess.TimeoutExpired as exc:
                raise LocalCodexAdvisorError(f"本地 Codex 超时（{self.timeout_seconds:.0f}秒）。") from exc
            except OSError as exc:
                raise LocalCodexAdvisorError(f"无法启动本地 Codex：{exc}") from exc
            output_text = ""
            if output_path.exists():
                output_text = output_path.read_text(encoding="utf-8").strip()
            if not output_text:
                output_text = (getattr(completed, "stdout", "") or "").strip()
            if output_text:
                try:
                    return self._parse_recommendation(output_text)
                except LocalCodexAdvisorError as exc:
                    parse_error = str(exc)
                else:
                    parse_error = ""
            else:
                parse_error = "空输出"
            if getattr(completed, "returncode", 1) != 0:
                raise LocalCodexAdvisorError(self._cli_error_message(completed, parse_error))
            raise LocalCodexAdvisorError(parse_error[:500] or "本地 Codex 输出不是合法军师 JSON。")

    def _codex_command(self) -> str:
        configured = os.environ.get("LOCAL_CODEX_COMMAND")
        if configured:
            return configured
        resolved = shutil.which("codex")
        if resolved:
            return resolved
        app_bundle = Path("/Applications/Codex.app/Contents/Resources/codex")
        if app_bundle.exists():
            return str(app_bundle)
        raise LocalCodexAdvisorError("未找到本地 Codex CLI。请确认 Codex.app 已安装，或设置 LOCAL_CODEX_COMMAND。")

    def _prompt(self, observation: AgentObservation) -> str:
        return (
            "你是蜀汉军师，正在为一个城市级三国策略游戏给玩家建议。\n"
            "不要运行任何工具命令，不要读取文件；你需要的全部局势都在下面的 JSON 里。\n"
            "目标：消灭其他势力，夺取城池，同时避免孤军深入和补给断裂。\n"
            "必须优先尊重玩家输入的方略；如果玩家方略不合法，请给出最接近且合法的替代军令。\n"
            "每个 unit 最多一个 order。move、attack、scout、transfer、reinforce 只能走相邻道路，且要考虑粮、金、损兵、疲劳成本。\n"
            "每条 order 必须包含 schema 要求的所有字段；不使用的可选字段请填 null，target_city_ids 可填空数组，amount 填 0。\n"
            "输出必须只是一段 JSON，完全符合 AdvisorRecommendation schema：policy、orders、diplomacy、summary。\n"
            "summary 使用中文，说明为什么这样安排。\n\n"
            f"局势观察 JSON：\n{json.dumps(self._compact_observation(observation), ensure_ascii=False, separators=(',', ':'))}"
        )

    def _compact_observation(self, observation: AgentObservation) -> dict[str, object]:
        unit_city_ids = {unit.city_id for unit in observation.units if unit.city_id}
        relevant_city_ids = set(unit_city_ids)
        for city_id in list(unit_city_ids):
            relevant_city_ids.update(observation.city_neighbors.get(city_id, []))
        for battle in observation.active_battles:
            relevant_city_ids.add(battle.source_city_id)
            relevant_city_ids.add(battle.target_city_id)

        roads: list[dict[str, object]] = []
        seen_roads: set[tuple[str, str]] = set()
        for road in observation.roads:
            if road.from_city_id not in relevant_city_ids or road.to_city_id not in relevant_city_ids:
                continue
            key = tuple(sorted((road.from_city_id, road.to_city_id)))
            if key in seen_roads:
                continue
            seen_roads.add(key)
            roads.append(
                {
                    "from": road.from_city_id,
                    "to": road.to_city_id,
                    "type": road.route_type,
                    "km": road.distance_km,
                    "food": road.food_cost,
                    "gold": road.gold_cost,
                    "loss_bps": road.soldier_loss_bps,
                    "readiness": road.readiness_cost,
                }
            )

        return {
            "faction_id": observation.faction_id,
            "round": observation.round,
            "max_rounds": observation.max_rounds,
            "policy": observation.policy,
            "resources": observation.resources.model_dump(mode="json"),
            "alliances": observation.alliances,
            "player_command": observation.player_command,
            "units": [
                {
                    "id": unit.id,
                    "general_id": unit.general_id,
                    "type": unit.unit_type,
                    "region": unit.region_id,
                    "city": unit.city_id,
                    "soldiers": unit.soldiers,
                    "readiness": unit.readiness,
                    "status": unit.status,
                }
                for unit in observation.units
            ],
            "city_owners": {city_id: observation.visible_cities.get(city_id) for city_id in sorted(relevant_city_ids) if city_id in observation.visible_cities},
            "city_neighbors": {
                city_id: [neighbor for neighbor in observation.city_neighbors.get(city_id, []) if neighbor in observation.visible_cities]
                for city_id in sorted(relevant_city_ids)
            },
            "roads": roads,
            "active_battles": [
                {
                    "id": battle.id,
                    "target_city_id": battle.target_city_id,
                    "source_city_id": battle.source_city_id,
                    "attacker_faction": battle.attacker_faction,
                    "defender_faction": battle.defender_faction,
                    "elapsed_rounds": battle.elapsed_rounds,
                    "duration_rounds": battle.duration_rounds,
                    "odds": battle.odds,
                    "summary": battle.summary,
                }
                for battle in observation.active_battles
            ],
            "recent_log": observation.recent_log[-8:],
        }

    def _advisor_output_schema(self) -> dict[str, object]:
        string_or_null = {"type": ["string", "null"]}
        order_properties: dict[str, object] = {
            "unit_id": string_or_null,
            "general_id": string_or_null,
            "action": {"type": "string", "enum": list(UNIT_ACTIONS)},
            "region_id": string_or_null,
            "target_region_id": string_or_null,
            "source_city_id": string_or_null,
            "target_city_id": string_or_null,
            "target_city_ids": {"type": "array", "items": {"type": "string"}},
            "target_faction": string_or_null,
            "battle_id": string_or_null,
            "resource": {"type": ["string", "null"], "enum": [*RESOURCE_TYPES, None]},
            "amount": {"type": "integer"},
        }
        diplomacy_properties: dict[str, object] = {
            "type": {"type": "string", "enum": ["propose_alliance", "break_alliance"]},
            "target": {"type": "string"},
            "duration_rounds": {"type": "integer"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["policy", "orders", "diplomacy", "summary"],
            "properties": {
                "policy": {"type": "string", "enum": list(POLICIES)},
                "orders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(order_properties),
                        "properties": order_properties,
                    },
                },
                "diplomacy": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(diplomacy_properties),
                        "properties": diplomacy_properties,
                    },
                },
                "summary": {"type": "string"},
            },
        }

    def _parse_recommendation(self, output_text: str) -> AdvisorRecommendation:
        errors: list[str] = []
        for candidate in self._json_candidates(output_text):
            if not self._looks_like_advisor_json(candidate):
                continue
            try:
                return AdvisorRecommendation.model_validate_json(candidate)
            except Exception as exc:  # pydantic returns several validation subclasses.
                errors.append(str(exc))
        raise LocalCodexAdvisorError(f"本地 Codex 输出不是合法军师 JSON：{errors[-1][:300] if errors else '空输出'}")

    def _looks_like_advisor_json(self, candidate: str) -> bool:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return False
        return isinstance(value, dict) and bool({"orders", "diplomacy", "summary"} & set(value))

    def _json_candidates(self, output_text: str) -> list[str]:
        text = output_text.strip()
        candidates = [text] if text.startswith("{") else []
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            candidates.append(fenced.group(1).strip())
        candidates.extend(self._embedded_json_objects(text))
        unique: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in unique:
                unique.append(candidate)
        return unique

    def _embedded_json_objects(self, text: str) -> list[str]:
        decoder = json.JSONDecoder()
        candidates: list[str] = []
        brace_positions = [match.start() for match in re.finditer(r"\{", text)]
        for position in reversed(brace_positions[-200:]):
            try:
                value, end = decoder.raw_decode(text[position:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                candidates.append(text[position : position + end].strip())
        return candidates

    def _cli_error_message(self, completed: subprocess.CompletedProcess[str], parse_error: str) -> str:
        raw = "\n".join(
            part
            for part in [
                getattr(completed, "stderr", "") or "",
                getattr(completed, "stdout", "") or "",
            ]
            if part
        )
        cleaned = self._clean_cli_error(raw)
        if cleaned:
            return cleaned[:500]
        if parse_error and parse_error != "空输出":
            return parse_error[:500]
        return "本地 Codex 没有生成合法军师 JSON；已忽略 CLI 启动 warning。请稍后重试，或检查 Codex 登录、模型权限和用量限制。"

    def _clean_cli_error(self, raw: str) -> str:
        warning_markers = (
            "codex_rollout::list",
            "state db discrepancy",
            "codex_core_plugins::manifest",
            "codex_core_skills::loader",
            "ignoring interface.defaultprompt",
            "ignoring interface.icon_",
            "state db backfill",
        )
        error_markers = (
            "error",
            "failed",
            "fatal",
            "timed out",
            "timeout",
            "usage limit",
            "rate limit",
            "unauthorized",
            "forbidden",
            "jwt verification",
            "not found",
            "invalid",
            "unavailable",
            "401",
            "403",
            "429",
        )
        cleaned_lines: list[str] = []
        for line in raw.replace("\r\n", "\n").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if any(marker in lowered for marker in warning_markers):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}t.+\bwarn\b", lowered) and "error:" not in lowered:
                continue
            if any(marker in lowered for marker in error_markers):
                cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines[-8:])


class OpenAIAdvisorProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("OPENAI_ADVISOR_MODEL", os.environ.get("OPENAI_AGENT_MODEL", "gpt-5.4-mini"))

    def recommend(self, observation: AgentObservation) -> AdvisorRecommendation:
        from openai import OpenAI

        client = OpenAI()
        schema = AdvisorRecommendation.model_json_schema()
        prompt = (
            "You are Shu Han's military advisor in a 100-round Three Kingdoms city conquest game. "
            "Return only legal JSON matching the schema. Recommend one policy and at most one order per unit. "
            "Prioritize the player's written strategy where it can be made legal. "
            "Cities are connected by explicit roads; move, attack, scout, transfer, and reinforce can only use one adjacent road per round "
            "and should account for food, gold, soldier attrition, and readiness cost in the observation. "
            "Prefer winning city control, preserving generals, and exploiting weak adjacent cities."
        )
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": observation.model_dump_json()},
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "advisor_recommendation",
                    "schema": schema,
                    "strict": True,
                }
            },
        )
        output_text = getattr(response, "output_text", None) or _extract_output_text(response)
        return AdvisorRecommendation.model_validate_json(output_text)


def default_agent_provider() -> AgentProvider:
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIAgentProvider()
    return MockAgentProvider()


def default_advisor_provider() -> AdvisorProvider:
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_ADVISOR_ENABLED") == "1":
        return OpenAIAdvisorProvider()
    return DeterministicAdvisorProvider()


def _extract_output_text(response: object) -> str:
    try:
        data = response.model_dump()  # type: ignore[attr-defined]
    except AttributeError:
        data = response if isinstance(response, dict) else {}
    for item in data.get("output", []):
        for content in item.get("content", []):
            if "text" in content:
                return str(content["text"])
    return json.dumps({})
