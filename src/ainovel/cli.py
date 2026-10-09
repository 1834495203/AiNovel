"""Application entry points, not a collection of ad hoc scripts."""

import argparse
import asyncio
import logging

import uvicorn

from ainovel.bootstrap.asyncio import event_loop_policy
from ainovel.bootstrap.runtime import Runtime
from ainovel.controller.api import create_app
from ainovel.core.config import Settings
from ainovel.infrastructure.job_queue import JobQueue
from ainovel.mapper.store import Store
from ainovel.service.worker import Worker


class Application:
    async def initialize(self) -> None:
        store = Store(Settings())
        try:
            await store.initialize()
        finally:
            await store.close()

    async def worker(self) -> None:
        runtime = Runtime()
        await Worker(runtime, JobQueue(runtime.settings.redis_url)).run()

    def run(self) -> None:
        asyncio.set_event_loop_policy(event_loop_policy())
        parser = argparse.ArgumentParser(description="AI Novel application")
        parser.add_argument("mode", choices=("init-db", "api", "worker"))
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8000)
        arguments = parser.parse_args()
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        if arguments.mode == "init-db":
            asyncio.run(self.initialize())
        elif arguments.mode == "worker":
            asyncio.run(self.worker())
        else:
            uvicorn.run(create_app(), host=arguments.host, port=arguments.port)


def main() -> None:
    Application().run()
