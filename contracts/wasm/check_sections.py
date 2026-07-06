import sys

NAMES = {
    0: 'custom', 1: 'type', 2: 'import', 3: 'function', 4: 'table',
    5: 'memory', 6: 'global', 7: 'export', 8: 'start', 9: 'element',
    10: 'code', 11: 'data', 12: 'datacount', 13: 'tag',
}


def read_leb(b, i):
    val = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        val |= (x & 0x7f) << s
        if not (x & 0x80):
            break
        s += 7
    return val, i


def parse(fn):
    b = open(fn, 'rb').read()
    assert b[:4] == b'\x00asm', 'not a wasm'
    i = 8
    secs = []
    while i < len(b):
        sid = b[i]
        i += 1
        sz, i = read_leb(b, i)
        secs.append((NAMES.get(sid, '?%d' % sid), sz))
        i += sz
    return secs


def check_memory_export(fn):
    """Walk to export section, list exported names; also report memory section count."""
    b = open(fn, 'rb').read()
    i = 8
    mem_sections = 0
    mem_imports = 0
    exports = []
    while i < len(b):
        sid = b[i]
        i += 1
        sz, start = read_leb(b, i)
        i = start
        end = i + sz
        if sid == 2:  # import
            j = i
            cnt, j = read_leb(b, j)
            for _ in range(cnt):
                mlen, j = read_leb(b, j)
                mod = b[j:j + mlen].decode('utf-8', 'replace'); j += mlen
                nlen, j = read_leb(b, j)
                j += nlen
                kind = b[j]; j += 1
                if kind == 2:  # memory import
                    mem_imports += 1
                    # skip limits
                    flags = b[j]; j += 1
                    _, j = read_leb(b, j)
                    if flags & 1:
                        _, j = read_leb(b, j)
                elif kind == 0:  # func
                    _, j = read_leb(b, j)
                elif kind == 1:  # table
                    j += 1
                    _, j = read_leb(b, j)
                    if b[j - 1] & 1 if False else False:
                        pass
                elif kind == 3:  # global
                    j += 1
                    j += 1
        elif sid == 5:  # memory
            mem_sections += 1
        elif sid == 7:  # export
            j = i
            cnt, j = read_leb(b, j)
            for _ in range(cnt):
                nlen, j = read_leb(b, j)
                name = b[j:j + nlen].decode('utf-8', 'replace'); j += nlen
                kind = b[j]; j += 1
                idx, j = read_leb(b, j)
                exports.append((name, {0: 'func', 1: 'table', 2: 'mem', 3: 'global'}.get(kind, kind), idx))
        i = end
    return mem_sections, mem_imports, exports


for f in sys.argv[1:]:
    print('=== %s ===' % f)
    for nm, sz in parse(f):
        print('  %-11s %8d' % (nm, sz))
    ms, mi, ex = check_memory_export(f)
    print('  >> memory sections: %d | memory imports: %d' % (ms, mi))
    memex = [e for e in ex if e[1] == 'mem']
    print('  >> exported memory: %s' % (memex if memex else 'NONE'))
