# -*- coding: utf-8 -*-
import functools


def return_arg_at_index(index, logger):
    def return_arg_func(exc, *args, **kwargs):
        logger.warning('Exception occurred: %s' % exc)
        if args is not None and len(args) > index:
            return args[index]
        return None
    return return_arg_func


def return_val(val, logger):
    def return_val_func(exc, *args, **kwargs):
        logger.warning('Exception occurred: %s' % exc)
        return val
    return return_val_func


# TODO: hook into safety.is_develop
IS_DEVELOP = True


def on_exception_default_to(recovery_func):
    def make_wrapper(original_func):
        @functools.wraps(original_func)
        def wrapped_func(*args, **kwargs):
            try:
                return original_func(*args, **kwargs)
            except Exception as e:
                if IS_DEVELOP:
                    raise e
                else:
                    return recovery_func(e, *args, **kwargs)
        return wrapped_func
    return make_wrapper
