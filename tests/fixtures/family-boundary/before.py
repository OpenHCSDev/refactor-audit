class CompactionProgress(PiEvent):
    chunk_index: int | None = field(default=None, metadata={"wire_name": "chunkIndex"})
    source_bytes_done: int | None = field(default=None, metadata={"wire_name": "sourceBytesDone"})
    source_bytes_total: int | None = field(default=None, metadata={"wire_name": "sourceBytesTotal"})
    summary_phase: str | None = field(default=None, metadata={"wire_name": "summaryPhase"})
    usage: PiUsage | None = field(default=None, metadata={"wire_name": "usage"})

    async def apply(self, session: TurnSession) -> AsyncIterator[events.AgentEvent]:
        session.watchdog.progress()
        if self.usage is not None:
            session.usage.response_index += 1
            session.usage.compaction_recorded = True
            yield events.ProviderUsage(
                response_id=str(session.usage.response_index), usage=self.usage
            )
        measured = (
            self.source_bytes_done is not None
            and self.source_bytes_total is not None
            and 0 <= self.source_bytes_done <= self.source_bytes_total
            and self.source_bytes_total > 0
        )
        if self.chunk_index is not None and (
            self.chunk_index > 0 or (self.chunk_index == 0 and measured)
        ):
            yield events.CompactionProgress(
                chunk_index=self.chunk_index,
                source_bytes_done=self.source_bytes_done if measured else None,
                source_bytes_total=self.source_bytes_total if measured else None,
                summary_phase=self.summary_phase if self.summary_phase else None,
            )
