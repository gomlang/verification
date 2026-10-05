set -euo pipefail

module="${1:?module is required}"
packages=()
for executable in cc make curl gzip tar unzip zip nm pkg-config; do
    if ! command -v "$executable" >/dev/null; then
        packages+=(build-essential curl gzip tar unzip zip binutils pkg-config)
        break
    fi
done
if [[ "$module" == llvm ]]; then
    packages+=(llvm-18-dev clang-18)
fi
if [[ "$module" == sql || "$module" == sqlite || "$module" == postgres ]]; then
    packages+=(libsqlite3-0)
fi
if [[ "$module" == cli ]]; then
    packages+=(zsh fish)
fi
if ((${#packages[@]})); then
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends "${packages[@]}"
fi
if [[ "$module" == llvm ]]; then
    printf '%s\n' /usr/lib/llvm-18/bin >> "$GITHUB_PATH"
fi
