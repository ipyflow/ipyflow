# -*- coding: future_annotations -*-
from enum import Enum
import os


class SafetyRunMode(Enum):
    PRODUCTION = 'NBSAFETY_PRODUCTION'
    DEVELOP = 'NBSAFETY_DEVELOP'

    @staticmethod
    def get():
        if SafetyRunMode.DEVELOP.value in os.environ and str(os.environ[SafetyRunMode.DEVELOP.value]) == '1':
            return SafetyRunMode.DEVELOP
        else:
            return SafetyRunMode.PRODUCTION
