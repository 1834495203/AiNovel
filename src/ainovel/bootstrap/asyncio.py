"""Application event loop policy for PostgreSQL's asynchronous driver on Windows."""

import asyncio
import sys


def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()
