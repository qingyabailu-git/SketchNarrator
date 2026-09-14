#!/usr/bin/env python3
"""Build panel assets and style registry metadata deterministically from references/style-registry.json."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def build_style_registry_asset(
    registry_path: Path,
    output_js_path: Path,
) -> dict[str, str]:
    """Compile style-registry.json into a browser-readable JavaScript asset with SHA256 metadata."""
    if not registry_path.is_file():
        raise FileNotFoundError(f"Style registry not found: {registry_path}")

    raw_bytes = registry_path.read_bytes()
    sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
    registry_data = json.loads(raw_bytes.decode("utf-8"))

    js_content = f"""// Deterministically generated from references/style-registry.json
// Source SHA256: {sha256_hash}
(function(global) {{
  'use strict';
  global.SKETCH_STYLE_REGISTRY_METADATA = {{
    sourceSha256: "{sha256_hash}",
    stylesCount: {len(registry_data.get("styles", []))},
    data: {json.dumps(registry_data, ensure_ascii=False, indent=2)}
  }};
}})(typeof window !== 'undefined' ? window : this);
"""
    output_js_path.parent.mkdir(parents=True, exist_ok=True)
    output_js_path.write_text(js_content, encoding="utf-8", newline="\n")
    return {
        "source": str(registry_path),
        "target": str(output_js_path),
        "sha256": sha256_hash,
    }


def main() -> int:
    base_dir = Path(__file__).parents[1]
    registry_path = base_dir / "references" / "style-registry.json"
    output_js_path = base_dir / "renderer" / "assets" / "style-registry-data.js"
    result = build_style_registry_asset(registry_path, output_js_path)
    print(f"Style registry data compiled successfully: {result['target']} (SHA256: {result['sha256']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
