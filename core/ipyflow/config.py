# -*- coding: utf-8 -*-
from dataclasses import dataclass
from enum import Enum
from typing import Generator, List, NamedTuple

from ipyflow.slicing.context import SlicingContext, iter_slicing_contexts


class ExecutionMode(Enum):
    NORMAL = "normal"
    REACTIVE = "reactive"


class ExecutionSchedule(Enum):
    LIVENESS_BASED = "liveness_based"
    DAG_BASED = "dag_based"
    HYBRID_DAG_LIVENESS_BASED = "hybrid_dag_liveness_based"
    STRICT = "strict"


class FlowDirection(Enum):
    ANY_ORDER = "any_order"
    IN_ORDER = "in_order"


class Highlights(Enum):
    ALL = "all"
    NONE = "none"
    EXECUTED = "executed"
    REACTIVE = "reactive"


# TODO: figure out how to represent different versions of
#  same interface (e.g. jupyterlab 4.0, notebook v7, etc)
class Interface(Enum):
    BENTO = "bento"  # ~TODO
    COLAB = "colab"  # TODO
    DATABRICKS = "databricks"  # TODO
    DATALORE = "datalore"  # TODO
    DEEPNOTE = "deepnote"  # TODO
    HEX = "hex"  # TODO
    IPYTHON = "ipython"
    JUPYTER = "jupyter"
    JUPYTERLAB = "jupyterlab"
    NOTEABLE = "noteable"  # TODO
    VSCODE = "vscode"  # TODO
    UNKNOWN = "unknown"


class DataflowSettings(NamedTuple):
    test_context: bool
    use_comm: bool
    mark_waiting_symbol_usages_unsafe: bool
    mark_typecheck_failures_unsafe: bool
    mark_phantom_cell_usages_unsafe: bool


@dataclass
class MutableDataflowSettings:
    dataflow_enabled: bool
    trace_messages_enabled: bool
    highlights: Highlights
    interface: Interface
    static_slicing_enabled: bool
    dynamic_slicing_enabled: bool
    exec_mode: ExecutionMode
    exec_schedule: ExecutionSchedule
    flow_order: FlowDirection
    warn_out_of_order_usages: bool
    lint_out_of_order_usages: bool
    syntax_transforms_enabled: bool
    syntax_transforms_only: bool
    max_external_call_depth_for_tracing: int
    is_dev_mode: bool

    def slicing_contexts(self) -> List[SlicingContext]:
        ret: List[SlicingContext] = []
        if self.dynamic_slicing_enabled:
            ret.append(SlicingContext.DYNAMIC)
        if self.static_slicing_enabled:
            ret.append(SlicingContext.STATIC)
        return ret

    def iter_slicing_contexts(self) -> Generator[None, None, None]:
        for _ in iter_slicing_contexts(*self.slicing_contexts()):
            yield
