class CodingCall(NativeToolCall):
    tool: NativeExecutableTool

    @property
    def name(self) -> str:
        return self.tool.declared_name

    def commit_terminal(
        self, is_error: bool, directory: Path, input_id: str, round_number: int
    ) -> None:
        slot = self.slot(input_id, round_number)
        record_selected_terminal(directory, slot, slot)

    def admission_failed(self) -> None:
        # A denied ordinary tool may report an error and let Pi explain it. The
        # consumed call cannot be retried; this grants no mutation authority.
        pass

    @classmethod
    def decode(cls, call_id: object, name: object, arguments: object) -> CodingCall:
        if (
            type(call_id) is not str
            or not call_id
            or len(call_id) > 256
            or type(name) is not str
            or type(arguments) is not dict
        ):
            raise SelectedToolDenied("Malformed native coding call")
        try:
            return NativeExecutableTool.from_call(name, arguments, call_id=call_id)
        except (ValueError, TypeError, KeyError) as error:
            raise SelectedToolDenied(f"Invalid native coding call: {error}") from error

    def slot(self, input_id: str, round_number: int) -> str:
        identity = f"{input_id}\0{round_number}\0{self.call_id}"
        return hashlib.sha256(identity.encode()).hexdigest()[:32]
