from strands import tool

_MAX_READ_CHARACTERS = 12_000
_MAX_SEARCH_MATCHES = 10
_SEARCH_CONTEXT_CHARACTERS = 240
_MAX_TOOL_CALLS = 12


class DocumentTextTools:
    """Capability-limited access to temporary native PDF text."""

    def __init__(self, page_text: tuple[str | None, ...]) -> None:
        if not page_text:
            raise ValueError("At least one page is required")
        self.page_text = page_text
        self._tool_calls = 0

    def _count_call(self) -> None:
        self._tool_calls += 1
        if self._tool_calls > _MAX_TOOL_CALLS:
            raise ValueError("Document text tool-call limit reached")

    @tool
    def read_document_text(self, page_number: int) -> dict[str, object]:
        """Read bounded native PDF text for one 1-based page.

        This returns no OCR or external content. It reports unavailable for images and scanned
        pages.
        """
        self._count_call()
        if page_number < 1 or page_number > len(self.page_text):
            raise ValueError("Page number is outside this document")
        text = self.page_text[page_number - 1]
        if not text:
            return {"page_number": page_number, "available": False, "text": None}
        return {
            "page_number": page_number,
            "available": True,
            "text": text[:_MAX_READ_CHARACTERS],
            "truncated": len(text) > _MAX_READ_CHARACTERS,
        }

    @tool
    def search_document_text(self, query: str) -> dict[str, object]:
        """Search temporary native PDF text and return bounded page snippets."""
        self._count_call()
        normalized_query = query.strip()
        if len(normalized_query) < 2 or len(normalized_query) > 200:
            raise ValueError("Search query must contain 2 to 200 characters")

        matches: list[dict[str, object]] = []
        needle = normalized_query.casefold()
        for page_number, text in enumerate(self.page_text, start=1):
            if not text:
                continue
            folded = text.casefold()
            start = 0
            while len(matches) < _MAX_SEARCH_MATCHES:
                index = folded.find(needle, start)
                if index < 0:
                    break
                left = max(0, index - _SEARCH_CONTEXT_CHARACTERS)
                right = min(
                    len(text), index + len(normalized_query) + _SEARCH_CONTEXT_CHARACTERS
                )
                matches.append(
                    {
                        "page_number": page_number,
                        "start_character": index,
                        "snippet": text[left:right],
                    }
                )
                start = index + len(normalized_query)
            if len(matches) >= _MAX_SEARCH_MATCHES:
                break
        return {
            "query": normalized_query,
            "matches": matches,
            "truncated": len(matches) == _MAX_SEARCH_MATCHES,
        }
