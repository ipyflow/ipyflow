# -*- coding: utf-8 -*-
from enum import Enum
import os


class SafetyRunMode(Enum):
    PRODUCTION = "NBSAFETY_PRODUCTION"
    DEVELOP = "NBSAFETY_DEVELOP"

    @staticmethod
    def get():
        if (
            SafetyRunMode.DEVELOP.value in os.environ
            and str(os.environ[SafetyRunMode.DEVELOP.value]) == "1"
        ):
            return SafetyRunMode.DEVELOP
        else:
            return SafetyRunMode.PRODUCTION


class ExecutionMode(Enum):
    NORMAL = "normal"
    REACTIVE = "reactive"


class ExecutionSchedule(Enum):
    LIVENESS_BASED = "liveness_based"
    DAG_BASED = "dag_based"
    STRICT = "strict"


class FlowOrder(Enum):
    ANY_ORDER = "any_order"
    IN_ORDER = "in_order"
