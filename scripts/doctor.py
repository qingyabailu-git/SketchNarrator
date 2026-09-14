#!/usr/bin/env python3
"""Installation preflight without installing dependencies or contacting services."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def probe(command: list[str]) -> dict:
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=90)
        if result.returncode == 0:
            return {'status': 'available', 'result': json.loads(result.stdout)}
        return {'status': 'needs-preparation', 'reason': '本地探测未通过；未安装或修复任何依赖'}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'status': 'unknown', 'reason': '无法完成本地探测'}


def write_access(path: Path) -> dict:
    target = path.resolve()
    ancestor = target
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return {'path': str(target), 'status': 'likely-writable' if os.access(ancestor, os.W_OK) else 'blocked',
            'evidence': '权限预检，未创建文件；真实写入权限仍以安装结果为准'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['doctor'])
    parser.add_argument('--project', type=Path)
    parser.add_argument('--host', choices=['codex', 'antigravity', 'workbuddy', 'doubao', 'other'], default='other')
    parser.add_argument('--capability', action='append', default=[],
                        choices=['files', 'commands', 'image-generation', 'image-inspection', 'localhost-browser'])
    args = parser.parse_args(argv)
    runtime = ROOT / 'renderer' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    py = str(runtime) if runtime.is_file() else sys.executable
    identity = probe([py, '-B', '-c', 'import json,sys; print(json.dumps({"path":sys.executable,"version":list(sys.version_info[:3])}))'])
    if identity['status'] == 'available':
        version = tuple(identity['result']['version'][:2])
        identity['meets_python_minimum'] = version >= (3, 10)
        if not identity['meets_python_minimum']:
            identity['status'] = 'needs-preparation'
    deps = probe([py, '-B', '-c', 'import av,numpy,cv2,PIL,edge_tts,json; print(json.dumps({"imports":"ok"}))'])
    ffmpeg = probe([py, '-B', str(ROOT/'renderer/scripts/ffmpeg_runtime.py'), '--json'])
    if ffmpeg['status'] == 'available':
        info = ffmpeg['result']
        caps = info['capabilities']
        ffmpeg['status'] = 'available' if info['available'] and caps['libx264'] and caps['concat_demuxer'] else 'needs-preparation'
        ffmpeg['evidence'] = '可执行文件与能力列表检测；尚未实际编码'
    font = probe([py, '-B', '-c', 'import sys,json; sys.path.insert(0,sys.argv[1]); from font_runtime import find_cjk_font; print(json.dumps(str(find_cjk_font())))', str(ROOT/'renderer/scripts')])
    # Validate optional requirements before any costly production work.
    optional_media = {"status": "not-required", "bookends_enabled": False}
    try:
        project_file = args.project / 'project.json' if args.project else None
        if project_file is not None and project_file.is_file():
            project = json.loads(project_file.read_text(encoding='utf-8'))
        else:
            from local_defaults import apply_local_defaults
            project = apply_local_defaults({}, ROOT)
        enabled = bool((project.get('bookends') or {}).get('enabled'))
        available = bool((ffmpeg.get('result', {}).get('ffprobe') or {}).get('available'))
        pyav_ready = probe([py, '-B', '-c', 'import av,json; print(json.dumps({"available":True}))'])['status'] == 'available' if enabled and not available else False
        optional_media = {'status': ('available' if available or pyav_ready else 'needs-preparation') if enabled else 'not-required',
                          'bookends_enabled': enabled, 'ffprobe_available': available,
                          'backend': 'ffprobe' if available else ('pyav' if pyav_ready else None),
                          'next_action': 'none' if available or pyav_ready or not enabled else 'scripts/run.cmd setup（macOS/Linux: sh scripts/run.sh setup）',
                          'reason': '外部片段使用 FFprobe 或现有 PyAV 读取元数据；这里只检查后端可用性，不代表具体媒体已经验证'}
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
        optional_media = {'status': 'needs-preparation', 'reason': '无法解析启用功能，请检查项目或外部配置'}
    capabilities = {name: {'status': 'host-reported' if name in args.capability else 'unknown'}
                    for name in ['files', 'commands', 'image-generation', 'image-inspection', 'localhost-browser']}
    report = {'version': 1, 'host': args.host, 'host_capabilities': capabilities,
              'bootstrap_python': sys.executable, 'runtime_python': identity,
              'dependencies': deps, 'font': font, 'ffmpeg': ffmpeg, 'optional_media': optional_media,
              'runtime_directory': write_access(ROOT/'renderer/.venv'),
              'project_directory': write_access(args.project) if args.project else {'status': 'unknown'},
              'network': 'not-probed', 'installation': 'not-performed', 'end_to_end': 'not-verified'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    checks = [identity, deps, font, ffmpeg, optional_media, report['runtime_directory'], report['project_directory']]
    return 2 if any(c['status'] in {'needs-preparation', 'blocked'} for c in checks) else 0


if __name__ == '__main__':
    raise SystemExit(main())
