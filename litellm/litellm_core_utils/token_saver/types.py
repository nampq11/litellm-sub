from typing import NamedTuple


class FilterHit(NamedTuple):
    shape: str
    filter_name: str
    saved_bytes: int


class TokenSaverStats(NamedTuple):
    bytes_before: int
    bytes_after: int
    hits: tuple[FilterHit, ...]

    @property
    def bytes_saved(self) -> int:
        return self.bytes_before - self.bytes_after

    @property
    def percent_saved(self) -> float:
        if self.bytes_before <= 0:
            return 0.0
        return (self.bytes_saved / self.bytes_before) * 100.0
