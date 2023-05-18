# -*- coding: utf-8 -*-
import logging
import sys
import textwrap
from typing import (
    TYPE_CHECKING,
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
from ipywidgets import HTML

from ipyflow.config import Interface
from ipyflow.data_model.timestamp import Timestamp
from ipyflow.models import cells
from ipyflow.singletons import flow
from ipyflow.slicing.context import SlicingContext, slicing_ctx_var
from ipyflow.types import IdType, TimestampOrCounter

if sys.version_info >= (3, 8):
    from typing import Protocol
else:
    Protocol = object

if TYPE_CHECKING:
    from ipyflow.data_model.symbol import Symbol


FormatType = TypeVar("FormatType", HTML, str)
SliceRefType = Union["SlicingMixin", IdType, Timestamp]


logger = logging.getLogger(__name__)
logger.setLevel(logging.WARNING)


class SlicingMixin(Protocol):
    """
    Common slicing functionality shared between CodeCell and Statement
    """

    #############
    # subclasses must implement the following:

    _dynamic_parents: Dict[IdType, Set["Symbol"]]
    _dynamic_children: Dict[IdType, Set["Symbol"]]
    _static_parents: Dict[IdType, Set["Symbol"]]
    _static_children: Dict[IdType, Set["Symbol"]]

    @classmethod
    def at_timestamp(
        cls, ts: TimestampOrCounter, stmt_num: Optional[int] = None
    ) -> "SlicingMixin":
        ...

    @classmethod
    def from_id(cls, sid: IdType) -> "SlicingMixin":
        ...

    @classmethod
    def from_id_nullable(cls, sid: IdType) -> Optional["SlicingMixin"]:
        ...

    @property
    def timestamp(self) -> Timestamp:
        ...

    @property
    def id(self) -> Union[str, int]:
        ...

    @property
    def prev(self) -> Optional["SlicingMixin"]:
        ...

    @property
    def text(self) -> str:
        ...

    # end abstract section
    #############

    @classmethod
    def _from_ref(cls, parent_ref: SliceRefType) -> "SlicingMixin":
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
                for prev_pid, prev_syms in self.prev.parents.items():
                    common = syms & prev_syms
                    if common:
                        self.parents.setdefault(prev_pid, set()).update(common)
            return
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
        syms = self.parents.pop(prev_parent.id)
        prev_parent.children.pop(self.id)
        self.parents.setdefault(new_parent.id, set()).update(syms)
        new_parent.children.setdefault(self.id, set()).update(syms)

    def replace_child_edges(
        self, prev_child_ref: SliceRefType, new_child_ref: SliceRefType
    ) -> None:
        prev_child = self._from_ref(prev_child_ref)
        new_child = self._from_ref(new_child_ref)
        syms = self.children.pop(prev_child.id)
        prev_child.parents.pop(self.id)
        self.children.setdefault(new_child.id, set()).update(syms)
        new_child.parents.setdefault(self.id, set()).update(syms)

    @property
    def parents(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            return self._dynamic_parents
        elif ctx == SlicingContext.STATIC:
            return self._static_parents
        else:
            assert False

    @parents.setter
    def parents(self, new_parents: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            self._dynamic_parents = new_parents
        elif ctx == SlicingContext.STATIC:
            self._static_parents = new_parents
        else:
            assert False

    @property
    def children(self) -> Dict[IdType, Set["Symbol"]]:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            return self._dynamic_children
        elif ctx == SlicingContext.STATIC:
            return self._static_children
        else:
            assert False

    @children.setter
    def children(self, new_children: Dict[IdType, Set["Symbol"]]) -> None:
        ctx = slicing_ctx_var.get()
        assert ctx is not None
        if ctx == SlicingContext.DYNAMIC:
            self._dynamic_children = new_children
        elif ctx == SlicingContext.STATIC:
            self._static_children = new_children
        else:
            assert False

    def _make_slice_helper(self, closure: Set["SlicingMixin"]) -> None:
        if self in closure:
            return
        closure.add(self)
        for _ in flow().mut_settings.iter_slicing_contexts():
            for pid in self.parents.keys():
                self.from_id(pid)._make_slice_helper(closure)

    def make_slice(self) -> List["SlicingMixin"]:
        return self.make_multi_slice([self])

    @classmethod
    def make_multi_slice(
        cls, seeds: Iterable[Union[TimestampOrCounter, "SlicingMixin"]]
    ) -> List["SlicingMixin"]:
        closure: Set["SlicingMixin"] = set()
        for seed in seeds:
            slice_seed = (
                cls.at_timestamp(seed) if isinstance(seed, (Timestamp, int)) else seed
            )
            slice_seed._make_slice_helper(closure)
        return sorted(closure, key=lambda dep: dep.timestamp)

    @staticmethod
    def make_cell_dict_from_closure(
        closure: Sequence["SlicingMixin"],
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
        cls, seeds: Iterable[Union[TimestampOrCounter, "SlicingMixin"]]
    ) -> Dict[int, str]:
        return cls.make_cell_dict_from_closure(cls.make_multi_slice(seeds))

    def make_cell_dict_slice(self) -> Dict[int, str]:
        return self.make_cell_dict_multi_slice([self])

    @classmethod
    def format_multi_slice(
        cls,
        seeds: Iterable[Union[TimestampOrCounter, "SlicingMixin"]],
        blacken: bool = True,
        format_type: Optional[Type[FormatType]] = None,
    ) -> FormatType:
        return format_slice(
            cls.make_cell_dict_multi_slice(seeds),
            blacken=blacken,
            format_type=format_type,
        )

    def format_slice(
        self,
        blacken: bool = True,
        format_type: Optional[Type[FormatType]] = None,
    ) -> FormatType:
        return self.format_multi_slice(
            [self],
            blacken=blacken,
            format_type=format_type,
        )


def format_slice(
    slice: Dict[int, str],
    blacken: bool = True,
    format_type: Optional[Type[FormatType]] = None,
) -> FormatType:
    iface = flow().mut_settings.interface
    if format_type is None:
        if iface in (Interface.IPYTHON, Interface.UNKNOWN):
            format_type = str
        else:
            format_type = HTML
    assert format_type is not None
    if blacken:
        for cell_num, content in list(slice.items()):
            try:
                slice[cell_num] = black.format_str(
                    content, mode=black.FileMode()
                ).strip()
            except Exception as e:
                logger.info("call to black failed with exception: %s", e)
    slice_text_cells = "\n\n".join(
        f"# Cell {cell_num}\n" + content for cell_num, content in sorted(slice.items())
    )
    if format_type is str:
        return slice_text_cells
    slice_text_linked_cells = []
    if iface == Interface.JUPYTER:
        container_selector = "javascript:document.getElementById('notebook-container')"
    elif iface == Interface.JUPYTERLAB:
        container_selector = (
            "javascript:document.getElementById("
            "document.querySelector('.jp-mod-current').dataset.id).children[2]"
        )
    else:
        container_selector = None
    for cell_num, content in sorted(slice.items()):
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
    assert format_type is HTML
    slice_text_no_cells = "\n".join(
        content for _cell_num, content in sorted(slice.items())
    )
    if blacken:
        slice_text_no_cells = black.format_str(
            slice_text_no_cells, mode=black.FileMode()
        ).strip()
    if iface == Interface.JUPYTER:
        classes = "output_subarea output_text output_stream output_stdout"
    elif iface == Interface.JUPYTERLAB:
        classes = "lm-Widget p-Widget jp-RenderedText jp-OutputArea-output"
    else:
        classes = ""
    return HTML(
        textwrap.dedent(
            f"""
        <div class="{classes}">
        <pre>
        <a href="javascript:navigator.clipboard.writeText('{slice_text_no_cells.encode("unicode_escape").decode("utf-8")}')">Copy code</a>\
 | <a href="javascript:navigator.clipboard.writeText('{slice_text_cells.encode("unicode_escape").decode("utf-8")}')">Copy cells</a>
 
        {{code}}
        </pre>
        </div>
        """
        ).format(code="\n\n".join(slice_text_linked_cells))
    )
