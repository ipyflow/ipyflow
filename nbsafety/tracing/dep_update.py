from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Set
    from ..data_symbol import DataSymbol


class NoUpdateYet(object):
    def update(self, dep_update: 'DependencyUpdate') -> 'DependencyUpdate':
        return dep_update


class DependencyUpdate(object):
    """Just records the metadata needed to update deps for a DataSymbol."""
    def __init__(
        self,
        deps: 'Set[DataSymbol]',
        overwrite: bool,
        mutate: bool = False
    ):
        self.deps = deps
        self.overwrite = overwrite
        self.mutate = mutate

    def update(self, dep_update: 'DependencyUpdate') -> 'DependencyUpdate':
        if dep_update.overwrite:
            dep_update.mutate = dep_update.mutate or self.mutate
            return dep_update
        else:
            self.mutate = self.mutate or dep_update.mutate
            self.deps |= dep_update.deps
            return self
