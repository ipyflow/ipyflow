import sys
from typing import Dict, Set, Tuple, Type, Union

import pyccolo as pyc

T_EXC_TYPE = Union[Type[BaseException], Tuple[Type[BaseException], ...]]


class PlaceholderException(BaseException):
    pass


class InterruptTracer(pyc.BaseTracer):
    global_guards_enabled = False
    requires_ast_bookkeeping = False

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.num_interrupts_by_node_id: Dict[int, int] = {}

    @staticmethod
    def _get_interrupt_count_threshold(exc_type_set: Set[Type[BaseException]]) -> int:
        if KeyboardInterrupt in exc_type_set:
            # explicit threshold
            return 2
        else:
            # implicit threshold
            return 1

    def should_instrument_file(self, filename: str) -> bool:
        # TODO: get this working (requires optimizing pyccolo, otherwise it's too slow to be useful for cache misses)
        # return filename.endswith(".py")
        return False

    @pyc.register_raw_handler(pyc.exception_handler_type, exempt_from_guards=True)
    def handle_exception_handler_type(
        self, exc_type: T_EXC_TYPE, node_id: int, *_, **__
    ) -> Union[T_EXC_TYPE, None]:
        is_handling_interrupt = sys.exc_info()[0] is KeyboardInterrupt
        if not is_handling_interrupt:
            return None
        exc_types = exc_type if isinstance(exc_type, tuple) else (exc_type,)
        if not all(
            isinstance(t, type) and issubclass(t, BaseException) for t in exc_types
        ):
            return None
        num_interrupts_at_node = self.num_interrupts_by_node_id.get(node_id, 0) + 1
        self.num_interrupts_by_node_id[node_id] = num_interrupts_at_node
        exc_type_set: Set[Type[BaseException]] = set(exc_types)
        if num_interrupts_at_node < self._get_interrupt_count_threshold(exc_type_set):
            return None
        if BaseException in exc_type_set:
            exc_type_set.remove(BaseException)
            exc_type_set |= set(BaseException.__subclasses__())
        exc_type_set.discard(KeyboardInterrupt)
        exc_type_set.discard(SystemExit)
        if len(exc_type_set) == 0:
            exc_type_set.add(PlaceholderException)
        return tuple(exc_type_set)
