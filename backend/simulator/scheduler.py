"""Cycle-accurate superscalar timing simulator.

Pipeline: IF -> ID (decode/rename) -> IS (issue queue) -> EX (functional unit,
k cycles) -> WB (in-order commit through a reorder buffer).

Every cycle produces a snapshot of where each instruction is and why anything
stalled, so the frontend can replay the run step by step.
"""
import copy

TYPES = ['ALU', 'MUL', 'MEM', 'BR']
UNAME = {'ALU': 'ALU', 'MUL': 'MUL', 'MEM': 'LSU', 'BR': 'BRU'}
CYCLE_LIMIT = 4000

DEFAULT_CONFIG = {
    'width': 2,
    'units': {'ALU': 2, 'MUL': 1, 'MEM': 1, 'BR': 1},
    'lat': {'ALU': 1, 'MUL': 3, 'MEM': 2},
    'fwd': True, 'rename': False, 'ooo': False, 'pipe': False,
    'bp': '2bit',      # 'stall' | 'nt' (predict not taken) | '2bit'
    'window': 8,       # issue queue size
}


def make_config(over=None):
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


class Inst:
    """One dynamic (fetched) instruction."""

    def __init__(self, seq, pc, I, wrong, tr, cycle):
        self.id = self.seq = seq
        self.pc, self.I, self.wrong, self.tr = pc, I, wrong, tr
        self.type, self.dest, self.srcs = I['type'], I['dest'], I['srcs']
        self.cond = I['kind'] == 'B'
        self.ifC = cycle
        self.idC = self.isC = self.s = self.exEnd = self.wb = self.flushC = None
        self.unit = self.pdest = self.psrc = self.pred = self.mis = self.prod = None
        self.decoded = False
        self.hist = {}


class Unit:
    def __init__(self, t, name):
        self.t, self.name, self.occ = t, name, []


def tag(x):
    return f'#{x.id + 1}'


def R(r):
    return f'R{r}'


def _pull(arr, x):
    for i, y in enumerate(arr):
        if y is x:
            del arr[i]
            return


def _tn(b):
    return 'taken' if b else 'not taken'


def _pl(n, one='', many='s'):
    return one if n == 1 else many


