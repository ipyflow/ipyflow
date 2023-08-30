# -*- coding: utf-8 -*-
import ast
import builtins
import logging
import sys
import textwrap
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Type,
    TypeVar,
    Union,
)

import black
import pyccolo as pyc
from ipywidgets import HTML

from ipyflow.analysis.live_refs import compute_live_dead_symbol_refs
from ipyflow.config import Interface
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import cells
from ipyflow.singletons import flow, shell, tracer
from ipyflow.slicing.context import (
    SlicingContext,
    dangling_context,
    dangling_ctx_var,
    iter_dangling_contexts,
    slicing_ctx_var,
)
from ipyflow.types import IdType, TimestampOrCounter

if sys.version_info >= (3, 8):
    from typing import Protocol
else:
    Protocol = object

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol


FormatType = TypeVar("FormatType", HTML, str)
SliceRefType = Union["SliceableMixin", IdType, Timestamp]


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class Slice:
    FUNC_PREFIX = "_X5ix_ipyflow_slice_func_"
    _func_counter = 0

    def __init__(
        self,
        raw_slice: Dict[int, str],
        blacken: bool,
        format_type: Optional[Type[FormatType]] = None,
    ) -> None:
        self.raw_slice = dict(raw_slice)
        self.iface = flow().mut_settings.interface
        if format_type is None:
            if self.iface in (Interface.IPYTHON, Interface.UNKNOWN):
                fmt: Type[FormatType] = str  # type: ignore
            else:
                fmt = HTML
        else:
            fmt = format_type  # type: ignore
        self.format_type: Type[FormatType] = fmt  # type: ignore
        self.blacken = blacken

    def _get_slice_text_from_slice(self) -> str:
        return "\n\n".join(
            f"# Cell {cell_num}\n" + content
            for cell_num, content in sorted(self.raw_slice.items())
        ).strip()

    def _make_slice_widget(self) -> HTML:
        slice_text = self._get_slice_text_from_slice()
        slice_text_linked_cells = []
        if self.iface == Interface.JUPYTER:
            container_selector = (
                "javascript:document.getElementById('notebook-container')"
            )
        elif self.iface == Interface.JUPYTERLAB:
            container_selector = (
                "javascript:document.getElementById("
                "document.querySelector('.jp-mod-current').dataset.id).children[2]"
                # the below is necessary for jupyterlab >= 4.0
                # TODO: should we also support jupyterlab < 4.0?
                ".children[0].children[0]"
            )
        else:
            container_selector = None
        for cell_num, content in sorted(self.raw_slice.items()):
            cell = cells().at_counter(cell_num)
            if (
                container_selector is not None
                and cell.is_current_for_id
                and cell.position >= 0
            ):
                rendered_cell = (
                    f'# <a href="{container_selector}.children[{cell.position}].scrollIntoView()">'
                    f"Cell {cell_num}</a>"
                )
            else:
                rendered_cell = f"# Cell {cell_num}"
            slice_text_linked_cells.append(rendered_cell + f"\n{content}")
        slice_text_no_cells = "\n".join(
            content for _cell_num, content in sorted(self.raw_slice.items())
        )
        if self.blacken:
            slice_text_no_cells = black.format_str(
                slice_text_no_cells, mode=black.FileMode()
            ).strip()
        if self.iface == Interface.JUPYTER:
            classes = "output_subarea output_text output_stream output_stdout"
        elif self.iface == Interface.JUPYTERLAB:
            classes = "lm-Widget p-Widget jp-RenderedText jp-OutputArea-output"
        else:
            classes = ""
        return HTML(
            textwrap.dedent(
                f"""
            <div class="{classes}">
            <pre>
            <a href="javascript:navigator.clipboard.writeText('{slice_text_no_cells.encode("unicode_escape").decode("utf-8")}')">Copy code</a>\
 | <a href="javascript:navigator.clipboard.writeText('{slice_text.encode("unicode_escape").decode("utf-8")}')">Copy cells</a>
     
            {{code}}
            </pre>
            </div>
            """
            ).format(code="\n\n".join(slice_text_linked_cells))
        )

    def __str__(self) -> str:
        return self._get_slice_text_from_slice()

    def __repr__(self):
        return repr(self._get_slice_text_from_slice())

    def _repr_mimebundle_(self, **kwargs) -> Dict[str, Any]:
        if self.format_type is str:
            return {"text/plain": self._get_slice_text_from_slice()}
        elif self.format_type is HTML:
            return self._make_slice_widget()._repr_mimebundle_(**kwargs)
        else:
            raise ValueError(f"Unknown format type {self.format_type}")

    def to_function(self):
        func_name = f"{self.FUNC_PREFIX}{self._func_counter}"
        code_lines = str(self).splitlines(keepends=True)
        try:
            last_stmt = ast.parse(code_lines[-1])
            if isinstance(last_stmt.body[-1], ast.Expr):
                code_lines[-1] = "return " + code_lines[-1]
        except SyntaxError:
            pass
        text = "".join(code_lines)
        self.__class__._func_counter += 1
        live_refs, _ = compute_live_dead_symbol_refs(text, scope=flow().global_scope)
        arg_set_raw = {ref.ref.chain[0].value for ref in live_refs}
        arg_set = {arg for arg in arg_set_raw if isinstance(arg, str)}
        for arg in list(arg_set):
            if hasattr(builtins, arg) or arg in sys.modules:
                arg_set.discard(arg)
        args = list(arg_set)
        prepend_lines = ["import sys\n"]
        for arg in args:
            prepend_lines.append(
                "if {arg} is None: {arg} = sys._getframe().f_back.f_back.f_globals['{arg}']\n".format(
                    arg=arg
                )
            )
        text = "".join(prepend_lines + text.splitlines(keepends=True))
        # TODO: annotate the function with explicit deps?
        return tracer().make_tracing_disabled_func(
            pyc.exec(
                f"def {func_name}({', '.join(f'{arg}=None' for arg in args)}):\n{textwrap.indent(text, '    ')}",
                global_env=shell().user_ns,
            )[func_name]
        )


