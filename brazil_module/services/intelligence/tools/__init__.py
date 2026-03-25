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
