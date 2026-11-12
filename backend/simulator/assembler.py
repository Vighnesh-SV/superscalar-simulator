"""Assembler and functional executor.

parse()   turns assembly text into a list of instruction dicts.
execute() runs the program once to get the real dynamic instruction stream
          (which way every branch actually went). The timing simulator then
          replays this trace cycle by cycle.
"""
import re

# opcode -> (functional unit type, operand format)
OPS = {
    'ADD': ('ALU', 'R'), 'SUB': ('ALU', 'R'), 'AND': ('ALU', 'R'), 'OR': ('ALU', 'R'),
    'XOR': ('ALU', 'R'), 'SLT': ('ALU', 'R'),
    'ADDI': ('ALU', 'I'), 'SUBI': ('ALU', 'I'), 'ANDI': ('ALU', 'I'), 'ORI': ('ALU', 'I'),
    'MUL': ('MUL', 'R'), 'DIV': ('MUL', 'R'),
    'LW': ('MEM', 'L'), 'SW': ('MEM', 'S'),
    'BEQ': ('BR', 'B'), 'BNE': ('BR', 'B'), 'BLT': ('BR', 'B'), 'J': ('BR', 'J'),
    'NOP': ('ALU', 'N'), 'HALT': ('BR', 'H'),
}
NUM_REGS = 16
MEM_WORDS = 64
TRACE_LIMIT = 400


class AsmError(Exception):
    pass


def _reg(a):
    m = re.fullmatch(r'[rR](\d+)', a)
    if not m or int(m.group(1)) >= NUM_REGS:
        raise AsmError(f'"{a}" is not a register (use R0 to R15)')
    return int(m.group(1))


def _num(a):
    if not re.fullmatch(r'-?\d+', a):
        raise AsmError(f'"{a}" is not a number')
    return int(a)


def parse(src):
    """Return {'prog': [...], 'errs': [...]}."""
    prog, labels, errs, pend = [], {}, [], []
    for ln, raw in enumerate(src.split('\n')):
        s = re.sub(r'[;#].*$', '', raw).strip()
        if not s:
            continue
        while True:
            m = re.match(r'^([A-Za-z_]\w*)\s*:\s*', s)
            if not m:
                break
            labels[m.group(1).lower()] = len(prog)
            s = s[m.end():]
        if not s:
            continue
        sp = re.match(r'^(\w+)\s*(.*)$', s)
        if not sp:
            errs.append(f'Line {ln + 1}: cannot read "{s}"')
            continue
        op = sp.group(1).upper()
        d = OPS.get(op)
        if not d:
            errs.append(f'Line {ln + 1}: unknown instruction "{sp.group(1)}"')
            continue
        args = [a.strip() for a in sp.group(2).split(',')] if sp.group(2) else []
        args = [a for a in args if a]
        ins = {'op': op, 'type': d[0], 'kind': d[1], 'dest': 0, 'srcs': [], 'imm': 0, 'line': ln + 1}

        def need(n):
            if len(args) != n:
                raise AsmError(f'{op} needs {n} operand{"" if n == 1 else "s"}, found {len(args)}')
        try:
            k = d[1]
            if k == 'R':
                need(3); ins['dest'] = _reg(args[0]); ins['srcs'] = [_reg(args[1]), _reg(args[2])]
            elif k == 'I':
                need(3); ins['dest'] = _reg(args[0]); ins['srcs'] = [_reg(args[1])]; ins['imm'] = _num(args[2])
            elif k in ('L', 'S'):
                need(2)
                mm = re.fullmatch(r'(-?\d+)\s*\(\s*([rR]\d+)\s*\)', args[1])
                if not mm:
                    raise AsmError(f'write it as {op} Rx, offset(Ry)')
                ins['imm'] = int(mm.group(1)); b = _reg(mm.group(2))
                if k == 'L':
                    ins['dest'] = _reg(args[0]); ins['srcs'] = [b]
                else:
                    ins['srcs'] = [_reg(args[0]), b]
            elif k == 'B':
                need(3); ins['srcs'] = [_reg(args[0]), _reg(args[1])]; ins['label'] = args[2]; pend.append(ins)
            elif k == 'J':
                need(1); ins['label'] = args[0]; pend.append(ins)
            else:
                need(0)
        except AsmError as e:
            errs.append(f'Line {ln + 1}: {e}')
            continue
        ins['text'] = op + (' ' + ', '.join(args) if args else '')
        prog.append(ins)
    for ins in pend:
        t = labels.get(ins['label'].lower())
        if t is None:
            errs.append(f'Line {ins["line"]}: label "{ins["label"]}" is not defined')
        else:
            ins['target'] = t
    if not prog and not errs:
        errs.append('The program is empty. Type some instructions or pick a sample.')
    for i, ins in enumerate(prog):
        ins['pc'] = i
    return {'prog': prog, 'errs': errs}


def i32(v):
    """Wrap to a signed 32-bit integer, like JavaScript's `x | 0`."""
    return ((int(v) + 0x80000000) & 0xFFFFFFFF) - 0x80000000


def execute(prog):
    """Run the program functionally. Returns {'trace': [...], 'trunc': bool}."""
    R = [0] * NUM_REGS
    M = [i + 1 for i in range(MEM_WORDS)]
    tr, pc, trunc = [], 0, False
    while 0 <= pc < len(prog):
        if len(tr) >= TRACE_LIMIT:
            trunc = True
            break
        ins = prog[pc]
        srcs = ins['srcs']
        a = R[srcs[0]] if len(srcs) > 0 else 0
        b = R[srcs[1]] if len(srcs) > 1 else 0
        imm, op = ins['imm'], ins['op']
        res, taken = None, False
        if op == 'ADD': res = a + b
        elif op == 'SUB': res = a - b
        elif op == 'AND': res = a & b
        elif op == 'OR': res = a | b
        elif op == 'XOR': res = a ^ b
        elif op == 'SLT': res = 1 if a < b else 0
        elif op == 'ADDI': res = a + imm
        elif op == 'SUBI': res = a - imm
        elif op == 'ANDI': res = a & imm
        elif op == 'ORI': res = a | imm
        elif op == 'MUL': res = a * b
        elif op == 'DIV': res = 0 if b == 0 else int(a / b)
        elif op == 'LW': res = M[(a + imm) % MEM_WORDS]
        elif op == 'SW': M[(b + imm) % MEM_WORDS] = a
        elif op == 'BEQ': taken = a == b
        elif op == 'BNE': taken = a != b
        elif op == 'BLT': taken = a < b
        elif op == 'J': taken = True
        if ins['dest'] and res is not None:
            R[ins['dest']] = i32(res)
        tr.append({'pc': pc, 'taken': taken, 'result': i32(res) if res is not None else 0})
        if op == 'HALT':
            break
        pc = ins['target'] if taken else pc + 1
    return {'trace': tr, 'trunc': trunc}
