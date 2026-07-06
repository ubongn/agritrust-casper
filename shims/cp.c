/* cp.exe — minimal single-file `cp SRC DST` for cargo-odra on Windows.
 * Binary-safe file copy. Exit 0 on success. */
#include <stdio.h>
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    FILE *in = fopen(argv[1], "rb");
    if (!in) return 1;
    FILE *out = fopen(argv[2], "wb");
    if (!out) { fclose(in); return 1; }
    char buf[65536]; size_t n;
    while ((n = fread(buf, 1, sizeof buf, in)) > 0) {
        if (fwrite(buf, 1, n, out) != n) { fclose(in); fclose(out); return 1; }
    }
    fclose(in);
    fclose(out);
    return 0;
}
