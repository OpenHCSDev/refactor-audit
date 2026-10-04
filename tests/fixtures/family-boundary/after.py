class CompactionProgress(PiEvent):
    operation_id: str = field(metadata={"wire_name": "operationId"})
    chunk_index: int = field(metadata={"wire_name": "chunkIndex"})
    reason: type[CompactionReason] = field(default=UnknownCompactionReason)
    text: str = ""
    source: CompactionSourceProgress | None = None
    usage: PiUsage | None = field(default=None, metadata={"wire_name": "usage"})

    async def apply(self, session: TurnSession) -> AsyncIterator[events.AgentEvent]:
        session.watchdog.progress()
        if self.usage is not None:
            session.usage.response_index += 1
            session.usage.compaction_recorded = True
            yield events.ProviderUsage(
                response_id=str(session.usage.response_index), usage=self.usage
            )
        yield events.CompactionProgress(
            reason=self.reason.declared_name,
            operation_id=self.operation_id,
            text=self.text,
            chunk_index=self.chunk_index,
            source=self.source,
        )