def simulate(prog, trace, cfg):
    W = cfg['width']
    lat = lambda t: 1 if t == 'BR' else cfg['lat'][t]
    units = [Unit(t, UNAME[t] + str(i)) for t in TYPES for i in range(cfg['units'][t])]
    all_, IF, ID, IS, inflight = [], [], [], [], []
    seq = trace_pos = cycle = 0
    wrong_pc = fetch_block = None
    reg_writer, ctr = {}, {}
    regs = [0] * 16
    phys = 16
    arch_map = {r: f'P{r}' for r in range(16)}
    rmap = dict(arch_map)
    st = dict.fromkeys(['RAW', 'WAR', 'WAW', 'STRUCT', 'WIDTH', 'ORDER', 'MEMORD', 'FULL', 'CTRL'], 0)
    st_by_type = dict.fromkeys(TYPES, 0)
    mispred = branches = flushed_total = committed = busy_unit_cycles = issued_total = 0
    unit_busy = {u.name: 0 for u in units}
    snaps = [{'c': 0, 'IF': [], 'ID': [], 'IS': [],
              'EX': [{'name': u.name, 't': u.t, 'ids': []} for u in units],
              'ROB': [], 'WB': [], 'FL': [], 'reasons': {},
              'ev': [{'k': 'info', 'm': 'Pipeline is empty. Press Step or Play to start fetching instructions.'}],
              'regs': regs[:], 'writers': {}, 'rmap': dict(rmap), 'ctr': {}, 'spec': [],
              'committed': 0, 'issued': 0, 'flushInfo': None}]

    def recompute():
        nonlocal reg_writer, rmap
        reg_writer = {}
        rmap = dict(arch_map)
        for x in inflight:
            if x.decoded and x.dest:
                reg_writer[x.dest] = x
                if x.pdest:
                    rmap[x.dest] = x.pdest

    while cycle < CYCLE_LIMIT:
        cycle += 1
        ev, FL, reasons = [], [], {}
        flush_info = None
        for u in units:
            u.occ = [o for o in u.occ if not o.flushC and o.exEnd >= cycle]

        # 1. branches that finished EX last cycle resolve now
        for b in list(inflight):
            if b.flushC or not b.cond or b.exEnd != cycle - 1:
                continue
            if b.wrong:
                continue
            branches += 1
            act = b.tr['taken']
            nxt = (b.I['target'] if act else b.pc + 1) + 1
            if cfg['bp'] == 'stall':
                fetch_block = None
                ev.append({'k': 'branch', 'm': f'{tag(b)} {b.I["op"]} resolved: {_tn(act)}. Fetch restarts at I{nxt}.'})
            else:
                if cfg['bp'] == '2bit':
                    c = ctr.get(b.pc, 1)
                    ctr[b.pc] = min(3, c + 1) if act else max(0, c - 1)
                if b.pred != act:
                    mispred += 1
                    b.mis = True
                    vict = [x for x in inflight if x.seq > b.seq]
                    for v in vict:
                        v.flushC = cycle
                        FL.append(v.id)
                        for arr in (IF, ID, IS, inflight):
                            _pull(arr, v)
                        for u in units:
                            _pull(u.occ, v)
                    flushed_total += len(vict)
                    wrong_pc = None
                    recompute()
                    flush_info = {'br': b.id, 'n': len(vict)}
                    n = len(vict)
                    lst = f' ({", ".join(tag(v) for v in vict)})' if n else ''
                    ev.append({'k': 'flush', 'm': f'{tag(b)} {b.I["op"]} MISPREDICTED: guessed {_tn(b.pred)}, actually {_tn(act)}. '
                               f'Flushed {n} wrong-path instruction{_pl(n)}{lst}. Fetch redirected to I{nxt}.'})
                else:
                    ev.append({'k': 'branch', 'm': f'{tag(b)} {b.I["op"]} resolved: prediction "{_tn(act)}" was correct, no penalty.'})

        # 2. commit / write-back, in program order
        WB = []
        while len(WB) < W and inflight:
            h = inflight[0]
            if h.exEnd is not None and h.exEnd < cycle:
                h.wb = cycle
                inflight.pop(0)
                WB.append(h.id)
                committed += 1
                if h.dest:
                    regs[h.dest] = h.tr['result']
                    if reg_writer.get(h.dest) is h:
                        del reg_writer[h.dest]
                    if h.pdest:
                        arch_map[h.dest] = h.pdest
            else:
                break
        if WB:
            parts = [tag(all_[i]) + (f' ({R(all_[i].dest)} = {all_[i].tr["result"]})' if all_[i].dest else '') for i in WB]
            ev.append({'k': 'commit', 'm': f'Write-back: {", ".join(parts)}.'})

        # 3. issue from the issue queue
        issued = 0
        blocker = None

        def check(x):
            for pr in x.prod:
                r, p = pr['r'], pr['p']
                if not p:
                    continue
                ok = (p.exEnd is not None and p.exEnd < cycle) if cfg['fwd'] else (p.wb is not None and p.wb < cycle)
                if not ok:
                    if p.exEnd is None:
                        when = 'it has not started executing'
                    elif cfg['fwd']:
                        when = f'its result is forwarded at the end of cycle {p.exEnd}'
                    else:
                        when = 'no forwarding, so it must wait for write-back'
                    return {'k': 'RAW', 's': f'RAW {R(r)}←{tag(p)}',
                            'm': f'{tag(x)} stalled, RAW hazard: needs {R(r)} from {tag(p)} {p.I["op"]}; {when}.'}
            if not cfg['rename'] and x.dest:
                for y in inflight:
                    if y.seq >= x.seq:
                        break
                    if y.dest == x.dest:
                        return {'k': 'WAW', 's': f'WAW {R(x.dest)} vs {tag(y)}',
                                'm': f'{tag(x)} stalled, WAW hazard: older {tag(y)} also writes {R(x.dest)} and has not written it yet. Renaming would remove this.'}
                    if y.s is None and x.dest in y.srcs:
                        return {'k': 'WAR', 's': f'WAR {R(x.dest)} vs {tag(y)}',
                                'm': f'{tag(x)} stalled, WAR hazard: older {tag(y)} has not read {R(x.dest)} yet, so {tag(x)} cannot overwrite it. Renaming would remove this.'}
            if x.type == 'MEM':
                for y in inflight:
                    if y.seq >= x.seq:
                        break
                    if y.type == 'MEM' and y.s is None:
                        return {'k': 'MEMORD', 's': f'mem order {tag(y)}',
                                'm': f'{tag(x)} waits: memory operations go in order, {tag(y)} first.'}
            cands = [u for u in units if u.t == x.type]
            if cfg['pipe']:
                free = next((u for u in cands if not any(o.s == cycle for o in u.occ)), None)
            else:
                free = next((u for u in cands if not any(o.exEnd >= cycle for o in u.occ)), None)
            if not free:
                busy = [tag(o) for u in cands for o in u.occ if o.exEnd >= cycle]
                n = len(cands)
                return {'k': 'STRUCT', 's': f'STRUCT {UNAME[x.type]} busy',
                        'm': f'{tag(x)} stalled, structural hazard: all {n} {UNAME[x.type]} unit{"s are" if n > 1 else " is"} busy'
                             f'{" with " + ", ".join(busy) if busy else ""}.'}
            if issued >= W:
                return {'k': 'WIDTH', 's': 'issue width full',
                        'm': f'{tag(x)} waits: {W} instruction{"s" if W > 1 else ""} already issued this cycle (issue width {W}).'}
            return {'ok': True, 'unit': free}

        for x in [x for x in IS if x.isC < cycle]:
            if not cfg['ooo'] and blocker:
                own = check(x)
                if own.get('ok') or own['k'] == 'WIDTH':
                    r = {'k': 'ORDER', 's': f'in-order: behind {tag(blocker)}',
                         'm': f'{tag(x)} is ready but waits: in-order issue cannot pass stalled {tag(blocker)}.'}
                else:
                    r = own
            else:
                r = check(x)
            if not r.get('ok'):
                reasons[x.id] = r
                st[r['k']] += 1
                if r['k'] == 'STRUCT':
                    st_by_type[x.type] += 1
                if not blocker:
                    blocker = x
                if r['k'] not in ('WIDTH', 'ORDER'):
                    ev.append({'k': 'stall', 'm': r['m']})
                continue
            u = r['unit']
            L = lat(x.type)
            x.s = cycle
            x.exEnd = cycle + L - 1
            x.unit = u.name
            u.occ.append(x)
            _pull(IS, x)
            issued += 1
            issued_total += 1
            ev.append({'k': 'issue', 'm': f'{tag(x)} {x.I["text"]} issued to {u.name}{f" (busy {L} cycles)" if L > 1 else ""}.'})
        ws = sum(1 for r in reasons.values() if r['k'] == 'WIDTH')
        os_ = sum(1 for r in reasons.values() if r['k'] == 'ORDER')
        if ws:
            ev.append({'k': 'stall', 'm': f'{ws} ready instruction{"s" if ws > 1 else ""} left waiting: issue width ({W}) used up.'})
        if os_:
            ev.append({'k': 'stall', 'm': f'{os_} ready instruction{"s" if os_ > 1 else ""} blocked by in-order issue. '
                                          f'Out-of-order issue would let {"them" if os_ > 1 else "it"} go.'})

        # 4. ID -> IS (dispatch)
        while ID and len(IS) < cfg['window']:
            x = ID.pop(0)
            x.isC = cycle
            IS.append(x)
        if ID:
            for x in ID:
                reasons[x.id] = {'k': 'FULL', 's': 'issue queue full', 'm': f'{tag(x)} cannot enter the full issue queue.'}
            st['FULL'] += len(ID)

        # 5. IF -> ID (decode + rename)
        dec = []
        while IF and len(ID) < W and IF[0].ifC < cycle:
            x = IF.pop(0)
            x.idC = cycle
            uniq = []
            for r in x.srcs:
                if r and r not in uniq:
                    uniq.append(r)
            x.prod = [{'r': r, 'p': reg_writer.get(r)} for r in uniq]
            x.psrc = [rmap[r] if r else 'P0' for r in x.srcs]
            if x.dest:
                reg_writer[x.dest] = x
                if cfg['rename']:
                    x.pdest = f'P{phys}'
                    phys += 1
                    rmap[x.dest] = x.pdest
            x.decoded = True
            ID.append(x)
            dec.append(x)
        if dec and cfg['rename']:
            rn = [x for x in dec if x.pdest]
            if rn:
                ev.append({'k': 'info', 'm': f'Rename: {", ".join(f"{tag(x)} {R(x.dest)}→{x.pdest}" for x in rn)}.'})

        # 6. fetch
        fet = []
        if fetch_block:
            st['CTRL'] += 1
            ev.append({'k': 'stall', 'm': f'Fetch stalled: waiting for branch {tag(fetch_block)} to resolve (control hazard).'})
        else:
            while len(IF) < W:
                tr, wrong = None, False
                if wrong_pc is not None:
                    if wrong_pc < 0 or wrong_pc >= len(prog):
                        break
                    pc, wrong = wrong_pc, True
                else:
                    if trace_pos >= len(trace):
                        break
                    tr = trace[trace_pos]
                    trace_pos += 1
                    pc = tr['pc']
                I = prog[pc]
                x = Inst(seq, pc, I, wrong, tr, cycle)
                seq += 1
                all_.append(x); inflight.append(x); IF.append(x); fet.append(x)
                stop = False
                if x.cond:
                    if cfg['bp'] == 'stall':
                        fetch_block = x
                        stop = True
                    else:
                        pred = False if cfg['bp'] == 'nt' else ctr.get(pc, 1) >= 2
                        x.pred = pred
                        nx = I['target'] if pred else pc + 1
                        if not wrong:
                            if pred != tr['taken']:
                                wrong_pc = nx
                        else:
                            wrong_pc = nx
                        if pred:
                            stop = True
                elif I['kind'] == 'J':
                    if wrong:
                        wrong_pc = I['target']
                    stop = True
                elif I['kind'] == 'H':
                    if wrong:
                        wrong_pc = -1
                    stop = True
                elif wrong:
                    wrong_pc = pc + 1
                if stop:
                    break
            if fet:
                br = [x for x in fet if x.cond]
                m = f'Fetched {", ".join(tag(x) + " " + x.I["op"] for x in fet)}.'
                if br and cfg['bp'] != 'stall':
                    m += f' Predicted {", ".join(tag(b) + " " + _tn(b.pred) for b in br)}.'
                if br and cfg['bp'] == 'stall':
                    m += ' Branch found: fetch stops until it resolves.'
                ev.insert(0, {'k': 'fetch', 'm': m})

        # bookkeeping + snapshot
        EX = []
        for u in units:
            ids = [o.id for o in u.occ if o.s <= cycle and o.exEnd >= cycle]
            if ids:
                busy_unit_cycles += 1
                unit_busy[u.name] += 1
            EX.append({'name': u.name, 't': u.t, 'ids': ids})
        ROB = [x.id for x in inflight if x.exEnd is not None and x.exEnd < cycle]
        for i in ROB:
            h = inflight[0]
            reasons[i] = {'k': 'ROB', 's': f'done, commit after {tag(h)}',
                          'm': f'{tag(all_[i])} finished executing but must commit in program order, after {tag(h)}.'}
        spec = []
        if cfg['bp'] != 'stall':
            on = False
            for x in inflight:
                if on:
                    spec.append(x.id)
                if x.cond and (x.exEnd is None or x.exEnd >= cycle):
                    on = True
        snaps.append({'c': cycle, 'IF': [x.id for x in IF], 'ID': [x.id for x in ID], 'IS': [x.id for x in IS],
                      'EX': EX, 'ROB': ROB, 'WB': WB, 'FL': FL, 'reasons': reasons, 'ev': ev, 'regs': regs[:],
                      'writers': {r: x.id for r, x in reg_writer.items()}, 'rmap': dict(rmap), 'ctr': dict(ctr),
                      'spec': spec, 'committed': committed, 'issued': issued, 'flushInfo': flush_info})
        if trace_pos >= len(trace) and not inflight:
            break

    # per-instruction stage history for the timing diagram
    for s in snaps:
        c = s['c']
        for i in s['IF']: all_[i].hist[c] = 'IF'
        for i in s['ID']: all_[i].hist[c] = 'ID'
        for i in s['IS']: all_[i].hist[c] = 'ST' if i in s['reasons'] else 'IS'
        for u in s['EX']:
            for i in u['ids']:
                all_[i].hist[c] = 'E' + str(c - all_[i].s + 1)
        for i in s['ROB']: all_[i].hist[c] = 'RB'
        for i in s['WB']: all_[i].hist[c] = 'WB'
        for i in s['FL']: all_[i].hist[c] = 'FL'

    cycles = len(snaps) - 1
    return {'all': all_, 'snaps': snaps, 'units': units, 'unitBusy': unit_busy, 'cycles': cycles,
            'committed': committed, 'ipc': committed / max(1, cycles),
            'util': busy_unit_cycles / max(1, len(units) * cycles),
            'issueUtil': issued_total / max(1, W * cycles),
            'st': st, 'stByType': st_by_type, 'mispred': mispred, 'branches': branches,
            'flushedTotal': flushed_total, 'limit': cycle >= CYCLE_LIMIT}


