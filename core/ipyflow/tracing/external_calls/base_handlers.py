# -*- coding: utf-8 -*-
import ast
import logging
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

from ipyflow.singletons import flow, tracer
from ipyflow.types import IMMUTABLE_PRIMITIVE_TYPES

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol

    ExternalCallArgument = Tuple[Any, Set[Symbol]]


logger = logging.getLogger(__name__)


class HasGetitem(type):
    """
    Mixin for indicating that a class has a __getitem__ method
    """

    def __getitem__(cls, item):
        return NotImplemented


class ExternalCallHandler(metaclass=HasGetitem):
    not_yet_defined = object()
    module: Optional[ModuleType] = None
    caller_self: Any = None
    function_or_method: Any = None
    args: List["ExternalCallArgument"]
    kwargs: Dict[str, "ExternalCallArgument"]
    _arg_syms: Optional[Set["Symbol"]] = None
    return_value: Any = not_yet_defined
    stmt_node: ast.stmt

    def __new__(cls, *args, **kwargs):
        if cls is ExternalCallHandler:
            raise TypeError(f"only children of '{cls.__name__}' may be instantiated")
        return object.__new__(cls)

    @classmethod
    def create(cls, **kwargs) -> "ExternalCallHandler":
        module = kwargs.pop("module", None)
        caller_self = kwargs.pop("caller_self", None)
        function_or_method = kwargs.pop("function_or_method", None)
        call_node = kwargs.pop("call_node", None)
        calling_symbol = kwargs.pop("calling_symbol", None)
        return cls(
            module=module,
            caller_self=caller_self,
            function_or_method=function_or_method,
            call_node=call_node,
            calling_symbol=calling_symbol,
        )._initialize_impl(**kwargs)

    def _initialize_impl(self, **kwargs) -> "ExternalCallHandler":
        ret = self
        for cls in self.__class__.mro():
            if not hasattr(cls, "initialize"):
                break
            ret = cls.initialize(ret, **kwargs) or ret  # type: ignore
        return ret

    def initialize(self, **_) -> Optional["ExternalCallHandler"]:
        return self

    def __init__(
        self,
        *,
        module: Optional[ModuleType] = None,
        caller_self: Any = None,
        function_or_method: Any = None,
        call_node: Optional[ast.Call] = None,
        calling_symbol: Optional["Symbol"] = None,
    ) -> None:
        self.module = module
        self.caller_self = caller_self
        self.function_or_method = function_or_method
        self.args: List["ExternalCallArgument"] = []
        self.kwargs: Dict[str, "ExternalCallArgument"] = {}
        self._arg_syms: Optional[Set["Symbol"]] = None
        self.return_value: Any = self.not_yet_defined
        self.call_node = call_node
        self.calling_symbol = calling_symbol
        self.stmt_node = tracer().prev_trace_stmt_in_cur_frame.stmt_node  # type: ignore[union-attr]

    def __init_subclass__(cls):
        external_call_handler_by_name[cls.__name__] = cls

    @property
    def modname(self) -> Optional[str]:
        if self.module is not None:
            return self.module.__name__
        function_or_method = self.function_or_method
        if function_or_method is None:
            return None
        clazz = getattr(function_or_method, "__class__", None)
        if clazz is None:
            return None
        return getattr(clazz, "__module__", None)

    @property
    def caller_self_obj_id(self) -> Optional[int]:
        return None if self.caller_self is None else id(self.caller_self)

    @property
    def arg_syms(self) -> Set["Symbol"]:
        if self._arg_syms is None:
            self._arg_syms = set().union(
                *(arg[1] for arg in self.args + list(self.kwargs.values()))
            )
        return self._arg_syms

    def process_arg(self, arg: Any) -> None:
        pass

    def _process_arg_impl(self, arg: "ExternalCallArgument") -> None:
        self.args.append(arg)
        self.process_arg(arg[0])

    def process_args(self, args: List["ExternalCallArgument"]) -> None:
        for arg in args:
            self._process_arg_impl(arg)

    def process_kwarg(self, kw: str, arg: Any) -> None:
        pass

    def _process_kwarg_impl(self, kw: str, arg: "ExternalCallArgument") -> None:
        self.kwargs[kw] = arg
        self.process_kwarg(kw, arg[0])

    def process_kwargs(self, kwargs: Dict[str, "ExternalCallArgument"]) -> None:
        for kw, arg in kwargs.items():
            self._process_kwarg_impl(kw, arg)

    def process_return(self, return_value: Any) -> None:
        self.return_value = return_value

    def _handle_impl(self) -> None:
        # TODO: why was this here
        # Timestamp.update_usage_info(self.arg_syms)
        result = self.handle()
        if result is None or self.call_node is None:
            return
        symbols = (
            cast(Iterable["Symbol"], result)
            if hasattr(result, "__iter__")
            else [cast("Symbol", result)]
        )
        tracer().node_id_to_loaded_symbols.setdefault(id(self.call_node), []).extend(
            symbols
        )

    def mutate_caller(self, should_propagate: bool) -> None:
        if self.caller_self is None:
            return
        if self.calling_symbol is not None:
            self._mutate_calling_symbol(
                self.calling_symbol, should_propagate=should_propagate
            )
        elif self.call_node is not None:
            syms_to_mutate = []
            if isinstance(self.call_node.func, ast.Attribute) and isinstance(
                self.call_node.func.value, ast.Name
            ):
                syms_to_mutate = [
                    sym
                    for sym in flow().aliases.get(self.caller_self_obj_id or -1, [])
                    if sym.name == self.call_node.func.value.id
                ]
            for sym in syms_to_mutate:
                self._mutate_calling_symbol(sym, should_propagate=should_propagate)
            if len(syms_to_mutate) == 0:
                self.mutate_aliases(
                    self.caller_self_obj_id, should_propagate=should_propagate
                )

    def mutate_module(self, should_propagate: bool) -> None:
        if self.module is None:
            return
        self.mutate_aliases(id(self.module), should_propagate=should_propagate)

    def mutate_aliases(self, obj_id: Optional[int], should_propagate: bool) -> None:
        mutated_syms = flow().aliases.get(obj_id or -1, set())
        for sym in mutated_syms:
            self._mutate_calling_symbol(sym, should_propagate=should_propagate)

    def _mutate_calling_symbol(self, sym: "Symbol", should_propagate: bool) -> None:
        sym.update_deps(
            self.arg_syms,
            overwrite=False,
            mutated=True,
            propagate_to_namespace_descendents=should_propagate,
            refresh=should_propagate,
        )

    def handle(self) -> Optional[Union["Symbol", Iterable["Symbol"]]]:
        pass


