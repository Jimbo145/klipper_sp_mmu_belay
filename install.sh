#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
klipper_dir="${KLIPPER_PATH:-${HOME}/klipper}"
if [[ -d "${klipper_dir}/klippy/plugins" ]]; then
    extras_dir="${klipper_dir}/klippy/plugins"
else
    extras_dir="${klipper_dir}/klippy/extras"
fi
if [[ ! -d "${extras_dir}" ]]; then
    printf 'Klipper extras directory not found: %s\n' "${extras_dir}" >&2
    exit 1
fi
target="${extras_dir}/sp_mmu_belay.py"
source_file="${project_dir}/sp_mmu_belay.py"
if [[ -e "${target}" || -L "${target}" ]]; then
    if [[ -L "${target}" && "$(readlink -f -- "${target}")" == "${source_file}" ]]; then
        printf 'Already installed: %s\n' "${target}"
        exit 0
    fi
    printf 'Refusing to overwrite existing file: %s\n' "${target}" >&2
    exit 1
fi
ln -s -- "${source_file}" "${target}"
printf 'Installed: %s -> %s\n' "${target}" "${source_file}"
printf 'Klipper was not restarted. Review README.md before enabling.\n'