class SliceableMixin(Protocol):
    """
    Common slicing functionality shared between CodeCell and Statement
    """

    #############
    # subclasses must implement the following:

    dynamic_parents: Dict[IdType, Set["Symbol"]]
    dynamic_children: Dict[IdType, Set["Symbol"]]
    static_parents: Dict[IdType, Set["Symbol"]]
    static_children: Dict[IdType, Set["Symbol"]]

    dangling_dynamic_parents: Dict[IdType, Set["Symbol"]]
    dangling_dynamic_children: Dict[IdType, Set["Symbol"]]
    dangling_static_parents: Dict[IdType, Set["Symbol"]]
    dangling_static_children: Dict[IdType, Set["Symbol"]]

    @classmethod
    def at_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "SliceableMixin":
        ...

    @classmethod
    def from_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "SliceableMixin":
        return cls.at_timestamp(ts, stmt_num=stmt_num)

    @classmethod
    def from_id(cls, sid: IdType) -> "SliceableMixin":
        ...

    @classmethod
    def from_id_nullable(cls, sid: IdType) -> Optional["SliceableMixin"]:
        ...

    @property
    def timestamp(self) -> Timestamp:
        ...

    @property
    def id(self) -> Union[str, int]:
        ...

    @property
    def prev(self) -> Optional["SliceableMixin"]:
        ...

    @property
    def text(self) -> str:
        ...

    @property
    def is_current(self) -> bool:
        return True

    # end abstract section
    #############

    @classmethod
    def _from_ref(cls, parent_ref: SliceRefType) -> "SliceableMixin":
        if isinstance(parent_ref, Timestamp):
            return cls.at_timestamp(parent_ref)
        elif isinstance(parent_ref, (int, str)):
            return cls.from_id(parent_ref)
        else:
            return parent_ref

    def add_parent_edges(self, parent_ref: SliceRefType, syms: Set["Symbol"]) -> None:
        if not syms:
            return
        parent = self._from_ref(parent_ref)
        pid = parent.id
        if pid in self.children:
            return
        if pid == self.id:
            # in this case, inherit the previous parents, if any
            if self.prev is not None:
                for _ in iter_dangling_contexts():
                    for prev_pid, prev_syms in self.prev.parents.items():
                        common = syms & prev_syms
                        if common:
                            self.parents.setdefault(prev_pid, set()).update(common)
            return
        with dangling_context(not parent.is_current):
            self.parents.setdefault(pid, set()).update(syms)
            parent.children.setdefault(self.id, set()).update(syms)

    def add_parent_edge(self, parent_ref: SliceRefType, sym: "Symbol") -> None:
        self.add_parent_edges(parent_ref, {sym})

    def remove_parent_edges(
        self, parent_ref: SliceRefType, syms: Set["Symbol"]
    ) -> None:
        if not syms:
            return
        parent = self._from_ref(parent_ref)
        pid = parent.id
        with dangling_context(not parent.is_current):
            for edges, eid in ((self.parents, pid), (parent.children, self.id)):
                sym_edges = edges.get(eid, set())
                if not sym_edges:
                    continue
                sym_edges.difference_update(syms)
                if not sym_edges:
                    del edges[eid]

    def remove_parent_edge(self, parent_ref: SliceRefType, sym: "Symbol") -> None:
        self.remove_parent_edges(parent_ref, {sym})

    def replace_parent_edges(
        self, prev_parent_ref: SliceRefType, new_parent_ref: SliceRefType
    ) -> None:
        prev_parent = self._from_ref(prev_parent_ref)
        new_parent = self._from_ref(new_parent_ref)
        with dangling_context(not prev_parent.is_current):
            syms = self.parents.pop(prev_parent.id)
            prev_parent.children.pop(self.id)
        with dangling_context(not new_parent.is_current):
            self.parents.setdefault(new_parent.id, set()).update(syms)
            new_parent.children.setdefault(self.id, set()).update(syms)

    def replace_child_edges(
        self, prev_child_ref: SliceRefType, new_child_ref: SliceRefType
    ) -> None:
        prev_child = self._from_ref(prev_child_ref)
        new_child = self._from_ref(new_child_ref)
        with dangling_context(not prev_child.is_current):
            syms = self.children.pop(prev_child.id)
            prev_child.parents.pop(self.id)
        with dangling_context(not new_child.is_current):
            self.children.setdefault(new_child.id, set()).update(syms)
            new_child.parents.setdefault(self.id, set()).update(syms)

    @property
    def parents(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        dangling_ctx = dangling_ctx_var.get()
        if ctx == SlicingContext.DYNAMIC:
            return (
                self.dangling_dynamic_parents if dangling_ctx else self.dynamic_parents
            )
        elif ctx == SlicingContext.STATIC:
            return self.dangling_static_parents if dangling_ctx else self.static_parents
        flow_ = flow()
        # TODO: rather than asserting test context,
        #  assert that we're being called from the notebook
        assert not flow_.is_test
        settings = flow_.mut_settings
        parents: Dict[IdType, Set["Symbol"]] = {}
        for _ in settings.iter_slicing_contexts():
            for pid, syms in self.parents.items():
                parents.setdefault(pid, set()).update(syms)
        return parents

    @parents.setter
    def parents(self, new_parents: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        dangling_ctx = dangling_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            if dangling_ctx:
                self.dangling_dynamic_parents = new_parents
            else:
                self.dynamic_parents = new_parents
        elif ctx == SlicingContext.STATIC:
            if dangling_ctx:
                self.dangling_static_parents = new_parents
            else:
                self.static_parents = new_parents
        else:
            assert False

    @property
    def children(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        dangling_ctx = dangling_ctx_var.get()
        if ctx == SlicingContext.DYNAMIC:
            return (
                self.dangling_dynamic_children
                if dangling_ctx
                else self.dynamic_children
            )
        elif ctx == SlicingContext.STATIC:
            return (
                self.dangling_static_children if dangling_ctx else self.static_children
            )
        flow_ = flow()
        # TODO: rather than asserting test context,
        #  assert that we're being called from the notebook
        assert not flow_.is_test
        settings = flow_.mut_settings
        children: Dict[IdType, Set["Symbol"]] = {}
        for _ in settings.iter_slicing_contexts():
            for pid, syms in self.children.items():
                children.setdefault(pid, set()).update(syms)
        return children

    @children.setter
    def children(self, new_children: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        dangling_ctx = dangling_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            if dangling_ctx:
                self.dangling_dynamic_children = new_children
            else:
                self.dynamic_children = new_children
        elif ctx == SlicingContext.STATIC:
            if dangling_ctx:
                self.dangling_static_children = new_children
            else:
                self.static_children = new_children
        else:
            assert False

    def _make_slice_helper(self, closure: Set["SliceableMixin"]) -> None:
        if self in closure:
            return
        closure.add(self)
        for _ in flow().mut_settings.iter_slicing_contexts():
            for pid in self.parents.keys():
                parent = self.from_id(pid)
                while parent.timestamp > self.timestamp:
                    parent = parent.prev
                parent._make_slice_helper(closure)

    def make_slice(self) -> List["SliceableMixin"]:
        return self.make_multi_slice([self])

    @classmethod
    def make_multi_slice(
        cls,
        seeds: Iterable[Union[TimestampOrCounter, "SliceableMixin"]],
        seed_only: bool = False,
    ) -> List["SliceableMixin"]:
        closure: Set["SliceableMixin"] = set()
        for seed in seeds:
            slice_seed = (
                cls.at_timestamp(seed) if isinstance(seed, (Timestamp, int)) else seed
            )
            if seed_only:
                closure.add(slice_seed)
            else:
                slice_seed._make_slice_helper(closure)
        return sorted(closure, key=lambda dep: dep.timestamp)

    @staticmethod
    def make_cell_dict_from_closure(
        closure: Sequence["SliceableMixin"],
    ) -> Dict[int, str]:
        slice_text_by_cell_num: Dict[int, List[str]] = {}
        for sliceable in closure:
            slice_text_by_cell_num.setdefault(sliceable.timestamp.cell_num, []).append(
                sliceable.text
            )
        return {
            cell_num: "\n".join(text)
            for cell_num, text in slice_text_by_cell_num.items()
        }

    @classmethod
    def make_cell_dict_multi_slice(
        cls,
        seeds: Iterable[Union[TimestampOrCounter, "SliceableMixin"]],
        seed_only: bool = False,
    ) -> Dict[int, str]:
        return cls.make_cell_dict_from_closure(
            cls.make_multi_slice(seeds, seed_only=seed_only)
        )

    def make_cell_dict_slice(self) -> Dict[int, str]:
        return self.make_cell_dict_multi_slice([self])

    @classmethod
    def format_multi_slice(
        cls,
        seeds: Iterable[Union[TimestampOrCounter, "SliceableMixin"]],
        blacken: bool = True,
        seed_only: bool = False,
        format_type: Optional[Type[FormatType]] = None,
    ) -> Slice:
        return format_slice(
            cls.make_cell_dict_multi_slice(seeds, seed_only=seed_only),
            blacken=blacken,
            format_type=format_type,
        )

    def format_slice(
        self,
        blacken: bool = True,
        seed_only: bool = False,
        format_type: Optional[Type[FormatType]] = None,
    ) -> Slice:
        return self.format_multi_slice(
            [self],
            blacken=blacken,
            seed_only=seed_only,
            format_type=format_type,
        )


def format_slice(
    raw_slice: Dict[int, str],
    blacken: bool = True,
    format_type: Optional[Type[FormatType]] = None,
) -> Slice:
    raw_slice = dict(raw_slice)
    if blacken:
        for cell_num, content in list(raw_slice.items()):
            try:
                raw_slice[cell_num] = black.format_str(
                    content, mode=black.FileMode()
                ).strip()
            except Exception as e:
                logger.info("call to black failed with exception: %s", e)
    return Slice(raw_slice, blacken=blacken, format_type=format_type)
