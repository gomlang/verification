import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib


CI = Path(__file__).resolve().parent


def inventory(file=CI / "repositories.json"):
    records = json.loads(file.read_text())
    if not isinstance(records, dict) or not records:
        raise ValueError("repository inventory must be a nonempty object")
    for name, record in records.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name == "verification":
            raise ValueError(f"invalid repository name: {name}")
        if set(record) != {"revision", "kind"}:
            raise ValueError(f"invalid repository record: {name}")
        if not re.fullmatch(r"[0-9a-f]{40}", record["revision"]):
            raise ValueError(f"repository requires a full commit SHA: {name}")
        if record["kind"] not in {"library", "application", "catalog"}:
            raise ValueError(f"invalid repository kind: {name}")
    return records


def selected_module(value, records):
    if value != "verification" and value not in records:
        raise ValueError(f"unknown ecosystem repository: {value}")
    return value


def run(arguments, cwd=None, env=None, capture=False):
    return subprocess.run(
        [str(value) for value in arguments], cwd=cwd, env=env, check=True,
        text=True, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    ).stdout


def checkout(root, name, revision):
    destination = root / name
    if destination.exists():
        actual = run(["git", "rev-parse", "HEAD"], cwd=destination, capture=True).strip()
        if actual != revision:
            raise ValueError(f"dependency checkout has unexpected revision: {name}")
        return
    destination.mkdir()
    run(["git", "init", "--quiet", destination], capture=True)
    run(["git", "remote", "add", "origin", f"https://github.com/gomlang/{name}.git"],
        cwd=destination, capture=True)
    run(["git", "fetch", "--quiet", "--depth=1", "origin", revision],
        cwd=destination, capture=True)
    run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        cwd=destination, capture=True)
    actual = run(["git", "rev-parse", "HEAD"], cwd=destination, capture=True).strip()
    if actual != revision:
        raise ValueError(f"dependency revision mismatch: {name}")


def prepare(root, module, records):
    if not (root / module / ".git").exists():
        raise ValueError(f"candidate must already be checked out: {module}")
    if not (root / "verification" / "goml.toml").is_file():
        raise ValueError("verification infrastructure is missing")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        jobs = {
            executor.submit(checkout, root, name, record["revision"]): name
            for name, record in records.items() if name != module
        }
        for job in concurrent.futures.as_completed(jobs):
            job.result()
    resolved = {}
    for name in sorted(set(records) | {"verification"}):
        resolved[name] = run(["git", "rev-parse", "HEAD"], cwd=root / name, capture=True).strip()
    output = root / "verification/_artifact/ci"
    output.mkdir(parents=True, exist_ok=True)
    (output / "checkouts.json").write_text(json.dumps({"candidate": module, "revisions": resolved}, indent=2) + "\n")
    print(f"Prepared {len(resolved)} repositories; candidate {module} at {resolved[module]}", flush=True)


def install(prefix, file=CI / "toolchain.json"):
    config = json.loads(file.read_text())
    version, checksum = config["version"], config["sha256"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("invalid toolchain version")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid toolchain checksum")
    if prefix.exists():
        raise ValueError(f"toolchain destination already exists: {prefix}")
    asset = f"goml-{version}-linux-amd64.tar.gz"
    url = f"https://github.com/gomlang/goml/releases/download/v{version}/{asset}"
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / asset
        run(["curl", "--fail", "--location", "--retry", "3", "--output", archive, url])
        if hashlib.sha256(archive.read_bytes()).hexdigest() != checksum:
            raise ValueError("released toolchain checksum mismatch")
        prefix.mkdir(parents=True)
        run(["tar", "-xzf", archive, "--strip-components=1", "-C", prefix])
    run([prefix / "bin/goml", "__toolchain-finalize", "--prefix", prefix])
    run([prefix / "bin/goml", "version"])


def manifests(root):
    for directory, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in {
            ".git", "_artifact", "_bootstrap", "__pycache__", "node_modules",
        })
        if "goml.toml" in files:
            yield Path(directory) / "goml.toml"


def dependency_closure(root, module, records):
    pending, visited = [module], set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        selected_module(name, records)
        visited.add(name)
        for manifest in manifests(root / name):
            data = tomllib.loads(manifest.read_text())
            for section in ("dependencies", "dev-dependencies"):
                for coordinate in data.get(section, {}):
                    if coordinate.startswith("ecosystem::"):
                        dependency = coordinate.removeprefix("ecosystem::")
                        selected_module(dependency, records)
                        pending.append(dependency)
    return sorted(visited)


def native(root, module, records):
    for name in dependency_closure(root, module, records):
        for relative in ("go.mod", "testdata/downstream/native/go.mod"):
            manifest = root / name / relative
            if manifest.is_file():
                print(f"Downloading native dependencies: {name}/{relative}", flush=True)
                run(["go", "mod", "download", "all"], cwd=manifest.parent)


def validate_catalog(root, records, available):
    expected = {name for name, record in records.items() if record["kind"] != "catalog"}
    if available != expected:
        raise ValueError(f"runner/catalog mismatch: {sorted(available ^ expected)}")
    for name, record in records.items():
        directory = root / name
        if not (directory / "README.md").is_file():
            raise ValueError(f"missing README: {name}")
        if record["kind"] != "catalog":
            manifest = tomllib.loads((directory / "goml.toml").read_text())
            if record["kind"] == "library" and manifest["module"]["path"] != f"ecosystem::{name}":
                raise ValueError(f"incorrect module coordinate: {name}")
            dependency_closure(root, name, records)
    for document in ("README.md", "ROADMAP.md", "FINDINGS.md", "split-manifest.tsv", "consumer-split-manifest.tsv"):
        if not (root / "ecosystem" / document).is_file():
            raise ValueError(f"missing ecosystem catalog document: {document}")


def verify(root, module, goml, records):
    verifier = root / "verification"
    output = verifier / "_artifact/ci"
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"GOMLANG_LIBRARIES": str(root)}
    run([goml, "fmt", "--check"], cwd=verifier, env=env)
    run([goml, "build"], cwd=verifier, env=env)
    binary = verifier / "_artifact/bin/verification"
    if module in {"verification", "ecosystem"}:
        run([goml, "test", "--timeout", "300s"], cwd=verifier, env=env)
        listing = run([binary, "--list"], cwd=verifier, env=env, capture=True)
        available = {line for line in listing.splitlines() if re.fullmatch(r"[a-z][a-z0-9_]*", line)}
        validate_catalog(root, records, available)
        (output / "catalog.json").write_text(json.dumps({"success": True, "modules": sorted(available)}, indent=2) + "\n")
    else:
        run([binary, "--goml", goml, module], cwd=verifier, env=env)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "install", "native", "verify"])
    parser.add_argument("--libraries", type=Path)
    parser.add_argument("--module")
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--goml", type=Path)
    args = parser.parse_args()
    if args.operation == "install":
        if args.prefix is None:
            parser.error("install requires --prefix")
        install(args.prefix.resolve())
        return
    records = inventory()
    module = selected_module(args.module, records)
    if args.libraries is None:
        parser.error("operation requires --libraries")
    root = args.libraries.resolve()
    if args.operation == "prepare":
        prepare(root, module, records)
    elif args.operation == "native":
        native(root, module, records)
    else:
        if args.goml is None:
            parser.error("verify requires --goml")
        verify(root, module, args.goml.resolve(), records)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"CI failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stdout or "", file=sys.stderr)
            print(error.stderr or "", file=sys.stderr)
        sys.exit(1)
