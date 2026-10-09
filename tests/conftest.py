import pytest

from ainovel.bootstrap.asyncio import event_loop_policy as application_event_loop_policy


@pytest.hookimpl(optionalhook=True)
def pytest_asyncio_loop_factories():
    return {"postgres": application_event_loop_policy().new_event_loop}