external_call_handler_by_name: Dict[str, Type[ExternalCallHandler]] = {}
REGISTERED_HANDLER_BY_FUNCTION: Dict[Callable, Type[ExternalCallHandler]] = {}
REGISTERED_HANDLER_BY_METHOD: Dict[Tuple[type, str], Type[ExternalCallHandler]] = {}


class NoopCallHandler(ExternalCallHandler):
    pass


# TODO: use dsl for these instead
ARG_MUTATION_EXCEPTED_MODULES = {
    "alt",
    "altair",
    "display",
    "logging",
    "matplotlib",
    "pyplot",
    "plot",
    "plt",
    "seaborn",
    "sns",
    "widget",
}


class StandardMutation(ExternalCallHandler):
    def _maybe_mutate_caller(self) -> None:
        if self.return_value is not None and self.caller_self is not self.return_value:
            return
        self.mutate_caller(should_propagate=True)

    def handle(self) -> None:
        if self.caller_self is not None:
            self._maybe_mutate_caller()
        elif self.module is not None and self.return_value is None:
            self.mutate_module(should_propagate=True)
            if len(self.args) == 0:
                return
            # FIXME: extremely hacky
            if (self.module.__name__ or "").split(".")[
                0
            ] in ARG_MUTATION_EXCEPTED_MODULES:
                return
            # FIXME: extremely hacky here too
            first_arg_obj, first_arg_syms = self.args[0]
            if isinstance(first_arg_obj, (list, set, dict) + IMMUTABLE_PRIMITIVE_TYPES):
                return
            depending_on_first_arg = []
            for obj, syms in self.args[1:]:
                filtered_syms = {
                    sym
                    for sym in syms
                    if any(first_sym in sym.parents for first_sym in first_arg_syms)
                }
                if len(filtered_syms) > 0:
                    depending_on_first_arg.append((obj, filtered_syms))
            self.args = [self.args[0]] + depending_on_first_arg
            self._arg_syms = None
            ArgMutate.handle(self)  # type: ignore


class CallerMutation(ExternalCallHandler):
    def handle(self) -> None:
        self.mutate_caller(should_propagate=True)


class CallerUpsert(ExternalCallHandler):
    def handle(self) -> None:
        for module_sym in flow().aliases.get(id(self.caller_self), []):
            module_sym.update_deps(
                self.arg_syms,
                overwrite=True,
                propagate_to_namespace_descendents=True,
                refresh=True,
            )
            tracer().pending_usage_updates_by_sym.pop(module_sym, None)


class ModuleMutation(ExternalCallHandler):
    def handle(self) -> None:
        self.mutate_module(should_propagate=True)


class ModuleUpsert(ExternalCallHandler):
    def handle(self) -> None:
        for module_sym in flow().aliases.get(id(self.module), []):
            module_sym.update_deps(
                self.arg_syms,
                overwrite=True,
                propagate_to_namespace_descendents=True,
                refresh=True,
            )
            tracer().pending_usage_updates_by_sym.pop(module_sym, None)


class NamespaceClear(StandardMutation):
    def handle(self) -> None:
        super().handle()
        mutated_sym = flow().get_first_full_symbol(self.caller_self_obj_id or -1)
        if mutated_sym is None:
            return
        namespace = mutated_sym.namespace
        if namespace is None:
            return
        for name in sorted(
            (
                sym.name
                for sym in namespace.all_symbols_this_indentation(
                    exclude_class=True, is_subscript=True
                )
            ),
            reverse=True,
            key=lambda n: n or -1,
        ):
            namespace.delete_symbol_for_name(name, is_subscript=True)


class MutatingMethodEventNotYetImplemented(ExternalCallHandler):
    pass


class ArgMutate(ExternalCallHandler):
    def handle(self) -> None:
        for mutated_sym in self.arg_syms:
            if mutated_sym is None or mutated_sym.is_anonymous:
                continue
            # TODO: happens when module mutates args
            #  should we add module as a dep in this case?
            mutated_sym.update_deps(set(), overwrite=False, mutated=True)
