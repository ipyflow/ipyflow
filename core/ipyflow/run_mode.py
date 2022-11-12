# -*- coding: utf-8 -*-
import os
from enum import Enum


class FlowRunMode(Enum):
    PRODUCTION = "IPYFLOW_PRODUCTION"
    DEVELOP = "IPYFLOW_DEVELOP"

    @staticmethod
    def get():
        if (
            FlowRunMode.DEVELOP.value in os.environ
            and str(os.environ[FlowRunMode.DEVELOP.value]) == "1"
        ):
            return FlowRunMode.DEVELOP
        else:
            return FlowRunMode.PRODUCTION


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
