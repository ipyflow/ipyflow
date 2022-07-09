# -*- coding: utf-8 -*-
from ipyflow.annotations import (
    AnyOf,
    FileSystem,
    Mutated,
    Parents,
    SymbolCreateOrLookup,
    SymbolUpserted,
    handler_for,
)

def open(file, *_, **__) -> SymbolCreateOrLookup[FileSystem[file]]: ...

class IOBase:

    """"""  # just to ensure space isn't removed by autoformatting

    @handler_for("flush", "truncate", "write", "writelines")
    def writer_method(
        self: AnyOf[FileSystem[file], Parents[FileSystem[file], ...]]
    ) -> Mutated[FileSystem[file], self]: ...

    """"""

    @handler_for("close", "readline", "readlines", "seek")
    def reader_method(self) -> Mutated[self]: ...
