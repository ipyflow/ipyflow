# -*- coding: utf-8 -*-
import logging
from typing import cast, TYPE_CHECKING, Optional
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.run_mode import ExecutionMode
from ipyflow.singletons import flow
from ipyflow.tracing.mutation_event import resolve_mutating_method
from ipyflow.utils import CommonEqualityMixin

if TYPE_CHECKING:
    from ipyflow.analysis.symbol_ref import Atom
    from ipyflow.data_model.data_symbol import DataSymbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ResolvedDataSymbol(CommonEqualityMixin):
    def __init__(
        self,
        dsym: "DataSymbol",
        atom: "Atom",
        next_atom: Optional["Atom"],
        liveness_timestamp: Optional[Timestamp] = None,
    ) -> None:
        self.dsym = dsym
        self.atom = atom
        self.next_atom = next_atom
        self.liveness_timestamp = liveness_timestamp

    def __hash__(self) -> int:
        return hash(
            (
                self.dsym,
                self.atom,
                self.next_atom,
                self.liveness_timestamp,
            )
        )

    @property
    def timestamp(self) -> Timestamp:
        return self.dsym.timestamp

    @property
    def is_called(self) -> bool:
        return self.atom.is_callpoint

    @property
    def is_last(self) -> bool:
        return self.next_atom is None

    @property
    def is_recursive_reactive(self):
        return self.atom.is_recursive_reactive or (
            self.is_live
            and self.dsym.is_recursive_reactive_at_counter(
                self.liveness_timestamp.cell_num
            )
        )

    @property
    def is_reactive(self) -> bool:
        if self.is_blocking:
            return False
        return (
            self.atom.is_reactive
            or self.is_recursive_reactive
            or (self.is_live and self.dsym in flow().updated_deep_reactive_symbols)
        )

    @property
    def is_blocking(self) -> bool:
        return self.atom.is_blocking or (
            self.is_live
            and flow().blocked_reactive_timestamps_by_symbol.get(self.dsym, -1)
            >= self.dsym.timestamp.cell_num
        )

    @property
    def is_dead(self) -> bool:
        return self.liveness_timestamp is None

    @property
    def is_live(self) -> bool:
        return not self.is_dead

    @property
    def is_deep(self) -> bool:
        # for live symbols, if it is used in its entirety
        assert self.is_live
        if self.is_reactive:
            return True
        if self.next_atom is None:
            return True
        elif not self.next_atom.is_callpoint:
            return False
        elif self.is_mutating:  # self.next_atom.is_callpoint
            return False
        else:
            return True

    @property
    def is_shallow(self) -> bool:
        # for live symbols, if only a portion (attr or subscript) is used
        assert self.is_live
        return not self.is_deep

    @property
    def is_mutating(self) -> bool:
        assert self.is_live
        if not self.next_atom.is_callpoint:
            return False
        return (
            resolve_mutating_method(self.dsym.obj, cast(str, self.next_atom.value))
            is not None
        )

    @property
    def is_unsafe(self) -> bool:
        assert self.is_live
        if self.next_atom is None:
            return False
        if self.next_atom.is_callpoint:
            if self.is_mutating and (
                self.is_reactive
                or flow().mut_settings.exec_mode == ExecutionMode.REACTIVE
            ):
                return True
            else:
                return False
        if (
            isinstance(self.dsym.obj, (list, tuple))
            and isinstance(self.next_atom.value, int)
            and self.next_atom.value >= len(self.dsym.obj)
        ):
            return True
        if (
            isinstance(self.dsym.obj, dict)
            and self.next_atom.value not in self.dsym.obj
        ):
            return True
        if (
            not isinstance(self.dsym.obj, (dict, list, tuple))
            and isinstance(self.next_atom.value, str)
            and (
                # the first check guards against properties; hasattr actually executes code for those if called
                # on the actual object, which we want to avoid
                not hasattr(self.dsym.obj.__class__, self.next_atom.value)
                and not hasattr(self.dsym.obj, self.next_atom.value)
            )
        ):
            # TODO: fix this once we can distinguish between attrs and subscripts in the chain
            return True
        return False

    def is_waiting_at_position(self, pos: int) -> bool:
        return self.dsym.is_waiting_at_position(pos, deep=self.is_deep)
