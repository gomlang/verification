# Archived: GoML verification

This repository is retained for historical consumers and pinned CI references.
Active development has moved to:

- [gomlang/workflows](https://github.com/gomlang/workflows): shared GitHub Actions, toolchain bootstrap, and ecosystem verification runner.
- [gomlang/testkit](https://github.com/gomlang/testkit): reusable test fixtures, JSON assertions, subprocess checks, and terminal reference models.

New libraries should use `ecosystem::testkit` as a development dependency and
call the shared workflow in `gomlang/workflows`. The implementation below is
frozen for compatibility; use the new repositories for current instructions.

---

# Native ecosystem verification

This standalone GoML repository owns the local ecosystem test runner, atomic private
registry snapshots, race and SIMD checks, and Linux PTY sessions. The
`reference` package supplies test fixture decoding, structural JSON comparisons
and an independent VT screen model. It depends only on the standard library.
Libraries are sibling repositories in `~/git/gomlang/` by default. Ordinary examples live inside each library at `examples/<name>/`, sharing its root manifest and `[dev-dependencies]`. The eight native fixtures in bench, llvm, postgres, redis, sql, sqlite, web and yaml retain independent modules under `testdata/downstream/native/`. Set `GOMLANG_LIBRARIES` to select another library directory. Source files use `.goml`. Go 1.26+ and a GoML driver supporting the unversioned registry index are required. The recipes default to `../../goml/stage2/bin/goml`; `GOML=/absolute/path/to/goml` selects a compatible toolchain for both building and running the verifier.

`GOMLANG_LIBRARIES` and `--goml` accept relative or absolute paths with filesystem
resolution of symbolic links and `..`. Missing or non-directory components in
the library path are rejected even when followed by `..`.
The runner finds its repository by the `ecosystem::verification` module declared
in `goml.toml`, walking from the working directory through its parents. The
checkout directory can have any name, and nested modules with other coordinates
are skipped. Unreadable or invalid manifests report their path.

From this repository root:

```sh
just ecosystem-test
just ecosystem-test color ndarray goml_stats
just ecosystem-test --no-race terminal explorer
just ecosystem-test --goml /path/to/goml lsp
just ecosystem-test --list
GOML=/path/to/compatible/goml just ecosystem-test
```

No module arguments selects all 74 libraries registered in `modules()`, including PostgreSQL, Protocol Buffers, OAuth 2.0 and cron, plus `goml_stats` and Explorer. Each selected module and its transitive dependencies must exist. A named selection supports a partial checkout: only the selected repositories and their dependencies are read. Dependencies include `[dependencies]` and `[dev-dependencies]` from root manifests and nested source modules such as native fixtures. Missing repositories fail with the selected module or referring manifest in the error; a default all-module run never silently skips missing repositories. The runner checks formatting, builds named examples or native fixtures, runs library and example/fixture `#[test]` suites using the same unversioned registry snapshot. It also verifies cached build fingerprints and executes the existing smoke checks. Native PTY checks cover terminal, tui, prompt,
progress, tui_markdown and Explorer. The ndarray check also compiles SSE2 and
scalar variants, inspects the linked kernel symbols and repeats reference tests.
Unicode tools freshly download checksum-pinned inputs on every conformance run.

Modules with concurrency checks run their suites again using `GOFLAGS=-race`
and separate `_artifact/race/` targets. This includes declared native Go adapter
tests where applicable. Native Go unit tests also run without the race detector,
including the HTML, image, YAML and JWT adapters. UUID generation and S3 client
tests participate in the race pass. `--no-race` explicitly skips this additional pass.

The local runner does not fetch native Go dependencies. Before verifying a module
with native adapters, run from this repository:

```sh
python3 ci/ecosystem.py native --libraries .. --module sqlite
```

Replace `sqlite` with the selected module. The command downloads Go dependencies
for the selected module and its transitive dependencies, including examples and
native fixtures. CI runs this preparation as a separate step before verification.

PostgreSQL verification needs a running PostgreSQL 16 server and
`GOML_POSTGRES_TEST_DSN`, shared by the library, native adapter and downstream
fixture tests. The CI job provides a dedicated service with a random host port.
Protocol Buffers also runs its independent Go interoperability tests in
`tools/oracle/`; OAuth 2.0 runs its local HTTP/TLS fixture tests in `testserver/`.
Both native test suites run normally and with the race detector. Native test
helpers can use `GOML_VERIFY_DRIVER` and `GOML_HOME` to build consumers with the
same selected compiler and immutable registry as the runner.
Individual test deadlines are 300 seconds; command deadlines are 600 seconds.
Failures write captured output and return a nonzero status. The final JSON
report records success, selected modules, registry identity, command durations,
exit codes and log paths under `_artifact/verification/`. Its `error` field retains
the failure reason, including failures between commands; it is `null` on success.
The report's `race` field records the requested configuration. Completed race
build/test commands and their exit codes establish which race checks ran.
Each command records `timed_out` and `timeout_ms`. The Linux runner uses a fixed
POSIX shell wrapper with separate arguments to redirect stdout and stderr into
`stdout_log` and `stderr_log` files as the command runs. These raw streams survive
command timeouts; the combined `log` also includes process timeout/launch errors.
Each invocation retains its logs and original `report.json` in a unique, visible
`runs/run-.../` directory, also recorded as `run_directory` in the report. Later
runs cannot overwrite logs referenced by earlier reports. The default
`_artifact/verification/report.json` remains a copy of the latest report;
`GOML_VERIFY_REPORT` selects a different copy path for independent module batches.
Before the first verification command, both report locations receive the current
run's initial record with `completed: false` and `success: false`. Finalization
sets `completed: true` for either success or a handled failure. Interrupted runs
retain their initial metadata and raw stream logs instead of the previous run's
success report; command records are finalized when verification returns. Each
report file is replaced atomically so readers see complete JSON.
Report parent directories follow filesystem resolution of symbolic links and
`..`; temporary files are placed beside the actual destination, including when
the selected report directory is on another filesystem.
After the repository root is found, argument, library-directory and registry
setup failures also replace the latest report with a completed failure. Its
`phase` is `options`, `libraries` or `registry`, with the original error and
`requested_arguments`; no commands or registry home are claimed. An options
failure leaves `race` null because configuration parsing did not finish. Normal
verification reports use `phase: "verification"`. Setup errors keep their nonzero
exit status and original stderr diagnostic, even if writing the report also
fails. Verification failures likewise keep the original diagnostic and log path
when final report publication fails, appending the report-write error.
Successful `--registry-only` output remains a single registry path.

Reference fixtures retain results from independent implementations, along with
their provenance. Native tests consume those fixed expectations; they do not
require Python, NumPy or Rust at test time. Actual filesystem, process, socket,
TLS, PTY and external LLVM/GNU tool checks continue to execute. See each module's
README for prerequisites.
Reference fixtures are stored as gzip streams in one `.gz` file or sequential
`.gz.0`, `.gz.1`, ... chunks; the test loader requires `gzip` on `PATH`.

For ordinary package development, the default central index is [gomlang/registry](https://github.com/gomlang/registry). Declare dependencies with `true`, run `goml update`, then use `goml test` or `goml run --example <name>`. The private snapshots below are for testing selected working checkouts without changing the user's registry cache. They require a GoML driver supporting the unversioned index format; use the source revision pinned in `ci/toolchain.json` until a compatible release is published.

To run an individual example manually, first build this runner and obtain its
private registry path:

```sh
../../goml/stage2/bin/goml build
export GOML_HOME="$(_artifact/bin/verification --registry-only color)"
cd ../color
../../goml/stage2/bin/goml run --example basic
../../goml/stage2/bin/goml test
```

The runner supplies `GOML_VERIFY_ROOT`, `GOML_VERIFY_DRIVER` and
`GOML_VERIFY_BINARY` to test subprocesses. Native test helpers use local build
paths when these variables are absent. Shared development dependencies are resolved
from a content-addressed snapshot, whose files are captured once and published
with an atomic directory rename. Concurrent publishers can only observe a
complete index. Entries use `path = "ecosystem/<module>"`, and sources live directly under `cache/registry/ecosystem/<module>/`; no package versions or version directories are generated. Before reusing a snapshot, the runner checks its index and captured
sources against the requested content. Changed files or source links fail with the
snapshot path; existing contents are preserved. Generated build caches remain
excluded from this comparison. The snapshot contains the selected ecosystem libraries and their
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
../../goml/stage2/bin/goml test
```

Every ecosystem repository also runs these checks through GitHub Actions. The
shared [CI configuration](ci/README.md) pins the source-built toolchain and sibling
repository revisions, preserves the triggering candidate, installs native test
dependencies, and retains failure logs. Verification and catalog repositories
run their own infrastructure and consistency checks.

Ordinary `goml test` builds and tests named examples. Native downstream fixtures are separate modules; the runner explicitly builds and tests them in their own directories against its snapshot. It disables workspace discovery for child commands. Programs are run explicitly by this runner for smoke, PTY and SIMD checks.
