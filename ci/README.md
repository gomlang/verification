# Ecosystem CI

Every ecosystem repository has a `CI` workflow for pushes to `main`, pull requests,
and manual dispatch. Library and application workflows call the shared
`ecosystem-ci.yml` at a full commit SHA. The matching `verification-ref` checks
out the runner and scripts from that same revision. Verification's own workflow
uses the candidate infrastructure, including on pull requests.

The job checks out the triggering commit or pull-request merge commit as the
candidate. It preserves that checkout and fetches sibling repositories at the
commits in `repositories.json`. No developer checkout, registry, credential or
local GoML installation is required. `checkouts.json` records every tested commit.
Existing dependency checkouts must be repository roots at the pinned revision
with no staged, modified or untracked files; ignored build artifacts are preserved.
Invalid checkouts fail without resetting or deleting local changes. The triggering
candidate remains exempt from dependency reuse checks.

`toolchain.json` pins the published GoML Linux amd64 archive and its SHA-256.
Installation verifies the archive before extraction and finalizes the toolchain
with its own binaries. The runner uses Go 1.26 on Ubuntu 24.04. Native dependencies
are downloaded explicitly before readonly GoML compilation, including manifests
in transitive dependencies, named examples and downstream fixture modules.
Discovery skips generated directories and symbolic links; failures stop the job.
Before native downloads start, every selected repository and transitive dependency
must have a real source directory and root manifest (catalogs need no manifest).
Missing dependencies identify the referring manifest, and source traversal errors
fail the job instead of silently omitting nested native modules.
This includes the HTML parsing/sanitization, image codec, YAML and JWT adapters.
LLVM, SQLite and shell
completion prerequisites are installed for their corresponding jobs.
PostgreSQL jobs alone start a PostgreSQL 16 service, wait for its health check,
and pass the mapped port through `GOML_POSTGRES_TEST_DSN`. Verification rejects a
missing DSN before running commands. PostgreSQL adapter tests and downstream
fixtures use that real server; the Protocol Buffers Go oracle and OAuth 2.0
HTTP/TLS fixtures are also included in normal and race checks.

Library jobs run the existing format, test, independent downstream verification,
cached-build and smoke checks. Existing race, PTY, SIMD, protocol and reference
checks remain enabled; SQL pool tests also run under the race detector.
The Wasm interpreter replays its frozen official conformance fixtures in its
conformance example tests, through both `goml test` and `goml verify`. The ordinary
checks use GoML and retained fixtures; WABT is needed only when regenerating them.
The verification and ecosystem catalog jobs run infrastructure regression tests,
the verifier's own GoML tests, and module/dependency/catalog consistency checks.
Workflow syntax is checked with actionlint. Logs and reports are retained for
14 days even when verification fails.

For a new repository, publish its tested source commit before adding that commit
to `repositories.json`: preparation fetches every inventory entry. Publish the
matching verification changes next, then create the new repository's caller
workflow with that published verification SHA in both reference fields. This
keeps the initial library and infrastructure workflows free of unpublished
dependency revisions. Existing toolchain pins do not change when adding libraries.
For goir, the artifact also includes `_artifact/codegen.tsv` with backend
comparisons and median compilation timings, and `_artifact/encoded-code.tsv`
with linked ABI0 symbol sizes measured by `go tool nm`. The typed corpus writes
`_artifact/typed-phases.tsv` with repeated per-stage compilation timings and
retained context capacity; `_artifact/runtime-boundary.txt` records the Go
version, pointer-leaf GC probe and expected ABIInternal selector rejection.
`_artifact/global-allocation.tsv` compares both allocators with 21-sample
P50/P95 and weighted spill statistics. `_artifact/pipeline-bench.tsv` covers
the shared analysis stage and cache statistics; `_artifact/seeded-fuzz.tsv`
records the deterministic module corpus and `_artifact/scalar-native.txt`
records checked arithmetic and native trap validation.
Failed native fixtures retain generated sources, exact cases and any reduced
reproducer in the same CI artifact.

Only `contents: read` is granted, checkout credentials are not persisted, and no
secrets or write tokens are passed to tests. Pull requests run through the normal
`pull_request` event. Jobs have a 40-minute deadline and superseded runs for the
same branch or pull request are cancelled.

## Updating the shared configuration

Update the relevant full commit SHA in `repositories.json` when adopting a new
dependency baseline. A repository's own CI always tests its triggering candidate,
even if its inventory entry still names an earlier baseline. Changes to the
released toolchain require the official archive checksum in `toolchain.json`.

Run the infrastructure checks from this repository:

```sh
python3 -m unittest discover -s ci -p 'test_*.py'
actionlint -shellcheck= .github/workflows/*.yml
python3 ci/ecosystem.py verify --libraries .. --module verification --goml /path/to/goml
```

After committing and publishing a validated infrastructure revision, update the
small caller workflows in sibling checkouts:

```sh
python3 ci/workflows.py --libraries .. --revision FULL_VERIFICATION_COMMIT_SHA
python3 ci/workflows.py --libraries .. --revision FULL_VERIFICATION_COMMIT_SHA --check
```

Commit and push those workflow updates in each repository. Dependencies remain
fixed until the shared inventory is deliberately updated. This CI checks isolated
registry snapshots built from real GitHub checkouts; it does not publish immutable
registry versions or claim that the private snapshot is a public registry.
