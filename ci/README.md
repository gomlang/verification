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

`toolchain.json` pins the published GoML Linux amd64 archive and its SHA-256.
Installation verifies the archive before extraction and finalizes the toolchain
with its own binaries. The runner uses Go 1.26 on Ubuntu 24.04. Native dependencies
are downloaded explicitly before readonly GoML compilation, including manifests
in dependency and downstream fixture repositories. LLVM, SQLite and shell
completion prerequisites are installed for their corresponding jobs.

Library jobs run the existing format, test, independent downstream verification,
cached-build and smoke checks. Existing race, PTY, SIMD, protocol and reference
checks remain enabled; SQL pool tests also run under the race detector.
The verification and ecosystem catalog jobs run infrastructure regression tests,
the verifier's own GoML tests, and module/dependency/catalog consistency checks.
Workflow syntax is checked with actionlint. Logs and reports are retained for
14 days even when verification fails.

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
