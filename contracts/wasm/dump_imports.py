import sys

NAMES = {0: 'func', 1: 'table', 2: 'mem', 3: 'global'}


def read_leb(b, i):
    val = 0
    s = 0
    while True:
        x = b[i]; i += 1
        val |= (x & 0x7f) << s
        if not (x & 0x80):
            break
        s += 7
    return val, i


def dump(fn):
    b = open(fn, 'rb').read()
    i = 8
    while i < len(b):
        sid = b[i]; i += 1
        sz, i = read_leb(b, i)
        end = i + sz
        if sid == 2:  # import section
            j = i
            cnt, j = read_leb(b, j)
            print('  imports: %d' % cnt)
            for n in range(cnt):
                mlen, j = read_leb(b, j)
                mod = b[j:j + mlen].decode('utf-8', 'replace'); j += mlen
                nlen, j = read_leb(b, j)
                fld = b[j:j + nlen].decode('utf-8', 'replace'); j += nlen
                kind = b[j]; j += 1
                kn = NAMES.get(kind, '?%d' % kind)
                detail = ''
                if kind == 0:  # func
                    ti, j = read_leb(b, j)
                    detail = 'typeidx=%d' % ti
                elif kind == 1:  # table
                    j += 1  # elem type
                    flags = b[j]; j += 1
                    mn, j = read_leb(b, j)
                    detail = 'min=%d' % mn
                    if flags & 1:
                        mx, j = read_leb(b, j)
                        detail += ' max=%d' % mx
                elif kind == 2:  # memory
                    flags = b[j]; j += 1
                    mn, j = read_leb(b, j)
                    detail = 'limits flags=%d min=%d' % (flags, mn)
                    if flags & 1:
                        mx, j = read_leb(b, j)
                        detail += ' max=%d' % mx
                elif kind == 3:  # global
                    j += 2  # valtype + mut
                # Only print memory imports + first few others
                if kind == 2:
                    print('   [%d] MEMORY  "%s"."%s"  %s' % (n, mod, fld, detail))
                elif n < 3:
                    print('   [%d] %-7s "%s"."%s"  %s' % (n, kn, mod, fld, detail))
        i = end


for f in sys.argv[1:]:
    print('=== %s ===' % f)
    dump(f)