SUMMARY_KEYS = ['cycles', 'committed', 'ipc', 'util', 'issueUtil', 'st', 'stByType',
                'mispred', 'branches', 'flushedTotal', 'unitBusy', 'limit']


def summary(sim):
    """Just the performance numbers (no per-cycle data)."""
    return {k: sim[k] for k in SUMMARY_KEYS}


def to_json(sim):
    """Full JSON-safe result, including per-cycle snapshots, for the UI."""
    def inst(x):
        return {'id': x.id, 'pc': x.pc, 'type': x.type, 'dest': x.dest, 'srcs': x.srcs, 'wrong': x.wrong,
                'cond': x.cond, 'tr': x.tr, 'ifC': x.ifC, 'idC': x.idC, 'isC': x.isC, 's': x.s,
                'exEnd': x.exEnd, 'unit': x.unit, 'wb': x.wb, 'flushC': x.flushC, 'pdest': x.pdest,
                'psrc': x.psrc, 'pred': x.pred, 'mis': x.mis,
                'prod': None if x.prod is None else [{'r': p['r'], 'p': p['p'].id if p['p'] else None} for p in x.prod],
                'hist': {str(k): v for k, v in x.hist.items()}}

    def snap(s):
        o = dict(s)
        o['reasons'] = {str(k): {'k': v['k'], 's': v['s'], 'm': v['m']} for k, v in s['reasons'].items()}
        for key in ('writers', 'rmap', 'ctr'):
            o[key] = {str(k): v for k, v in s[key].items()}
        return o

    out = summary(sim)
    out['units'] = [{'name': u.name, 't': u.t} for u in sim['units']]
    out['all'] = [inst(x) for x in sim['all']]
    out['snaps'] = [snap(s) for s in sim['snaps']]
    return out
