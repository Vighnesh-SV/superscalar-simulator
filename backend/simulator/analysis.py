"""Static hazard analysis and performance comparisons."""
from .assembler import parse, execute
from .scheduler import simulate, summary, make_config, UNAME

NO_SOLUTIONS = {'fwd': False, 'rename': False, 'ooo': False, 'pipe': False, 'bp': 'stall'}
ALL_SOLUTIONS = {'fwd': True, 'rename': True, 'ooo': True, 'pipe': True, 'bp': '2bit'}


def static_hazards(prog):
    """Every RAW / WAR / WAW / control hazard visible in the program text."""
    out, seen = [], set()

    def add(t, i, j, r):
        k = (t, i, j, r)
        if k not in seen:
            seen.add(k)
            out.append({'t': t, 'i': i, 'j': j, 'r': r})

    for j, J in enumerate(prog):
        for r in J['srcs']:
            if not r:
                continue
            for i in range(j - 1, -1, -1):
                if prog[i]['dest'] == r:
                    add('RAW', i, j, r)
                    break
        if J['dest']:
            w = -1
            for i in range(j - 1, -1, -1):
                if prog[i]['dest'] == J['dest']:
                    w = i
                    break
            if w >= 0:
                add('WAW', w, j, J['dest'])
            for i in range(w + 1, j):
                if J['dest'] in prog[i]['srcs']:
                    add('WAR', i, j, J['dest'])
        if J['kind'] == 'B':
            add('CTRL', j, j, 0)
    return out


def dependency_graph(prog):
    """Nodes and edges in a plain format (for reports or other front ends)."""
    nodes = [{'id': i, 'label': f'I{i + 1}', 'text': I['text'], 'unit': UNAME[I['type']]} for i, I in enumerate(prog)]
    edges = [{'from': h['i'], 'to': h['j'], 'type': h['t'], 'reg': f'R{h["r"]}'}
             for h in static_hazards(prog) if h['t'] != 'CTRL']
    return {'nodes': nodes, 'edges': edges}


def compare(prog, trace, cfg, widths=(1, 2, 4, 8)):
    """Issue-width sweep and one-solution-at-a-time sweep, as summaries."""
    def run(over):
        return summary(simulate(prog, trace, make_config({**cfg, **over})))

    wr = [{'w': w, 'r': run({'width': w})} for w in widths]
    base = wr[0]['r']['cycles']
    for x in wr:
        x['speedup'] = base / x['r']['cycles']
    sols = [('No solutions (stall only)', NO_SOLUTIONS),
            ('+ Forwarding', {**NO_SOLUTIONS, 'fwd': True}),
            ('+ Renaming', {**NO_SOLUTIONS, 'rename': True}),
            ('+ Out-of-order', {**NO_SOLUTIONS, 'ooo': True}),
            ('+ Pipelined units', {**NO_SOLUTIONS, 'pipe': True})]
    if any(I['kind'] == 'B' for I in prog):
        sols.append(('+ 2-bit prediction', {**NO_SOLUTIONS, 'bp': '2bit'}))
    sols.append(('All solutions', ALL_SOLUTIONS))
    return {'widths': wr, 'solutions': [{'l': l, 'r': run(o)} for l, o in sols]}


def run_program(source, cfg_over=None, with_compare=True):
    """Assemble, execute and simulate. The single entry point used by the API and CLI."""
    p = parse(source)
    if p['errs']:
        return {'errs': p['errs']}
    cfg = make_config(cfg_over)
    ex = execute(p['prog'])
    sim = simulate(p['prog'], ex['trace'], cfg)
    out = {'errs': [], 'cfg': cfg, 'prog': p['prog'], 'trace': ex['trace'], 'trunc': ex['trunc'],
           'sim': sim, 'hazards': static_hazards(p['prog'])}
    if with_compare:
        out['compare'] = compare(p['prog'], ex['trace'], cfg)
    return out
