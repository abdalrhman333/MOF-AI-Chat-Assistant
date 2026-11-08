"""Pull the answer text out of a JSON response while it is still arriving.

The model does not return prose. It returns one strict-schema JSON object:

    {"language":"ar","intent":"question","answer":"...","used_passages":[2],
     "used_project_record":true}

Streaming that to a browser verbatim would show the user
`{"language":"ar","intent":"qu` — so something has to read the `answer`
field out of a document that is not finished yet. That is all this module
does.

**Why it works at all:** `RESPONSE_SCHEMA` lists `language` and `intent`
*before* `answer`, and a strict schema is generated in order. So by the
time the first character of the answer arrives, the classification is
already decided, and `used_passages` — which every citation is rebuilt
from — arrives after the text is complete. Text first, citations at the
end, which is also the right order for the reader.

⚠️ **This is a reader, never an authority.** Nothing here decides anything:
the complete raw JSON is still parsed by `parse_response` when the stream
ends, and that is what produces the citations, the language and the intent.
If this module and the final parse ever disagreed, the final parse wins,
because it is the one that validates. What streams is a preview of text the
model has already committed to.

The scanner is a small state machine rather than a regex over the buffer.
A regex would have to re-scan the whole document on every chunk, and would
be fooled by a `"answer"` appearing inside a string value — which the enum
fields cannot contain today, but which no one should have to re-verify
after a schema change.
"""
from __future__ import annotations

_HEX = "0123456789abcdefABCDEF"

_SIMPLE_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b",
    "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}


class AnswerTextStream:
    """Feed it response chunks; it returns newly decoded `answer` text.

    Usage:

        reader = AnswerTextStream()
        for chunk in deltas:
            new = reader.feed(chunk)
            if new:
                send(new)

    `feed` returns `""` for every chunk that adds nothing displayable —
    the fields before the answer, the fields after it, and a chunk that
    ends in the middle of a `\\uXXXX` escape. It never returns half an
    escape sequence, and it never raises on malformed input: a reader that
    threw would turn a cosmetic problem into a failed answer, when the
    authoritative parse is still to come.
    """

    def __init__(self, key: str = "answer") -> None:
        self._key = key
        self._in_string = False
        self._escape = False          # previous char was a backslash
        self._unicode: str | None = None   # hex digits collected so far
        self._current: list[str] = []      # the string being read
        self._pending_key: str | None = None
        self._last_string: str | None = None
        self._awaiting_colon = False
        self._in_answer = False
        self._done = False

    @property
    def finished(self) -> bool:
        """True once the answer string has been closed."""
        return self._done

    def feed(self, chunk: str) -> str:
        """Consume a chunk; return whatever new answer text it completed."""
        if self._done or not chunk:
            return ""
        out: list[str] = []
        for char in chunk:
            self._consume(char, out)
            if self._done:
                break
        return "".join(out)

    # -- internals ---------------------------------------------------------

    def _consume(self, char: str, out: list[str]) -> None:
        if self._in_string:
            self._consume_in_string(char, out)
            return

        if char == '"':
            self._in_string = True
            self._current = []
            # A string opening while we are waiting for this key's value is
            # the answer itself.
            self._in_answer = self._pending_key == self._key
            self._pending_key = None
            return

        if char == ":" and self._awaiting_colon:
            # The string that just closed was a key, not a value.
            self._pending_key = self._last_string
            self._awaiting_colon = False
            return

        if not char.isspace():
            # Anything else ends the chance that the last string was a key.
            self._awaiting_colon = False
            if char == ",":
                self._pending_key = None

    def _consume_in_string(self, char: str, out: list[str]) -> None:
        if self._unicode is not None:
            self._consume_unicode(char, out)
            return

        if self._escape:
            self._escape = False
            if char == "u":
                self._unicode = ""
                return
            decoded = _SIMPLE_ESCAPES.get(char, char)
            self._emit(decoded, out)
            return

        if char == "\\":
            self._escape = True
            return

        if char == '"':
            self._in_string = False
            self._last_string = "".join(self._current)
            self._awaiting_colon = True
            if self._in_answer:
                # The answer is complete. Everything after it — the
                # passage numbers the citations come from — is the final
                # parse's business, not ours.
                self._in_answer = False
                self._done = True
            return

        self._emit(char, out)

    def _consume_unicode(self, char: str, out: list[str]) -> None:
        """Collect \\uXXXX, emitting only once all four digits are in.

        A chunk boundary falls mid-escape often enough to matter: Arabic is
        not escaped by every provider, but `\\u200f` (right-to-left mark)
        and smart quotes are, and emitting `\\u06` would corrupt the text.
        """
        if char not in _HEX:
            # Malformed. Keep what was collected rather than raising.
            self._emit("\\u" + self._unicode + char, out)
            self._unicode = None
            return
        self._unicode += char
        if len(self._unicode) == 4:
            try:
                self._emit(chr(int(self._unicode, 16)), out)
            except ValueError:                              # pragma: no cover
                self._emit("\\u" + self._unicode, out)
            self._unicode = None

    def _emit(self, text: str, out: list[str]) -> None:
        self._current.append(text)
        if self._in_answer:
            out.append(text)


__all__ = ["AnswerTextStream"]
