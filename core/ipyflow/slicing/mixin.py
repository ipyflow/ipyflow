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

from ipyflow.analysis.live_refs import compute_live_dead_symbol_refs
from ipyflow.config import Interface
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import cells
from ipyflow.singletons import flow, shell, tracer
from ipyflow.slicing.context import SlicingContext, slicing_ctx_var
from ipyflow.types import IdType, TimestampOrCounter

try:
    from ipywidgets import HTML
except Exception:
    HTML = str

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
    FUNC_PREFIX = f"{pyc.PYCCOLO_BUILTIN_PREFIX}_ipyflow_slice_func_"
    _func_counter = 0

    def __init__(
        self,
        raw_slice: Dict[int, str],
        blacken: bool,
        format_type: Optional[Type[FormatType]] = None,
        include_cell_headers: bool = True,
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
        self.include_cell_headers = include_cell_headers

    def _get_slice_text_from_slice(self) -> str:
        sep = "\n\n" if self.include_cell_headers else "\n"
        return sep.join(
            f"# Cell {cell_num}\n" + content if self.include_cell_headers else content
            for cell_num, content in sorted(self.raw_slice.items())
        ).strip()

    def _make_slice_widget(self) -> HTML:
        if HTML is str:
            raise ValueError("ipywidgets not available")
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
                """
            <div class="{classes}">
            <pre>
            <a href="javascript:navigator.clipboard.writeText('{copy_code}')">Copy code</a>\
 | <a href="javascript:navigator.clipboard.writeText('{copy_cells}')">Copy cells</a>
     
            {code}
            </pre>
            </div>
            """
            ).format(
                classes=classes,
                copy_code=slice_text_no_cells.encode("unicode_escape").decode("utf-8"),
                copy_cells=slice_text.encode("unicode_escape").decode("utf-8"),
                code="\n\n".join(slice_text_linked_cells),
            )
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
        live_refs, *_ = compute_live_dead_symbol_refs(text, scope=flow().global_scope)
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

    raw_dynamic_parents: Dict[IdType, Set["Symbol"]]
    raw_dynamic_children: Dict[IdType, Set["Symbol"]]
    raw_static_parents: Dict[IdType, Set["Symbol"]]
    raw_static_children: Dict[IdType, Set["Symbol"]]

    @property
    def dynamic_parents(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {
            self.from_id(pid): syms for pid, syms in self.raw_dynamic_parents.items()
        }

    @property
    def dynamic_children(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {
            self.from_id(cid): syms for cid, syms in self.raw_dynamic_children.items()
        }

    @property
    def static_parents(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {
            self.from_id(pid): syms for pid, syms in self.raw_static_parents.items()
        }

    @property
    def static_children(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {
            self.from_id(cid): syms for cid, syms in self.raw_static_children.items()
        }

    @property
    def parents(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {self.from_id(pid): syms for pid, syms in self.raw_parents.items()}

    @property
    def children(self) -> Dict["SliceableMixin", Set["Symbol"]]:
        return {self.from_id(cid): syms for cid, syms in self.raw_children.items()}

    @classmethod
    def current(cls) -> "SliceableMixin":
        return NotImplemented

    @classmethod
    def at_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "SliceableMixin":
        return NotImplemented

    @classmethod
    def from_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "SliceableMixin":
        return cls.at_timestamp(ts, stmt_num=stmt_num)

    @classmethod
    def from_id(cls, sid: IdType) -> "SliceableMixin":
        return NotImplemented

    @classmethod
    def from_id_nullable(cls, sid: IdType) -> Optional["SliceableMixin"]: ...

    @property
    def timestamp(self) -> Timestamp:
        return NotImplemented

    @property
    def id(self) -> Union[str, int]:
        return NotImplemented

    @property
    def prev(self) -> Optional["SliceableMixin"]:
        return NotImplemented

    @property
    def text(self) -> str:
        return NotImplemented

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
        if pid in self.raw_children:
            return
        if pid == self.id:
            # in this case, inherit the previous parents, if any
            if self.prev is not None:
                for prev_pid, prev_syms in self.prev.raw_parents.items():
                    common = syms & prev_syms
                    if common:
                        self.raw_parents.setdefault(prev_pid, set()).update(common)
            return
        self.raw_parents.setdefault(pid, set()).update(syms)
        parent.raw_children.setdefault(self.id, set()).update(syms)

    def add_parent_edge(self, parent_ref: SliceRefType, sym: "Symbol") -> None:
        self.add_parent_edges(parent_ref, {sym})

    def remove_parent_edges(
        self, parent_ref: SliceRefType, syms: Set["Symbol"]
    ) -> None:
        if not syms:
            return
        parent = self._from_ref(parent_ref)
        pid = parent.id
        for edges, eid in ((self.raw_parents, pid), (parent.raw_children, self.id)):
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
        syms = self.raw_parents.pop(prev_parent.id)
        prev_parent.raw_children.pop(self.id)
        self.raw_parents.setdefault(new_parent.id, set()).update(syms)
        new_parent.raw_children.setdefault(self.id, set()).update(syms)

    def replace_child_edges(
        self, prev_child_ref: SliceRefType, new_child_ref: SliceRefType
    ) -> None:
        prev_child = self._from_ref(prev_child_ref)
        new_child = self._from_ref(new_child_ref)
        syms = self.raw_children.pop(prev_child.id)
        prev_child.raw_parents.pop(self.id)
        self.raw_children.setdefault(new_child.id, set()).update(syms)
        new_child.raw_parents.setdefault(self.id, set()).update(syms)

    @property
    def raw_parents(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        if ctx == SlicingContext.DYNAMIC:
            return self.raw_dynamic_parents
        elif ctx == SlicingContext.STATIC:
            return self.raw_static_parents
        flow_ = flow()
        # TODO: rather than asserting test context,
        #  assert that we're being called from the notebook
        assert not flow_.is_test
        settings = flow_.mut_settings
        parents: Dict[IdType, Set["Symbol"]] = {}
        for _ in settings.iter_slicing_contexts():
            for pid, syms in getattr(
                self, "directional_parents", self.raw_parents
            ).items():
                parents.setdefault(pid, set()).update(syms)
        return parents

    @raw_parents.setter
    def raw_parents(self, new_parents: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            self.raw_dynamic_parents = new_parents
        elif ctx == SlicingContext.STATIC:
            self.raw_static_parents = new_parents
        else:
            assert False

    @property
    def raw_children(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        if ctx == SlicingContext.DYNAMIC:
            return self.raw_dynamic_children
        elif ctx == SlicingContext.STATIC:
            return self.raw_static_children
        flow_ = flow()
        # TODO: rather than asserting test context,
        #  assert that we're being called from the notebook
        assert not flow_.is_test
        settings = flow_.mut_settings
        children: Dict[IdType, Set["Symbol"]] = {}
        for _ in settings.iter_slicing_contexts():
            for pid, syms in getattr(
                self, "directional_children", self.raw_children
            ).items():
                children.setdefault(pid, set()).update(syms)
        return children

    @raw_children.setter
    def raw_children(self, new_children: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            self.raw_dynamic_children = new_children
        elif ctx == SlicingContext.STATIC:
            self.raw_static_children = new_children
        else:
            assert False

    def _make_slice_helper(self, closure: Set["SliceableMixin"]) -> None:
        if self in closure:
            return
        closure.add(self)
        for _ in flow().mut_settings.iter_slicing_contexts():
            for pid in self.raw_parents.keys():
                parent = self.from_id(pid)
                while parent.timestamp > self.timestamp:
                    if getattr(parent, "override", False):
                        break
                    parent = parent.prev  # type: ignore[assignment]
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

    @staticmethod
    def _process_memoized_seeds(
        seeds: Iterable[Union[TimestampOrCounter, "SliceableMixin"]],
    ) -> Set[TimestampOrCounter]:
        processed_seeds: Set[TimestampOrCounter] = set()
        for seed in seeds:
            if not isinstance(seed, (Timestamp, int)):
                seed = seed.timestamp  # type: ignore
            assert isinstance(seed, (Timestamp, int))
            mem_ctr = cells().at_timestamp(seed).skipped_due_to_memoization_ctr
            if mem_ctr == -1:
                processed_seeds.add(seed)
            else:
                if isinstance(seed, int):
                    processed_seeds.add(mem_ctr)
                else:
                    processed_seeds.add(Timestamp(mem_ctr, seed.stmt_num))
        return processed_seeds

    @classmethod
    def format_multi_slice(
        cls,
        seeds: Iterable[Union[TimestampOrCounter, "SliceableMixin"]],
        blacken: bool = True,
        seed_only: bool = False,
        format_type: Optional[Type[FormatType]] = None,
        include_cell_headers: bool = True,
    ) -> Slice:
        seeds = cls._process_memoized_seeds(seeds)
        return format_slice(
            cls.make_cell_dict_multi_slice(seeds, seed_only=seed_only),
            blacken=blacken,
            format_type=format_type,
            include_cell_headers=include_cell_headers,
        )

    def format_slice(
        self,
        blacken: bool = True,
        seed_only: bool = False,
        format_type: Optional[Type[FormatType]] = None,
        include_cell_headers: bool = True,
    ) -> Slice:
        return self.format_multi_slice(
            [self],
            blacken=blacken,
            seed_only=seed_only,
            format_type=format_type,
            include_cell_headers=include_cell_headers,
        )


def format_slice(
    raw_slice: Dict[int, str],
    blacken: bool = True,
    format_type: Optional[Type[FormatType]] = None,
    include_cell_headers: bool = True,
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
    return Slice(
        raw_slice,
        blacken=blacken,
        format_type=format_type,
        include_cell_headers=include_cell_headers,
    )
