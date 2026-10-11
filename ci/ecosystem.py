import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib


CI = Path(__file__).resolve().parent


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def inventory(file=CI / "repositories.json"):
    records = json.loads(file.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(records, dict) or not records:
        raise ValueError("repository inventory must be a nonempty object")
    for name, record in records.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name == "verification":
            raise ValueError(f"invalid repository name: {name}")
        if not isinstance(record, dict) or set(record) != {"revision", "kind"}:
            raise ValueError(f"invalid repository record: {name}")
        if not isinstance(record["revision"], str) or not re.fullmatch(r"[0-9a-f]{40}", record["revision"]):
            raise ValueError(f"repository requires a full commit SHA: {name}")
        if record["kind"] not in ("library", "application", "catalog"):
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
    if destination.is_symlink():
        raise ValueError(f"dependency checkout is a symbolic link: {name}")
    if destination.exists():
        actual = run(["git", "rev-parse", "HEAD"], cwd=destination, capture=True).strip()
        if actual != revision:
            raise ValueError(f"dependency checkout has unexpected revision: {name}")
        top = run(["git", "rev-parse", "--show-toplevel"], cwd=destination, capture=True).strip()
        if Path(top).resolve() != destination.resolve():
            raise ValueError(f"dependency checkout is not a repository root: {name}")
        changes = run(["git", "status", "--porcelain=v1", "--untracked-files=all"],
                      cwd=destination, capture=True)
        if changes:
            raise ValueError(f"dependency checkout has local changes: {name}")
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


def install_release(prefix, file=CI / "toolchain.json"):
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


def install(prefix, file=CI / "toolchain.json"):
    prefix = prefix.resolve()
    config = json.loads(file.read_text(), object_pairs_hook=unique_object)
    revision = config.get("source_revision")
    if revision is None:
        install_release(prefix, file)
        return
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("toolchain source requires a full commit SHA")
    if prefix.exists():
        raise ValueError(f"toolchain destination already exists: {prefix}")
    with tempfile.TemporaryDirectory(prefix="goml-bootstrap-") as temporary:
        root = Path(temporary)
        checkout(root, "goml", revision)
        source = root / "goml"
        install_release(source / "stage0", file)
        # The pinned source's `just make` stages, without requiring just on CI.
        run(["bash", "tools/goml-go-meta/build.sh", prefix], cwd=source)
        run(["bash", "bootstrap/build-stage.sh", "stage2", "stage0/bin/goml",
             "stage0/bin/gomlc"], cwd=source)
        for name in ("gomlc", "gomlfmt", "gomldoc", "gomllsp"):
            shutil.copy2(source / f"gomlc/_bootstrap/stage2/bin/cmd/{name}/{name}",
                         prefix / "bin" / name)
        shutil.copy2(source / "goml/_bootstrap/stage2/bin/cmd/goml/goml",
                     prefix / "bin/goml")
        run(["bash", "tools/lib/install.sh", prefix], cwd=source)
        run(["bash", "tools/lib/finalize-toolchain.sh", prefix, prefix / "bin/goml",
             prefix / "bin/gomlc"], cwd=source)
    (prefix / "source-revision").write_text(revision + "\n")
    run([prefix / "bin/goml", "version"])
    print(f"Built GoML source revision {revision}", flush=True)


def read_toml(path):
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML in {path}: {error}") from error


def source_manifests(root, filename):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"missing source directory or symbolic link: {root}")

    def walk_error(error):
        raise error

    outputs = set()
    for directory, directories, files in os.walk(root, onerror=walk_error):
        directory = Path(directory)
        project = directory / "goml.toml"
        if "goml.toml" in files and not project.is_symlink():
            build = read_toml(project).get("build", {})
            if not isinstance(build, dict):
                raise ValueError(f"build must be a table: {project}")
            target = build.get("target-dir", "_artifact")
            if (not isinstance(target, str) or not target or "\\" in target
                    or Path(target).is_absolute() or ".." in Path(target).parts
                    or Path(target) == Path(".")):
                raise ValueError(f"invalid build target directory: {project}")
            outputs.add(directory / target)
        directories[:] = sorted(name for name in directories if name not in {
            ".git", ".goml", ".cache", "_artifact", "_bootstrap", "__pycache__", "node_modules", "vendor",
        } and not (directory / name).is_symlink() and directory / name not in outputs)
        manifest = directory / filename
        if filename in files and not manifest.is_symlink():
            yield manifest


def manifests(root):
    return source_manifests(root, "goml.toml")


def dependency_closure(root, module, records):
    pending, visited = [(module, f"selected module {module}")], set()
    while pending:
        name, reason = pending.pop()
        if name in visited:
            continue
        try:
            selected_module(name, records)
        except ValueError as error:
            raise ValueError(f"{reason}: {error}") from error
        directory = root / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"{reason}: missing source directory or symbolic link: {directory}")
        if records.get(name, {}).get("kind") != "catalog":
            manifest = directory / "goml.toml"
            if manifest.is_symlink() or not manifest.is_file():
                raise ValueError(f"{reason}: missing source manifest or symbolic link: {manifest}")
        visited.add(name)
        for manifest in manifests(directory):
            data = read_toml(manifest)
            for section in ("dependencies", "dev-dependencies"):
                dependencies = data.get(section, {})
                if not isinstance(dependencies, dict):
                    raise ValueError(f"{section} must be a table: {manifest}")
                for coordinate in dependencies:
                    if coordinate.startswith("ecosystem::"):
                        dependency = coordinate.removeprefix("ecosystem::")
                        kind = records.get(dependency, {}).get("kind")
                        if kind in {"application", "catalog"}:
                            raise ValueError(f"{kind} cannot satisfy library dependency {coordinate} required by {manifest}")
                        pending.append((dependency, f"dependency {dependency} required by {manifest}"))
    return sorted(visited)


def native(root, module, records):
    for name in dependency_closure(root, module, records):
        directory = root / name
        for manifest in source_manifests(directory, "go.mod"):
            print(f"Downloading native dependencies: {name}/{manifest.relative_to(directory)}", flush=True)
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
    env = os.environ | {"GOMLANG_LIBRARIES": str(root)}
    if module == "postgres" and not env.get("GOML_POSTGRES_TEST_DSN", "").strip():
        raise ValueError("postgres verification requires GOML_POSTGRES_TEST_DSN for a live PostgreSQL 16 server")
    verifier = root / "verification"
    output = verifier / "_artifact/ci"
    output.mkdir(parents=True, exist_ok=True)
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
