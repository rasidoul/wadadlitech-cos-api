#!/usr/bin/env python3
"""Export a GPT-safe OpenAPI schema for supported COS actions.

The generated file preserves the deployed server URL, supported existing COS
operations, consequential-action metadata, and the app's actual schema models,
while excluding deprecated GitHub routes and device-only Reminders routes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Set

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main import app

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "cos_gpt_actions_openapi.json"
DEPLOYED_SERVER_URL = "https://wadadlitech-cos-api.onrender.com"
HTTP_METHODS = {"get", "put", "post", "delete", "patch", "options", "head"}


def resolve_ref(document: Dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    target: Any = document
    for part in ref[2:].split("/"):
        target = target.get(part.replace("~1", "/").replace("~0", "~"))
        if target is None:
            return None
    return target


def validate_local_refs(document: Dict[str, Any], obj: Any, stack: Set[str] = None) -> None:
    if stack is None:
        stack = set()

    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in stack:
                return
            target = resolve_ref(document, ref)
            if target is None:
                raise AssertionError(f"Unresolved local reference: {ref}")
            stack.add(ref)
            validate_local_refs(document, target, stack)
            stack.remove(ref)
        for value in obj.values():
            validate_local_refs(document, value, stack)
    elif isinstance(obj, list):
        for item in obj:
            validate_local_refs(document, item, stack)


def validate_export(document: Dict[str, Any]) -> None:
    components = document.get("components") or {}
    security_schemes = (components.get("securitySchemes") or {})
    if not security_schemes:
        raise AssertionError("Export is missing components.securitySchemes")

    # Validate security scheme names
    for op in _iter_operations(document):
        for requirement in op.get("security", []):
            for scheme_name in requirement.keys():
                if scheme_name not in security_schemes:
                    raise AssertionError(f"Operation {op.get('operationId')} references unknown security scheme: {scheme_name}")

    validate_local_refs(document, document)

    # Validate unique operation IDs
    seen: Set[str] = set()
    for op in _iter_operations(document):
        op_id = op.get("operationId")
        if not op_id:
            continue
        if op_id in seen:
            raise AssertionError(f"Duplicate operationId: {op_id}")
        seen.add(op_id)

    # Validate excluded routes are absent
    for path in document.get("paths", {}):
        if path.startswith("/github"):
            raise AssertionError(f"Deprecated path leaked into export: {path}")
        if path.startswith("/integrations/reminders"):
            raise AssertionError(f"Device-only reminder route leaked into export: {path}")


def _iter_operations(document: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for path_item in document.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in HTTP_METHODS and isinstance(operation, dict):
                yield operation


def build_export() -> Dict[str, Any]:
    original = app.openapi()
    filtered_paths: Dict[str, Any] = {}
    for path, path_item in (original.get("paths") or {}).items():
        if path.startswith("/github"):
            continue
        if path.startswith("/integrations/reminders"):
            continue
        filtered_paths[path] = path_item

    # Preserve the app's exact schema models and transitive $ref dependencies.
    original_components = original.get("components") or {}
    original_schemas = original_components.get("schemas", {})

    for path_item in filtered_paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation.get("operationId") in {"root__get", "health_health_get"}:
                continue
            operation["security"] = [{"bearerAuth": []}]

    export = {
        "openapi": original.get("openapi", "3.0.0"),
        "info": original.get("info", {}),
        "servers": [{"url": DEPLOYED_SERVER_URL}],
        "security": [{"bearerAuth": []}],
        "components": {
            "schemas": original_schemas,
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                }
            },
        },
        "paths": filtered_paths,
    }

    # Make sure any explicit security requirement uses the defined scheme name.
    for op in _iter_operations(export):
        if op.get("operationId") in {"root__get", "health_health_get"}:
            continue
        op["security"] = [{"bearerAuth": []}]

    validate_export(export)
    return export


def main() -> None:
    export = build_export()
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(export, fh, indent=2)
        fh.write("\n")

    print(f"Generated {OUTPUT_PATH}")
    print(f"Path count: {len(export.get('paths', {}))}")
    print("Validated: all local refs resolve, all security schemes exist, and operationIds are unique.")


if __name__ == "__main__":
    main()
