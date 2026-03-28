import json

from brazil_module.services.intelligence.tools import (
    banking_tools,
    communication_tools,
    email_tools,
    erp_tools,
    fiscal_tools,
    purchasing_tools,
)

ALL_TOOL_MODULES = [
    erp_tools,
    purchasing_tools,
    fiscal_tools,
    banking_tools,
    email_tools,
    communication_tools,
]


def get_all_tool_schemas() -> list:
    schemas = []
    for mod in ALL_TOOL_MODULES:
        schemas.extend(mod.TOOL_SCHEMAS)
    return schemas


def execute_tool(tool_name: str, args: dict, executor) -> dict:
    prefix = tool_name.split("-")[0]
    module_map = {
        "erp": erp_tools,
        "p2p": purchasing_tools,
        "fiscal": fiscal_tools,
        "banking": banking_tools,
        "email": email_tools,
        "comm": communication_tools,
    }
    mod = module_map.get(prefix)
    if not mod:
        raise ValueError(f"Unknown tool prefix: {prefix}")
    return mod.execute_tool(tool_name, args, executor)


def filter_tools_for_module(module_tools_json: str) -> list:
    """Filter tool schemas based on module's tool patterns."""
    patterns = json.loads(module_tools_json or "[]")
    if not patterns:
        return get_all_tool_schemas()

    return _filter_by_patterns(get_all_tool_schemas(), patterns)


def _filter_by_patterns(schemas: list, patterns: list) -> list:
    """Filter tool schemas by name patterns (exact or prefix-*)."""
    filtered = []
    for schema in schemas:
        name = schema["name"]
        for pattern in patterns:
            if pattern.endswith("*"):
                if name.startswith(pattern[:-1]):
                    filtered.append(schema)
                    break
            elif name == pattern:
                filtered.append(schema)
                break
    return filtered
