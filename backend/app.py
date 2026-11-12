"""REST API for the superscalar simulator.

Run:   python -m uvicorn app:app --reload        (from the backend/ folder)
Open:  http://127.0.0.1:8000        the simulator UI, now driven by this backend
       http://127.0.0.1:8000/docs   interactive API documentation
"""
import csv
import io
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from simulator.analysis import dependency_graph, run_program, static_hazards
from simulator.assembler import parse
from simulator.samples import SAMPLES
from simulator.scheduler import DEFAULT_CONFIG, UNAME, summary, to_json

ROOT = Path(__file__).resolve().parent
INDEX = ROOT.parent / 'index.html'
DB_PATH = Path(os.environ.get('SIM_DB', ROOT / 'experiments.db'))

app = FastAPI(title='Superscalar Simulator API', version='1.0.0',
              description='Cycle-by-cycle superscalar scheduling: hazards, issue, metrics and comparisons.')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


# ---------- request models ----------
class Units(BaseModel):
    ALU: int = Field(2, ge=1, le=4)
    MUL: int = Field(1, ge=1, le=4)
    MEM: int = Field(1, ge=1, le=4)
    BR: int = Field(1, ge=1, le=4)


class Latency(BaseModel):
    ALU: int = Field(1, ge=1, le=10)
    MUL: int = Field(3, ge=1, le=10)
    MEM: int = Field(2, ge=1, le=10)


class Config(BaseModel):
    width: int = Field(2, ge=1, le=8, description='Instructions fetched/decoded/issued/committed per cycle')
    units: Units = Units()
    lat: Latency = Latency()
    fwd: bool = Field(True, description='Data forwarding')
    rename: bool = Field(False, description='Register renaming')
    ooo: bool = Field(False, description='Out-of-order issue')
    pipe: bool = Field(False, description='Pipelined functional units')
    bp: Literal['stall', 'nt', '2bit'] = Field('2bit', description='Branch handling')
    window: int = Field(8, ge=2, le=32, description='Issue queue entries')


class ProgramIn(BaseModel):
    source: str = Field(..., max_length=20000, examples=[SAMPLES['Intro example']])
    config: Config = Config()


class SimulateIn(ProgramIn):
    compare: bool = Field(True, description='Also run the issue-width and hazard-solution sweeps')
    snapshots: bool = Field(True, description='Include per-cycle snapshots (needed for the animation)')


class ExperimentIn(ProgramIn):
    name: str = Field(..., min_length=1, max_length=100)
    notes: str = Field('', max_length=2000)


