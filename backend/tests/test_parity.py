"""The Python engine must produce exactly what the browser engine in index.html produces.

Runs the original JavaScript (pulled straight out of index.html) in an embedded
JS interpreter and compares every snapshot of every cycle for many configurations.
"""
import itertools
import json
from pathlib import Path

import pytest

quickjs = pytest.importorskip('quickjs')

from simulator.analysis import run_program, static_hazards
from simulator.samples import SAMPLES
from simulator.scheduler import to_json

HTML = (Path(__file__).resolve().parents[2] / 'index.html').read_text(encoding='utf8')
JS_ENGINE = HTML[HTML.index('const SAMPLES = {'):HTML.index('/* ================= STATE')]
JS_SERIALIZE = r"""
function runAll(src, cfgJson){
  const cfg = JSON.parse(cfgJson);
  const p = parse(src), ex = execute(p.prog), s = simulate(p.prog, ex.trace, cfg);
  const n = v => v === undefined ? null : v;
  return JSON.stringify({
    hazards: staticHazards(p.prog), trace: ex.trace,
    sim: {cycles:s.cycles, committed:s.committed, ipc:s.ipc, util:s.util, issueUtil:s.issueUtil, st:s.st,
      stByType:s.stByType, mispred:s.mispred, branches:s.branches, flushedTotal:s.flushedTotal,
      unitBusy:s.unitBusy, limit:s.limit, units:s.units.map(u=>({name:u.name,t:u.t})),
      all: s.all.map(x=>({id:x.id,pc:x.pc,type:x.type,dest:x.dest,srcs:x.srcs,wrong:x.wrong,cond:x.cond,tr:n(x.tr),
        ifC:x.ifC,idC:n(x.idC),isC:n(x.isC),s:n(x.s),exEnd:n(x.exEnd),unit:n(x.unit),wb:n(x.wb),flushC:n(x.flushC),
        pdest:n(x.pdest),psrc:n(x.psrc),pred:n(x.pred),mis:n(x.mis),
        prod:x.prod?x.prod.map(q=>({r:q.r,p:q.p?q.p.id:null})):null,hist:x.hist})),
      snaps: s.snaps.map(q=>Object.assign({}, q, {reasons:Object.fromEntries(Object.entries(q.reasons).map(([k,v])=>[k,{k:v.k,s:v.s,m:v.m}]))}))}
  });
}
"""

ctx = quickjs.Context()
ctx.eval(JS_ENGINE + JS_SERIALIZE)


def js_run(src, cfg):
    return json.loads(ctx.eval(f'runAll({json.dumps(src)}, {json.dumps(json.dumps(cfg))})'))


def py_run(src, cfg):
    r = run_program(src, cfg, with_compare=False)
    return json.loads(json.dumps({'hazards': static_hazards(r['prog']), 'trace': r['trace'], 'sim': to_json(r['sim'])}))


CONFIGS = []
for width, fwd, rename, ooo, pipe, bp in itertools.product([1, 2, 4], [False, True], [False, True],
                                                          [False, True], [False, True], ['stall', 'nt', '2bit']):
    CONFIGS.append({'width': width, 'units': {'ALU': 2, 'MUL': 1, 'MEM': 1, 'BR': 1},
                    'lat': {'ALU': 1, 'MUL': 3, 'MEM': 2}, 'fwd': fwd, 'rename': rename,
                    'ooo': ooo, 'pipe': pipe, 'bp': bp, 'window': 8})
# a few unusual hardware setups
CONFIGS += [
    {'width': 8, 'units': {'ALU': 4, 'MUL': 2, 'MEM': 2, 'BR': 2}, 'lat': {'ALU': 2, 'MUL': 5, 'MEM': 3},
     'fwd': True, 'rename': True, 'ooo': True, 'pipe': True, 'bp': '2bit', 'window': 32},
    {'width': 4, 'units': {'ALU': 1, 'MUL': 1, 'MEM': 1, 'BR': 1}, 'lat': {'ALU': 1, 'MUL': 6, 'MEM': 4},
     'fwd': False, 'rename': False, 'ooo': True, 'pipe': False, 'bp': 'nt', 'window': 2},
]


@pytest.mark.parametrize('name', list(SAMPLES))
def test_same_as_browser_engine(name):
    src = SAMPLES[name]
    for cfg in CONFIGS:
        assert py_run(src, cfg) == js_run(src, cfg), f'{name} differs with {cfg}'


def test_arithmetic_edge_cases():
    src = """ADDI R1, R0, 2147483647
ADDI R2, R1, 1
MUL  R3, R1, R1
SUBI R4, R0, 7
ADDI R5, R0, 2
DIV  R6, R4, R5
DIV  R7, R4, R0
SW   R4, -3(R0)
LW   R8, 61(R0)
SLT  R9, R4, R0
HALT
ADD  R10, R1, R1"""
    for cfg in CONFIGS[::7]:
        assert py_run(src, cfg) == js_run(src, cfg)


def test_parse_errors_match():
    bad = "ADD R1, R2\nFOO R1\nLW R1, R2\nBEQ R1, R2, nowhere\nADDI R1, R2, x\nADD R16, R1, R1"
    from simulator.assembler import parse
    js = json.loads(ctx.eval(f'JSON.stringify(parse({json.dumps(bad)}).errs)'))
    assert parse(bad)['errs'] == js
