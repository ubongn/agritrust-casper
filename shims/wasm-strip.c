/* wasm-strip.exe — no-op shim for cargo-odra on Windows.
 * cargo-odra runs wasm-opt (which lowers/fixes bulk-memory ops) BEFORE
 * wasm-strip, so by the time we get here the wasm is already clean. We do
 * nothing and return success; Casper accepts the wasm as-is. (A real strip
 * would only drop the name/debug sections to shave gas — not required.) */
int main(int argc, char **argv) { (void)argc; (void)argv; return 0; }
