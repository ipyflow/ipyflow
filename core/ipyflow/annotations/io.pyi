# -*- coding: utf-8 -*-
from ipyflow.annotations import (
    AnyOf,
    FileSystem,
    Mutate,
    Parents,
    UpsertSymbol,
    handler_for,
    self,
)

# fake symbols to reduce lint errors
file = None

def open(file, *_, **__) -> UpsertSymbol[FileSystem[file]]: ...

class IOBase:
    """"""  # just to ensure space isn't removed by autoformatting

    @handler_for("flush", "truncate", "write", "writelines")
    def writer_method(
        self: AnyOf[FileSystem[file], Parents[FileSystem[file], ...]],
    ) -> Mutate[FileSystem[file], self]: ...

    """"""

    @handler_for("close", "readline", "readlines", "seek")
    def reader_method(self) -> Mutate[self]: ...

    """"""

    def __enter__(self) -> UpsertSymbol[Parents[self]]: ...
