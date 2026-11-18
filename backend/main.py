import asyncio
import json
import random
import time
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="GridClash Authoritative Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

GRID_SIZE = 40
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
OPPOSITE = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
SPAWNS = [
    {"x": 8, "y": 20, "dir": "RIGHT", "color": "#38bdf8"},
    {"x": 31, "y": 20, "dir": "LEFT", "color": "#f43f5e"},
]

LEADERBOARD: dict[str, dict] = {}

def record_match_result(players: dict, winner: str | None):
    for pid in players:
        stats = LEADERBOARD.setdefault(pid, {"wins": 0, "matches": 0})
        stats["matches"] += 1
        if pid == winner:
            stats["wins"] += 1

class GameRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.sockets: dict[str, WebSocket] = {}
        self.players: dict[str, dict] = {}
        self.occupied: set[tuple[int, int]] = set()
        self.status = "WAITING"
        self.winner = None
        self.loop_task = None
        self.has_bot = False
        self.bot_strategy = "FLOOD_FILL"
        self.telemetry = {
            "tick_ms": 0.0,
            "tick_count": 0,
            "bot_eval_ms": 0.0,
            "bot_nodes_scanned": 0,
            "strategy": "FLOOD_FILL"
        }

    def _set_bot_player(self, strategy: str):
        for k in list(self.players.keys()):
            if self.players[k].get("is_bot"):
                self.players.pop(k, None)
        self.bot_strategy = strategy if strategy in ("FLOOD_FILL", "GREEDY") else "FLOOD_FILL"
        self.telemetry["strategy"] = self.bot_strategy
        bot_name = f"AI-{self.bot_strategy}"
        self.players[bot_name] = {
            "alive": True, "trail": [], "dir": "LEFT",
            "x": 0, "y": 0, "color": "#f43f5e", "is_bot": True
        }

    def add_bot(self, strategy: str = "FLOOD_FILL"):
        if len(self.players) < 2 and not self.has_bot:
            self.has_bot = True
            self._set_bot_player(strategy)
            self.reset_match()
            self.loop_task = asyncio.create_task(self.game_loop())

    def switch_bot_strategy(self, strategy: str):
        if self.has_bot:
            self._set_bot_player(strategy)

    def _bfs_reachable_area(self, start_x: int, start_y: int, limit: int = 250) -> int:
        visited = {(start_x, start_y)}
        queue = deque([(start_x, start_y)])
        while queue and len(visited) < limit:
            cx, cy = queue.popleft()
            for dx, dy in DIRS.values():
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < GRID_SIZE and 0 <= ny < GRID_SIZE and (nx, ny) not in self.occupied and (nx, ny) not in visited:
                    visited.add((nx, ny))
                    queue.append((nx, ny))
        return len(visited)

    def _choose_bot_direction(self, bot: dict) -> str:
        t0 = time.perf_counter()
        curr_dir = bot["dir"]
        valid_moves = [d for d in DIRS if OPPOSITE[d] != curr_dir]
        best_dir = curr_dir
        best_score = -1.0
        nodes_scanned = 0

        for d in valid_moves:
            dx, dy = DIRS[d]
            nx, ny = bot["x"] + dx, bot["y"] + dy
            if nx < 0 or nx >= GRID_SIZE or ny < 0 or ny >= GRID_SIZE or (nx, ny) in self.occupied:
                continue

            if self.bot_strategy == "FLOOD_FILL":
                area = self._bfs_reachable_area(nx, ny, limit=220)
                nodes_scanned += area
                score = float(area) + (1.2 if d == curr_dir else 0.0) + random.uniform(0, 0.5)
            else:
                clearance = 0
                tx, ty = nx, ny
                while 0 <= tx < GRID_SIZE and 0 <= ty < GRID_SIZE and (tx, ty) not in self.occupied:
                    clearance += 1
                    tx += dx
                    ty += dy
                nodes_scanned += clearance
                score = float(clearance) + (1.5 if d == curr_dir else 0.0) + random.uniform(0, 0.5)

            if score > best_score:
                best_score = score
                best_dir = d

        self.telemetry["bot_eval_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        self.telemetry["bot_nodes_scanned"] = nodes_scanned
        return best_dir

    def reset_match(self):
        self.occupied.clear()
        self.winner = None
        self.telemetry["tick_count"] = 0
        for idx, (pid, pdata) in enumerate(self.players.items()):
            spawn = SPAWNS[idx % len(SPAWNS)]
            pdata.update({
                "x": spawn["x"],
                "y": spawn["y"],
                "dir": spawn["dir"],
                "color": spawn["color"],
                "alive": True,
                "trail": [(spawn["x"], spawn["y"])]
            })
            self.occupied.add((spawn["x"], spawn["y"]))
        self.status = "RUNNING"

    async def game_loop(self):
        while self.status == "RUNNING":
            await asyncio.sleep(0.08)
            tick_start = time.perf_counter()
            alive_players = []

            for pid, p in self.players.items():
                if not p["alive"]:
                    continue
                if p.get("is_bot"):
                    p["dir"] = self._choose_bot_direction(p)

                dx, dy = DIRS[p["dir"]]
                nx, ny = p["x"] + dx, p["y"] + dy
                if nx < 0 or nx >= GRID_SIZE or ny < 0 or ny >= GRID_SIZE or (nx, ny) in self.occupied:
                    p["alive"] = False
                else:
                    p["x"], p["y"] = nx, ny
                    p["trail"].append((nx, ny))
                    self.occupied.add((nx, ny))
                    alive_players.append(pid)

            self.telemetry["tick_count"] += 1
            self.telemetry["tick_ms"] = round((time.perf_counter() - tick_start) * 1000, 3)

            if len(alive_players) <= 1 and len(self.players) >= 2:
                self.status = "GAME_OVER"
                self.winner = alive_players[0] if alive_players else "DRAW"
                record_match_result(self.players, self.winner)

            await self.broadcast_state()

    async def broadcast_state(self):
        top_board = sorted(
            [{"name": k, **v} for k, v in LEADERBOARD.items()],
            key=lambda x: (x["wins"], -x["matches"]),
            reverse=True
        )[:5]

        payload = json.dumps({
            "type": "STATE",
            "status": self.status,
            "winner": self.winner,
            "has_bot": self.has_bot,
            "players": self.players,
            "telemetry": self.telemetry,
            "leaderboard": top_board
        })
        for ws in list(self.sockets.values()):
            try:
                await ws.send_text(payload)
            except Exception:
                pass

rooms: dict[str, GameRoom] = {}

@app.get("/")
def serve_game():
    return FileResponse("static/index.html")

@app.get("/health")
def health():
    return {"status": "online", "active_rooms": len(rooms), "tick_rate_hz": 12.5}

@app.websocket("/ws/{room_id}/{player_id}")
async def game_ws(websocket: WebSocket, room_id: str, player_id: str):
    await websocket.accept()
    room = rooms.setdefault(room_id, GameRoom(room_id))

    if len(room.players) >= 2 and player_id not in room.players:
        await websocket.send_text(json.dumps({"type": "ERROR", "message": "Room full"}))
        await websocket.close()
        return

    room.sockets[player_id] = websocket
    room.players[player_id] = {"alive": True, "trail": [], "dir": "RIGHT", "x": 0, "y": 0, "color": "#38bdf8", "is_bot": False}

    if len(room.players) == 2 and room.status != "RUNNING":
        room.reset_match()
        room.loop_task = asyncio.create_task(room.game_loop())
    else:
        await room.broadcast_state()

    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            mtype = msg.get("type")
            if mtype == "PING":
                await websocket.send_text(json.dumps({"type": "PONG", "ts": msg.get("ts")}))
            elif mtype == "TURN":
                new_dir = msg.get("dir")
                curr_dir = room.players[player_id]["dir"]
                if new_dir in DIRS and OPPOSITE[new_dir] != curr_dir:
                    room.players[player_id]["dir"] = new_dir
            elif mtype == "SPAWN_BOT" and room.status == "WAITING":
                room.add_bot(msg.get("strategy", "FLOOD_FILL"))
            elif mtype == "REMATCH" and room.status == "GAME_OVER" and len(room.players) == 2:
                if room.has_bot and msg.get("strategy"):
                    room.switch_bot_strategy(msg.get("strategy"))
                room.reset_match()
                room.loop_task = asyncio.create_task(room.game_loop())
    except WebSocketDisconnect:
        room.sockets.pop(player_id, None)
        room.players.pop(player_id, None)
        if room.has_bot:
            for k in list(room.players.keys()):
                if room.players[k].get("is_bot"):
                    room.players.pop(k, None)
            room.has_bot = False
        room.status = "WAITING"
        await room.broadcast_state()
