from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hwsim.agentic.agents import MockAgentProvider, default_advisor_provider, default_agent_provider
from hwsim.agentic.models import AgentOrder, DiplomacyOrder, GameView, Policy
from hwsim.agentic.simulator import PLAYER_FACTION, AgenticGameEngine


STATIC_DIR = Path(__file__).resolve().parent / "static"


class CreateGameRequest(BaseModel):
    player_faction: str = PLAYER_FACTION


class CommandRequest(BaseModel):
    strategy_text: str = ""
    policy: Policy = "balanced"
    orders: list[AgentOrder] = Field(default_factory=list)
    diplomacy: list[DiplomacyOrder] = Field(default_factory=list)


class GameStore:
    def __init__(self, engine: AgenticGameEngine | None = None) -> None:
        self.engine = engine or AgenticGameEngine.from_default_scenario(
            agent_provider=default_agent_provider(),
            fallback_provider=MockAgentProvider(),
            advisor_provider=default_advisor_provider(),
        )
        self.states = {}

    def create(self, player_faction: str = PLAYER_FACTION, game_id: str | None = None) -> GameView:
        state = self.engine.new_game(player_faction=player_faction, game_id=game_id)
        self.states[state.game_id] = state
        return self.engine.to_view(state)

    def get(self, game_id: str) -> GameView:
        state = self.states.get(game_id)
        if state is None:
            raise KeyError(game_id)
        return self.engine.to_view(state)

    def command(self, game_id: str, request: CommandRequest) -> GameView:
        state = self._state(game_id)
        self.engine.save_player_command(
            state,
            strategy_text=request.strategy_text,
            policy=request.policy,
            orders=request.orders,
            diplomacy=request.diplomacy,
        )
        return self.engine.to_view(state)

    def resolve(self, game_id: str) -> GameView:
        state = self._state(game_id)
        self.engine.resolve_round(state)
        return self.engine.to_view(state)

    def codex_advisor(self, game_id: str) -> GameView:
        state = self._state(game_id)
        self.engine.request_local_codex_advisor(state)
        return self.engine.to_view(state)

    def reset(self, game_id: str) -> GameView:
        state = self._state(game_id)
        return self.create(player_faction=state.player_faction, game_id=game_id)

    def _state(self, game_id: str):
        state = self.states.get(game_id)
        if state is None:
            raise KeyError(game_id)
        return state


def create_app(store: GameStore | None = None) -> FastAPI:
    app = FastAPI(title="Historical War Sim - Agentic Civilization Demo")
    game_store = store or GameStore()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/games", response_model=GameView)
    def create_game(request: CreateGameRequest | None = None) -> GameView:
        request = request or CreateGameRequest()
        return game_store.create(player_faction=request.player_faction)

    @app.get("/api/games/{game_id}", response_model=GameView)
    def get_game(game_id: str) -> GameView:
        try:
            return game_store.get(game_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Game not found") from exc

    @app.post("/api/games/{game_id}/command", response_model=GameView)
    def command_game(game_id: str, request: CommandRequest) -> GameView:
        try:
            return game_store.command(game_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Game not found") from exc

    @app.post("/api/games/{game_id}/resolve", response_model=GameView)
    def resolve_game(game_id: str) -> GameView:
        try:
            return game_store.resolve(game_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Game not found") from exc

    @app.post("/api/games/{game_id}/codex-advisor", response_model=GameView)
    def codex_advisor_game(game_id: str) -> GameView:
        try:
            return game_store.codex_advisor(game_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Game not found") from exc

    @app.post("/api/games/{game_id}/reset", response_model=GameView)
    def reset_game(game_id: str) -> GameView:
        try:
            return game_store.reset(game_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Game not found") from exc

    return app


app = create_app()
