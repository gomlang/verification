# Native ecosystem verification

This standalone GoML repository owns the local ecosystem test runner, atomic private
registry snapshots, race and SIMD checks, and Linux PTY sessions. The
`reference` package supplies test fixture decoding, structural JSON comparisons
and an independent VT screen model. It depends only on the standard library.
Libraries are sibling repositories in `~/git/gomlang/` by default. Ordinary examples live inside each library at `examples/<name>/`, sharing its root manifest and `[dev-dependencies]`. The seven native fixtures in bench, llvm, redis, sql, sqlite, web and yaml retain independent modules under `testdata/downstream/native/`. Set `GOMLANG_LIBRARIES` to select another library directory. Source files use `.goml`. GoML 0.1.57 or newer and Go 1.26+ are required. The recipes default to `../../goml-dev/stage2/bin/goml`; `GOML=/absolute/path/to/goml` selects an installed release for both building and running the verifier.

From this repository root:

```sh
just ecosystem-test
just ecosystem-test color ndarray goml_stats
just ecosystem-test --no-race terminal explorer
just ecosystem-test --goml /path/to/goml lsp
just ecosystem-test --list
GOML=/path/to/goml-0.1.57/bin/goml just ecosystem-test
```

No module arguments selects all 70 libraries registered in `modules()`, including UUID, YAML, JWT, S3, the WebAssembly interpreter and goir, plus `goml_stats` and Explorer. Each selected module and its transitive dependencies must exist. A named selection supports a partial checkout: only the selected repositories and their dependencies are read. Dependencies include `[dependencies]` and `[dev-dependencies]` from root manifests and nested source modules such as native fixtures. Missing repositories fail with the selected module or referring manifest in the error; a default all-module run never silently skips missing repositories. The runner checks formatting, builds named examples or native fixtures, runs library and example/fixture `#[test]` suites, and invokes `goml verify` for an independent registry boundary check. It also verifies cached build fingerprints and executes the existing smoke checks. Native PTY checks cover terminal, tui, prompt,
progress, tui_markdown and Explorer. The ndarray check also compiles SSE2 and
scalar variants, inspects the linked kernel symbols and repeats reference tests.
Unicode tools freshly download checksum-pinned inputs on every conformance run.

Modules with concurrency checks run their suites again using `GOFLAGS=-race`
and separate `_artifact/race/` targets. This includes declared native Go adapter
tests where applicable. Native Go unit tests also run without the race detector,
including the HTML, image, YAML and JWT adapters. UUID generation and S3 client
tests participate in the race pass. `--no-race` explicitly skips this additional pass.
Individual test deadlines are 300 seconds; command deadlines are 600 seconds.
Failures write captured output and return a nonzero status. The final JSON
report records success, selected modules, registry identity, command durations,
exit codes and log paths under `_artifact/verification/`.
`GOML_VERIFY_REPORT` can select a different JSON report path when running disjoint
module batches concurrently.

Reference fixtures retain results from independent implementations, along with
their provenance. Native tests consume those fixed expectations; they do not
require Python, NumPy or Rust at test time. Actual filesystem, process, socket,
TLS, PTY and external LLVM/GNU tool checks continue to execute. See each module's
README for prerequisites.
Reference fixtures are stored as gzip streams in one `.gz` file or sequential
`.gz.0`, `.gz.1`, ... chunks; the test loader requires `gzip` on `PATH`.

To run an individual example manually, first build this runner and obtain its
private registry path:

```sh
../../goml-dev/stage2/bin/goml build
export GOML_HOME="$(_artifact/bin/verification --registry-only color)"
cd ../color
../../goml-dev/stage2/bin/goml run --example basic
../../goml-dev/stage2/bin/goml test
../../goml-dev/stage2/bin/goml verify --timeout 300s
```

The runner supplies `GOML_VERIFY_ROOT`, `GOML_VERIFY_DRIVER` and
`GOML_VERIFY_BINARY` to test subprocesses. Native test helpers use local build
paths when these variables are absent. Shared development dependencies are resolved
from a content-addressed snapshot, whose files are captured once and published
with an atomic directory rename. Concurrent publishers can only observe a
complete index. The snapshot contains the selected ecosystem libraries and their
transitive dependencies; application selections such as Explorer contribute their
dependencies without becoming registry packages. Generated directories, manifest
`[build].target-dir` outputs and symbolic links are excluded from both dependency
discovery and captured sources. `--registry-only` accepts the same module selection
as an ordinary run; omitting it requires the complete ecosystem checkout.

The runner's own regression suite covers publication races, captured source
consistency, partial checkouts, transitive and native fixture dependencies,
missing dependency diagnostics, coordinate validation, binary data, symlink exclusion, command
failure logs, path delimiters, UTF-8 failures and option parsing. The reference
model tests also check fragmented terminal sequences, erased password history
and cleanup when starting a PTY child fails:

```sh
../../goml-dev/stage2/bin/goml test
```

Every ecosystem repository also runs these checks through GitHub Actions. The
shared [CI configuration](ci/README.md) pins the released toolchain and sibling
repository revisions, preserves the triggering candidate, installs native test
dependencies, and retains failure logs. Verification and catalog repositories
run their own infrastructure and consistency checks.

`goml verify` builds and tests copied examples and explicit fixtures as independent modules. Each invocation creates a second isolated snapshot under the library target directory, with workspace resolution disabled and local requirements normalized to snapshot versions. Example test data remains beside the example; the reference helpers locate it from both ordinary module tests and materialized downstream tests. Programs are run explicitly by this runner for smoke, PTY and SIMD checks.
