# -*- coding: utf-8 -*-
import logging
from typing import TYPE_CHECKING, Optional, cast

from ipyflow.data_model.timestamp import Timestamp
from ipyflow.run_mode import ExecutionMode
from ipyflow.singletons import flow
from ipyflow.tracing.external_calls import resolve_external_call
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
        is_lhs_ref: bool = False,
    ) -> None:
        self.dsym = dsym
        self.atom = atom
        self.next_atom = next_atom
        self.liveness_timestamp = liveness_timestamp
        self.is_lhs_ref = is_lhs_ref

    def update_usage_info(self, *args, **kwargs) -> None:
        kwargs["is_blocking"] = kwargs.get("is_blocking", self.is_blocking)
        if self.is_lhs_ref:
            kwargs["exclude_ns"] = True
        self.dsym.update_usage_info(*args, **kwargs)

    def __hash__(self) -> int:
        return hash(
            (
                self.dsym,
                self.atom,
                self.next_atom,
                self.liveness_timestamp,
                self.is_lhs_ref,
            )
        )

    @property
    def timestamp(self) -> Timestamp:
        if self.is_deep:
            return self.dsym.timestamp
        else:
            return self.dsym.timestamp_excluding_ns_descendents

    @property
    def is_anonymous(self) -> bool:
        return self.dsym.is_anonymous

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
            and self.dsym.is_cascading_reactive_at_counter(
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
            or (self.is_live and flow().is_updated_deep_reactive(self.dsym))
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
        if not self.next_atom.is_callpoint:
            return False
        ext_call = resolve_external_call(
            None,
            self.dsym.obj,
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
            not self.atom.is_callpoint
            and not isinstance(self.dsym.obj, (dict, list, tuple))
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
