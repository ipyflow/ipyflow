# -*- coding: utf-8 -*-
import logging
from typing import TYPE_CHECKING, Optional, cast

from ipyflow.config import ExecutionMode
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.singletons import flow
from ipyflow.tracing.external_calls import resolve_external_call
from ipyflow.utils import CommonEqualityMixin

if TYPE_CHECKING:
    from ipyflow.analysis.symbol_ref import Atom
    from ipyflow.data_model.symbol import Symbol


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class ResolvedSymbol(CommonEqualityMixin):
    def __init__(
        self,
        sym: "Symbol",
        atom: "Atom",
        next_atom: Optional["Atom"],
        liveness_timestamp: Optional[Timestamp] = None,
        is_lhs_ref: bool = False,
        is_killed: bool = False,
    ) -> None:
        self.sym = sym
        self.atom = atom
        self.next_atom = next_atom
        self.liveness_timestamp = liveness_timestamp
        self.is_lhs_ref = is_lhs_ref
        self.is_killed = is_killed

    def update_usage_info(self, *args, **kwargs) -> None:
        kwargs["is_blocking"] = kwargs.get("is_blocking", self.is_blocking)
        if self.is_lhs_ref:
            kwargs["exclude_ns"] = True
        self.sym.update_usage_info(*args, **kwargs)

    def __repr__(self) -> str:
        return f"|->{self.sym}|"

    def __hash__(self) -> int:
        return hash(
            (
                self.sym,
                self.atom,
                self.next_atom,
                self.liveness_timestamp,
                self.is_lhs_ref,
                self.is_killed,
            )
        )

    @property
    def timestamp(self) -> Timestamp:
        if self.is_deep:
            return self.sym.timestamp
        else:
            return self.sym.shallow_timestamp

    @property
    def is_anonymous(self) -> bool:
        return self.sym.is_anonymous

    @property
    def is_called(self) -> bool:
        return self.atom.is_callpoint

    @property
    def is_last(self) -> bool:
        return self.next_atom is None

    @property
    def is_cascading_reactive(self):
        return self.atom.is_cascading_reactive or (
            self.is_live
            and self.sym.is_cascading_reactive_at_counter(
                self.liveness_timestamp.cell_num
            )
        )

    @property
    def is_reactive(self) -> bool:
        if self.is_blocking:
            return False
        return (
            (self.atom.is_reactive and not self.is_lhs_ref)
            or self.is_cascading_reactive
            or (self.is_live and flow().is_updated_deep_reactive(self.sym))
        )

    @property
    def is_blocking(self) -> bool:
        return self.atom.is_blocking or (
            self.is_live
            and flow().blocked_reactive_timestamps_by_symbol.get(self.sym, -1)
            >= self.sym.timestamp.cell_num
        )

    @property
    def is_dead(self) -> bool:
        return self.liveness_timestamp is None or self.is_killed

    @property
    def is_live(self) -> bool:
        return not self.is_dead

    @property
    def is_deep(self) -> bool:
        # for live symbols, if it is used in its entirety
        assert self.is_live
        if self.is_lhs_ref:
            return False
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
        if self.next_atom is None:
            return False
        if not self.next_atom.is_callpoint:
            return False
        ext_call = resolve_external_call(
            None,
            self.sym.obj,
            None,
            cast(str, self.next_atom.value),
            call_node=None,
            use_standard_default=False,
        )
        return ext_call is not None

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
            isinstance(self.sym.obj, (list, tuple))
            and isinstance(self.next_atom.value, int)
            and self.next_atom.value >= len(self.sym.obj)
        ):
            return True
        if isinstance(self.sym.obj, dict) and self.next_atom.value not in self.sym.obj:
            return True
        if (
            not self.atom.is_callpoint
            and not isinstance(self.sym.obj, (dict, list, tuple))
            and isinstance(self.next_atom.value, str)
            and (
                # the first check guards against properties; hasattr actually executes code for those if called
                # on the actual object, which we want to avoid
                not hasattr(self.sym.obj.__class__, self.next_atom.value)
                and not hasattr(self.sym.obj, self.next_atom.value)
            )
        ):
            # TODO: fix this once we can distinguish between attrs and subscripts in the chain
            return True
        return False

    def is_waiting_at_position(self, pos: int) -> bool:
        return self.sym.is_waiting_at_position(pos, deep=self.is_deep)
