SEND_TOOL = 'comms_send'

class SendOutputItem(Item):
    wire_tag = 'functionCallOutput'
    name: str
    output: str

    def events(self) -> tuple[TranscriptEvent, ...]:
        # The bridge's send result is already rendered from the bus in Toad.
        if self.name == SEND_TOOL:
            return ()
        return (ToolEndTranscript(tool_call_id=self.id, tool_name=self.name, text=self.output),)
