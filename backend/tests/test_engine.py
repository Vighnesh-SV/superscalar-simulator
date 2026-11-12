"""Hand-traced checks of the scheduler. Each expected number was worked out on paper."""
from simulator.analysis import run_program, static_hazards
from simulator.assembler import parse
from simulator.samples import SAMPLES

SCALAR = {'width': 1, 'fwd': True, 'rename': False, 'ooo': False, 'pipe': False, 'bp': '2bit'}


def run(src, **cfg):
    return run_program(src, cfg, with_compare=False)['sim']


def test_parser_reads_all_formats():
    p = parse('loop: ADD R1, R2, R3\nADDI R4, R1, -5\nLW R5, 8(R1)\nSW R5, 0(R2)\nBNE R1, R4, loop\nJ loop\nNOP')
    assert not p['errs']
    add, addi, lw, sw, bne, j, nop = p['prog']
    assert (add['dest'], add['srcs']) == (1, [2, 3])
    assert (addi['imm'], lw['imm'], lw['srcs'], sw['srcs']) == (-5, 8, [1], [5, 2])
    assert bne['target'] == 0 and j['target'] == 0 and nop['type'] == 'ALU'


def test_parser_reports_errors_with_line_numbers():
    errs = parse('ADD R1, R2\nFOO R1\nBEQ R1, R2, nowhere')['errs']
    assert errs[0].startswith('Line 1') and 'Line 2' in errs[1] and 'nowhere' in errs[2]


def test_static_hazards():
    p = parse('ADD R1, R2, R3\nSUB R4, R1, R5\nADD R5, R6, R7\nADD R1, R8, R9')['prog']
    kinds = {(h['t'], h['i'], h['j']) for h in static_hazards(p)}
    assert ('RAW', 0, 1) in kinds          # I2 reads R1 written by I1
    assert ('WAR', 1, 2) in kinds          # I3 overwrites R5 that I2 reads
    assert ('WAW', 0, 3) in kinds          # I1 and I4 both write R1
    assert ('WAR', 1, 3) in kinds          # I4 overwrites R1 that I2 reads


def test_single_instruction_takes_five_cycles():
    # IF(1) ID(2) IS(3) EX(4) WB(5)
    s = run('ADD R1, R2, R3', **SCALAR)
    assert s['cycles'] == 5 and s['committed'] == 1


def test_scalar_independent_stream():
    # n independent 1-cycle ops on a 1-wide pipe: 5 + (n - 1) cycles
    src = '\n'.join(f'ADD R{i}, R0, R0' for i in range(1, 9))
    assert run(src, **SCALAR)['cycles'] == 5 + 7


def test_two_issue_halves_independent_work():
    src = '\n'.join(f'ADD R{i}, R0, R0' for i in range(1, 9))
    s2 = run(src, **{**SCALAR, 'width': 2})['cycles']
    assert s2 == 5 + 3                      # 4 groups of 2, two ALUs


def test_forwarding_shortens_raw_stall():
    src = 'ADD R1, R2, R3\nADD R4, R1, R1'
    with_fwd = run(src, **SCALAR)
    no_fwd = run(src, **{**SCALAR, 'fwd': False})
    assert with_fwd['cycles'] == 6          # back-to-back through the bypass
    assert with_fwd['st']['RAW'] == 0
    assert no_fwd['st']['RAW'] > 0 and no_fwd['cycles'] > with_fwd['cycles']


def test_dependency_chain_gets_little_from_width():
    src = 'ADDI R1, R0, 1\n' + '\n'.join(f'ADD R{i + 1}, R{i}, R{i}' for i in range(1, 8))
    c1 = run(src, **SCALAR)['cycles']
    c4 = run(src, **{**SCALAR, 'width': 4})['cycles']
    assert c1 - c4 <= 1                     # only pipeline fill changes


def test_structural_hazard_on_single_multiplier():
    src = 'MUL R1, R2, R3\nMUL R4, R5, R6'
    s = run(src, **{**SCALAR, 'width': 2})
    assert s['st']['STRUCT'] > 0 and s['stByType']['MUL'] > 0
    two = run(src, **{**SCALAR, 'width': 2, 'units': {'MUL': 2}})
    assert two['st']['STRUCT'] == 0 and two['cycles'] < s['cycles']
    piped = run(src, **{**SCALAR, 'width': 2, 'pipe': True})
    assert piped['cycles'] < s['cycles']


def test_renaming_removes_false_dependencies():
    src = 'MUL R1, R2, R3\nADD R4, R1, R1\nADD R1, R5, R6\nADD R7, R1, R1'
    base = run(src, **{**SCALAR, 'width': 2, 'ooo': True})
    ren = run(src, **{**SCALAR, 'width': 2, 'ooo': True, 'rename': True})
    assert base['st']['WAW'] + base['st']['WAR'] > 0
    assert ren['st']['WAW'] + ren['st']['WAR'] == 0
    assert ren['cycles'] <= base['cycles']


def test_branch_prediction_and_results():
    src = SAMPLES['Array sum loop (branches)']
    s = run(src, bp='2bit')
    assert s['branches'] == 8               # loop body runs 8 times
    assert 1 <= s['mispred'] < 8            # the 2-bit counter learns
    assert s['snaps'][-1]['regs'][3] == sum(range(1, 9))   # memory holds i + 1, so sum = 36
    stall = run(src, bp='stall')
    assert stall['mispred'] == 0 and stall['st']['CTRL'] > 0
    assert s['cycles'] < stall['cycles']


def test_metrics_are_consistent():
    for src in SAMPLES.values():
        for w in (1, 2, 4, 8):
            s = run(src, width=w)
            assert s['ipc'] == s['committed'] / s['cycles'] <= w
            assert 0 <= s['util'] <= 1 and 0 <= s['issueUtil'] <= 1
            assert not s['limit']


def test_compare_speedup():
    r = run_program(SAMPLES['Independent ops (high ILP)'], {})
    sp = {x['w']: x['speedup'] for x in r['compare']['widths']}
    assert sp[1] == 1 and sp[2] > 1.4 and sp[4] > sp[2]
    sols = {x['l']: x['r']['cycles'] for x in r['compare']['solutions']}
    assert sols['All solutions'] <= sols['No solutions (stall only)']
