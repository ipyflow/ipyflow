# -*- coding: utf-8 -*-
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Generator, List

from ipyflow.slicing.context import SlicingContext, iter_slicing_contexts


class EnumWithDefault(Enum):
    @classmethod
    def _missing_(cls, value):
        return cls(cls.__default__)  # type: ignore


class ExecutionMode(EnumWithDefault):
    NORMAL = "normal"
    REACTIVE = __default__ = "reactive"  # type: ignore


class ExecutionSchedule(EnumWithDefault):
    LIVENESS_BASED = "liveness_based"
    DAG_BASED = __default__ = "dag_based"  # type: ignore
    HYBRID_DAG_LIVENESS_BASED = "hybrid_dag_liveness_based"


class FlowDirection(EnumWithDefault):
    ANY_ORDER = "any_order"
    IN_ORDER = __default__ = "in_order"  # type: ignore


class Highlights(EnumWithDefault):
    ALL = "all"
    NONE = "none"
    EXECUTED = __default__ = "executed"  # type: ignore
    REACTIVE = "reactive"


class ReactivityMode(EnumWithDefault):
    BATCH = __default__ = "batch"  # type: ignore
    INCREMENTAL = "incremental"


# TODO: figure out how to represent different versions of
#  same interface (e.g. jupyterlab 4.0, notebook v7, etc)
class Interface(EnumWithDefault):
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
    UNKNOWN = __default__ = "unknown"  # type: ignore


class ColorScheme(EnumWithDefault):
    NORMAL = __default__ = "normal"  # type: ignore
    CLASSIC = "classic"  # type: ignore


class JsonSerializableMixin:
    def to_json(self: Any) -> Dict[str, Any]:
        json = {}
        for key, value in asdict(self).items():
            if isinstance(value, Enum):
                value = value.value
            if not isinstance(value, (bool, float, str)):
                value = str(value)
            json[key] = value
        return json


@dataclass(frozen=True)
class DataflowSettings(JsonSerializableMixin):
    test_context: bool
    mark_waiting_symbol_usages_unsafe: bool
    mark_typecheck_failures_unsafe: bool
    mark_phantom_cell_usages_unsafe: bool


@dataclass
class MutableDataflowSettings(JsonSerializableMixin):
    dataflow_enabled: bool
    trace_messages_enabled: bool
    highlights: Highlights
    interface: Interface
    static_slicing_enabled: bool
    dynamic_slicing_enabled: bool
    exec_mode: ExecutionMode
    exec_schedule: ExecutionSchedule
    flow_order: FlowDirection
    reactivity_mode: ReactivityMode
    push_reactive_updates: bool
    push_reactive_updates_to_cousins: bool
    pull_reactive_updates: bool
    color_scheme: ColorScheme
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
