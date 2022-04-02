import asyncio
import sys


def apply():
    import nest_asyncio

    # ref: https://github.com/erdewit/nest_asyncio/issues/14
    nest_asyncio._patch_task = _patched_patch_task

    # ref: https://github.com/erdewit/nest_asyncio
    nest_asyncio.apply()


def _patched_patch_task():
    """Patch the Task's step and enter/leave methods to make it reentrant."""

    def step(task, exc=None):
        curr_task = curr_tasks.get(task._loop)
        try:
            step_orig(task, exc)
        finally:
            if curr_task is None:
                curr_tasks.pop(task._loop, None)
            else:
                curr_tasks[task._loop] = curr_task

    Task = asyncio.Task
    if sys.version_info >= (3, 7, 0):

        def enter_task(loop, task):
            curr_tasks[loop] = task

        def leave_task(loop, task):
            curr_tasks.pop(loop, None)

        asyncio.tasks._enter_task = enter_task
        asyncio.tasks._leave_task = leave_task
        curr_tasks = asyncio.tasks._current_tasks
    else:
        curr_tasks = Task._current_tasks
    try:
        step_orig = Task._Task__step
        Task._Task__step = step
    except AttributeError:
        try:
            step_orig = Task.__step
            Task.__step = step
        except AttributeError:
            step_orig = Task._step
            Task._step = step