# ---------- helpers ----------
def _run(body: ProgramIn, compare=True):
    r = run_program(body.source, body.config.model_dump(), with_compare=compare)
    if r['errs']:
        raise HTTPException(422, detail={'errors': r['errs']})
    return r


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS experiments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, notes TEXT, source TEXT, config TEXT,
        cycles INTEGER, ipc REAL, speedup REAL, util REAL, result TEXT, created TEXT)""")
    return con


def _row(r, full=False):
    d = {k: r[k] for k in ('id', 'name', 'notes', 'cycles', 'ipc', 'speedup', 'util', 'created')}
    d['config'] = json.loads(r['config'])
    if full:
        d['source'] = r['source']
        d['result'] = json.loads(r['result'])
    return d


# ---------- endpoints ----------
@app.get('/api/health')
def health():
    return {'status': 'ok', 'engine': 'python', 'version': app.version}


@app.get('/api/samples')
def samples():
    return SAMPLES


@app.get('/api/config/default')
def default_config():
    return DEFAULT_CONFIG


@app.post('/api/assemble')
def assemble(body: ProgramIn):
    """Parse only: instructions, static hazards and the dependency graph."""
    p = parse(body.source)
    if p['errs']:
        raise HTTPException(422, detail={'errors': p['errs']})
    return {'prog': p['prog'], 'hazards': static_hazards(p['prog']), 'graph': dependency_graph(p['prog'])}


@app.post('/api/simulate')
def simulate_ep(body: SimulateIn):
    """Full cycle-by-cycle simulation. This is what the web UI calls."""
    r = _run(body, compare=body.compare)
    sim = to_json(r['sim']) if body.snapshots else summary(r['sim'])
    out = {'cfg': r['cfg'], 'prog': r['prog'], 'trace': r['trace'], 'trunc': r['trunc'],
           'hazards': r['hazards'], 'sim': sim}
    if body.compare:
        out['compare'] = r['compare']
    return out


@app.post('/api/compare')
def compare_ep(body: ProgramIn):
    """Single-issue vs multi-issue, and the effect of each hazard solution."""
    r = _run(body)
    s = r['sim']
    base = r['compare']['widths'][0]['r']['cycles']
    return {'current': {**summary(s), 'speedup': base / s['cycles']}, **r['compare']}


@app.post('/api/report')
def report(body: ProgramIn):
    """CSV report: metrics, width sweep, solution sweep, per-instruction timing."""
    r = _run(body)
    s, cmp = r['sim'], r['compare']
    base = cmp['widths'][0]['r']['cycles']
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(['Superscalar simulator report', datetime.now().strftime('%Y-%m-%d %H:%M')])
    w.writerow(['Config', json.dumps(r['cfg'])])
    w.writerow([])
    w.writerow(['Metric', 'Value'])
    for k, v in [('Total cycles', s['cycles']), ('Instructions committed', s['committed']),
                 ('IPC (throughput)', f"{s['ipc']:.3f}"), ('Speedup vs 1-issue', f"{base / s['cycles']:.3f}"),
                 ('Unit utilization', f"{s['util']:.1%}"), ('Issue slot use', f"{s['issueUtil']:.1%}"),
                 ('Branches', s['branches']), ('Mispredictions', s['mispred']), ('Flushed', s['flushedTotal'])]:
        w.writerow([k, v])
    w.writerow([])
    w.writerow(['Stall reason', 'Slots'])
    for k, v in s['st'].items():
        w.writerow([k, v])
    w.writerow([])
    w.writerow(['Issue width', 'Cycles', 'IPC', 'Speedup', 'Unit util', 'Issue slot use'])
    for x in cmp['widths']:
        q = x['r']
        w.writerow([x['w'], q['cycles'], f"{q['ipc']:.3f}", f"{x['speedup']:.3f}", f"{q['util']:.1%}", f"{q['issueUtil']:.1%}"])
    w.writerow([])
    w.writerow(['Hazard solution', 'Cycles', 'IPC'])
    for x in cmp['solutions']:
        w.writerow([x['l'], x['r']['cycles'], f"{x['r']['ipc']:.3f}"])
    w.writerow([])
    w.writerow(['#', 'PC', 'Instruction', 'Unit', 'Fetch', 'Issue', 'EX end', 'Commit', 'Flushed'])
    for x in s['all']:
        w.writerow([x.id + 1, f'I{x.pc + 1}', x.I['text'], x.unit or UNAME[x.type], x.ifC, x.s, x.exEnd, x.wb, x.flushC])
    return Response(f.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="simulation-report.csv"'})


@app.post('/api/experiments', status_code=201)
def save_experiment(body: ExperimentIn):
    """Save a run so results can be compared later."""
    r = _run(body)
    s = r['sim']
    base = r['compare']['widths'][0]['r']['cycles']
    result = {'summary': summary(s), 'compare': r['compare']}
    with closing(_db()) as con, con:
        cur = con.execute('INSERT INTO experiments(name,notes,source,config,cycles,ipc,speedup,util,result,created) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (body.name, body.notes, body.source, json.dumps(r['cfg']), s['cycles'], s['ipc'],
                           base / s['cycles'], s['util'], json.dumps(result),
                           datetime.now(timezone.utc).isoformat(timespec='seconds')))
        row = con.execute('SELECT * FROM experiments WHERE id=?', (cur.lastrowid,)).fetchone()
    return _row(row)


@app.get('/api/experiments')
def list_experiments():
    with closing(_db()) as con:
        return [_row(r) for r in con.execute('SELECT * FROM experiments ORDER BY id DESC')]


@app.get('/api/experiments/{exp_id}')
def get_experiment(exp_id: int):
    with closing(_db()) as con:
        r = con.execute('SELECT * FROM experiments WHERE id=?', (exp_id,)).fetchone()
    if not r:
        raise HTTPException(404, 'Experiment not found')
    return _row(r, full=True)


@app.delete('/api/experiments/{exp_id}', status_code=204)
def delete_experiment(exp_id: int):
    with closing(_db()) as con, con:
        n = con.execute('DELETE FROM experiments WHERE id=?', (exp_id,)).rowcount
    if not n:
        raise HTTPException(404, 'Experiment not found')
    return Response(status_code=204)


@app.get('/', include_in_schema=False)
def ui():
    return FileResponse(INDEX)
