# Superscalar Scheduling Simulator

**Live demo:** https://vighnesh-sv.github.io/superscalar-simulator/

Computer Architecture and Organization project (Unit 3: Pipeline Processing).

A cycle-by-cycle simulator of a superscalar processor. It takes a short assembly program, finds data dependencies (RAW, WAR, WAW) and resource conflicts, schedules the instructions on multiple execution units, and compares single-issue with multi-issue execution (total cycles, IPC, utilization, speedup).

## Run it

**With the backend (full product):** double-click `run.bat`, or:

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app:app --port 8000
```

Then open http://127.0.0.1:8000. The header shows **Engine: Python backend**. API docs are at http://127.0.0.1:8000/docs.

**Without the backend:** open `index.html` in any browser. It falls back to the same engine in JavaScript (this is what the live demo uses). Saving runs and the CSV report need the backend.

Live version: https://claude.ai/artifact/FRTp5aDL1iseGVdUUEmYxv

## Backend

Python 3.10+ with FastAPI. The scheduling engine lives in `backend/simulator/`:

| File | Job |
|---|---|
| `assembler.py` | Parses the assembly text and runs the program once to get the real branch outcomes |
| `scheduler.py` | Cycle-by-cycle pipeline: fetch, decode/rename, issue, execute, in-order commit |
| `analysis.py` | Static RAW/WAR/WAW detection, dependency graph, issue-width and hazard-solution comparisons |
| `app.py` | REST API, serves the UI, stores saved runs in SQLite |

| Endpoint | What it does |
|---|---|
| `POST /api/simulate` | Full simulation with a snapshot of every cycle (the UI calls this) |
| `POST /api/compare` | 1/2/4/8-issue comparison and the gain from each hazard solution |
| `POST /api/assemble` | Parsed instructions, hazards and dependency graph only |
| `POST /api/report` | CSV report of the run |
| `GET/POST/DELETE /api/experiments` | Saved runs |
| `GET /api/samples`, `/api/config/default`, `/api/health` | Sample programs, default settings, status |

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests
```

- `test_engine.py`: hand-traced cases (a lone instruction takes 5 cycles, forwarding removes the RAW stall, one multiplier causes a structural hazard, and so on).
- `test_parity.py`: runs the JavaScript engine from `index.html` and checks the Python engine gives the identical result, cycle by cycle, for every sample program in 146 processor configurations.
- `test_api.py`: every endpoint.

## Features

- Live animated pipeline: IF, ID, IS (issue queue), EX (ALU, multiplier, load/store, branch units), WB
- Hazard detection with the exact reason for every stall
- Hazard solutions you can switch on and off: data forwarding, register renaming, out-of-order issue, pipelined units, branch prediction (stall, predict not-taken, 2-bit predictor with flush)
- Timing (space-time) diagram, dependency graph, hazard table
- Performance comparison of 1, 2, 4 and 8-issue execution and of each hazard solution

## Tech stack

Frontend: HTML5, CSS3 and vanilla JavaScript in one file. Animations use the Web Animations API and the dependency graph uses inline SVG. Backend: Python, FastAPI, SQLite.

## Planned features

Planned features are tracked in a separate document shared with the team.

## Project structure

- `index.html` - the user interface (single file, no build step)
- `backend/` - Python simulation engine, REST API and tests
- `run.bat` - starts the backend and opens the UI
- `docs/Planned-Features.docx` - planned next features (error mode, hazard diagnosis panel)

## Working on it (for teammates)

```bash
git clone https://github.com/Vighnesh-SV/superscalar-simulator.git
cd superscalar-simulator
# open index.html in any browser - that's it, no install needed
```

Workflow:
1. `git pull` before you start.
2. Make a branch: `git checkout -b your-feature`
3. Edit `index.html`, refresh the browser to test.
4. `git add . && git commit -m "what you changed"` then `git push -u origin your-feature`
5. Open a Pull Request on GitHub. Once merged into `main`, the live demo updates automatically.
