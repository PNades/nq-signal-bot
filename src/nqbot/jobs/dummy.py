"""A tiny job to exercise the runner: N chunks, each made of a few short work units and one small file."""

from __future__ import annotations

from nqbot.jobs.runner import Ctx, StopChunk
from nqbot.storage import atomic_write_text


class DummyJob:
    name = "dummy"

    def __init__(self, n_chunks: int = 5, sleep_sec: float = 0.2, units: int = 2):
        self.n_chunks = n_chunks
        self.sleep_sec = sleep_sec
        self.units = units

    def chunks(self, ctx: Ctx) -> list[str]:
        return [f"chunk-{i:03d}" for i in range(self.n_chunks)]

    def run_chunk(self, chunk_id: str, ctx: Ctx) -> None:
        for _ in range(self.units):  # the pattern every long job uses between its own work units
            reason = ctx.should_stop()
            if reason:
                raise StopChunk(reason)
            ctx.sleep(self.sleep_sec / self.units)
        atomic_write_text(ctx.out_dir / "output" / f"{chunk_id}.txt", f"{chunk_id} low_memory={ctx.low_memory}\n")
