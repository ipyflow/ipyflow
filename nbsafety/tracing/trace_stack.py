# -*- coding: future_annotations -*-
import itertools
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any, Callable, Dict, List, Set, Tuple

    # avoid circular imports
    from nbsafety.tracing.trace_manager import BaseTraceManager


class TraceStack:
    def __init__(self, manager: BaseTraceManager):
        self._manager = manager
        self._stack: List[Tuple[Any, ...]] = []
        self._stack_item_initializers: Dict[str, Callable[[], Any]] = {}
        self._stack_items_with_manual_initialization: Set[str] = set()
        self._registering_stack_state_context = False

    def _stack_item_names(self):
        return itertools.chain(self._stack_item_initializers.keys(), self._stack_items_with_manual_initialization)

    @contextmanager
    def register_stack_state(self):
        self._registering_stack_state_context = True
        original_state = set(self._manager.__dict__.keys())
        yield
        self._registering_stack_state_context = False
        stack_item_names = set(self._manager.__dict__.keys() - original_state)
        for stack_item_name in stack_item_names - self._stack_items_with_manual_initialization:
            stack_item = self._manager.__dict__[stack_item_name]
            if isinstance(stack_item, TraceStack):
                self._stack_item_initializers[stack_item_name] = stack_item._clone
            elif stack_item is None:
                self._stack_item_initializers[stack_item_name] = lambda: None
            elif isinstance(stack_item, bool):
                init_val = bool(stack_item)
                self._stack_item_initializers[stack_item_name] = lambda: init_val
            else:
                self._stack_item_initializers[stack_item_name] = type(stack_item)

    @contextmanager
    def needing_manual_initialization(self):
        assert self._registering_stack_state_context
        original_state = set(self._manager.__dict__.keys())
        yield
        self._stack_items_with_manual_initialization = set(self._manager.__dict__.keys() - original_state)

    @contextmanager
    def push(self):
        """
        Checks at the end of the context that everything requiring manual init was manually inited.
        """
        self._stack.append(tuple(self._manager.__dict__[stack_item] for stack_item in self._stack_item_names()))
        for stack_item, initializer in self._stack_item_initializers.items():
            self._manager.__dict__[stack_item] = initializer()
        for stack_item in self._stack_items_with_manual_initialization:
            del self._manager.__dict__[stack_item]
        yield
        uninitialized_items = []
        for stack_item in self._stack_items_with_manual_initialization:
            if stack_item not in self._manager.__dict__:
                uninitialized_items.append(stack_item)
        if len(uninitialized_items) > 0:
            raise ValueError(
                "Stack item(s) %s requiring manual initialization were not initialized" % uninitialized_items
            )

    def _clone(self):
        new_tracing_stack = TraceStack(self._manager)
        new_tracing_stack.__dict__ = dict(self.__dict__)
        new_tracing_stack._stack = []
        return new_tracing_stack

    def pop(self):
        for stack_item_name, stack_item in zip(self._stack_item_names(), self._stack.pop()):
            self._manager.__dict__[stack_item_name] = stack_item

    def clear(self):
        self._stack = []

    def __len__(self):
        return len(self._stack)


