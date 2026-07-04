#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path


def normalize_task_id(task_id: str) -> str:
    return task_id.replace(':', '_')


def read_kv_metadata(meta_path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not meta_path.is_file():
        return data
    for line in meta_path.read_text().splitlines():
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip()
    return data


def write_dockerfile(*, out_path: Path, task_id: str, binary_name: str, bin_path: Path) -> None:
    with out_path.open('wb') as handle:
        handle.write(b'FROM cage/claude-code:cyberdebug\n\n')
        handle.write(f'LABEL cybergym.dynamic_environment.task_id="{task_id}" cybergym.dynamic_environment.binary="{binary_name}"\n\n'.encode('utf-8'))
        handle.write(b'RUN mkdir -p /in\n')
        handle.write(b"RUN cat <<'__CYBERGYM_OFFICIAL_BINARY_B64__' >/tmp/official_vulnerable_binary.b64\n")
        with bin_path.open('rb') as source:
            base64.encode(source, handle)
        handle.write(b'__CYBERGYM_OFFICIAL_BINARY_B64__\n')
        handle.write(b'RUN base64 -d /tmp/official_vulnerable_binary.b64 >/in/official_vulnerable_binary && cp /in/official_vulnerable_binary /in/offcial_vulnerable_binary && chmod +x /in/official_vulnerable_binary /in/offcial_vulnerable_binary && rm -f /tmp/official_vulnerable_binary.b64\n')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', default='/tmp/psy_workspace')
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cases_root = output_root / 'cases'
    cases_root.mkdir(parents=True, exist_ok=True)

    index: dict[str, dict[str, str]] = {}
    for case_dir in sorted(p for p in source_root.iterdir() if p.is_dir() and p.name.isdigit()):
        task_id = f'arvo:{case_dir.name}'
        dataset_key = normalize_task_id(task_id)
        bin_path = (case_dir / 'official_vulnerable_binary').resolve()
        if not bin_path.is_file():
            continue
        case_out = cases_root / dataset_key
        case_out.mkdir(parents=True, exist_ok=True)

        write_dockerfile(
            out_path=case_out / 'Dockerfile',
            task_id=task_id,
            binary_name=bin_path.name,
            bin_path=bin_path,
        )

        metadata = {
            'task_id': task_id,
            'dataset_key': dataset_key,
            'binary_name': bin_path.name,
            'binary_size': str(bin_path.stat().st_size),
            'binary_source_path': str(bin_path),
            'source_case_dir': str(case_dir),
            **read_kv_metadata(case_dir / 'metadata.txt'),
        }
        (case_out / 'metadata.json').write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding='utf-8')
        index[task_id] = {
            'dataset_key': dataset_key,
            'dockerfile': str((case_out / 'Dockerfile').relative_to(output_root)),
            'metadata': str((case_out / 'metadata.json').relative_to(output_root)),
            'binary_name': bin_path.name,
        }

    (output_root / 'index.json').write_text(json.dumps(index, indent=2, sort_keys=True), encoding='utf-8')
    print(f'Wrote {len(index)} cases to {output_root}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
