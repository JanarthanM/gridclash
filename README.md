# GridClash — Real-Time Authoritative Multiplayer Engine & Telemetry Sandbox

A low-latency, server-authoritative multiplayer light-cycle arena built with **Python (FastAPI + Asyncio + WebSockets)** and **HTML5 Canvas**.

## Key Engineering Highlights
- **Authoritative 12.5 Hz Simulation Loop:** Deterministic server-side state ticks, collision resolution, and input validation blocking invalid 180-degree self-reversals.
- **Dynamic AI Heuristic Opponents:** Supports runtime switching between **BFS Flood-Fill Territory Evaluation** and **Greedy Raycast** pathfinding with live decision latency (`ms`) and graph node-scan telemetry.
- **Real-Time Network & Engine Telemetry:** Streams live WebSocket Round-Trip Time (RTT ping), server tick execution duration, frame payload byte sizes, and a session leaderboard.
- **Single-Container Deployment:** Serves both the WebSocket API and static HTML5 Canvas frontend from a single FastAPI service.

## Run Locally
```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
