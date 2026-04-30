import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from open_webui.utils.middleware import estimate_token_count, truncate_to_token_limit


class TestEstimateTokenCount:
    def test_empty_string_returns_zero(self):
        assert estimate_token_count("") == 0

    def test_none_returns_zero(self):
        assert estimate_token_count(None) == 0

    def test_single_word(self):
        count = estimate_token_count("hello")
        assert count >= 1

    def test_simple_sentence(self):
        count = estimate_token_count("The quick brown fox jumps over the lazy dog.")
        assert count > 0

    def test_longer_text_has_more_tokens(self):
        short = estimate_token_count("Hello")
        long = estimate_token_count(
            "Hello world, this is a much longer sentence with many more words in it."
        )
        assert long > short

    def test_whitespace_only(self):
        count = estimate_token_count("   ")
        # Whitespace still encodes to tokens
        assert count >= 1

    def test_special_characters(self):
        count = estimate_token_count("<source><source_id>test</source_id></source>")
        assert count > 0

    def test_xml_structured_context(self):
        context = (
            "<source><source_id>doc1</source_id>"
            "<source_context>This is some document content.</source_context></source>"
        )
        count = estimate_token_count(context)
        assert count > 0

    def test_default_encoding(self):
        count_default = estimate_token_count("test string")
        count_explicit = estimate_token_count("test string", "cl100k_base")
        assert count_default == count_explicit

    def test_large_text(self):
        large_text = "word " * 10000
        count = estimate_token_count(large_text)
        assert count > 1000


class TestTruncateToTokenLimit:
    def test_empty_string_returns_empty(self):
        assert truncate_to_token_limit("", 100) == ""

    def test_none_returns_empty(self):
        assert truncate_to_token_limit(None, 100) == ""

    def test_zero_max_tokens_returns_empty(self):
        assert truncate_to_token_limit("Hello world", 0) == ""

    def test_negative_max_tokens_returns_empty(self):
        assert truncate_to_token_limit("Hello world", -5) == ""

    def test_text_within_limit_unchanged(self):
        text = "Hello world"
        result = truncate_to_token_limit(text, 1000)
        assert result == text

    def test_text_exactly_at_limit(self):
        text = "Hello"
        token_count = estimate_token_count(text)
        result = truncate_to_token_limit(text, token_count)
        assert result == text

    def test_text_exceeding_limit_is_truncated(self):
        text = "The quick brown fox jumps over the lazy dog. " * 100
        max_tokens = 10
        result = truncate_to_token_limit(text, max_tokens)
        result_tokens = estimate_token_count(result)
        assert result_tokens <= max_tokens
        assert len(result) < len(text)

    def test_truncated_text_respects_token_boundary(self):
        text = "This is a test sentence with multiple words that should be truncated properly."
        max_tokens = 5
        result = truncate_to_token_limit(text, max_tokens)
        result_tokens = estimate_token_count(result)
        assert result_tokens <= max_tokens

    def test_single_token_limit(self):
        text = "Hello wonderful world of programming"
        result = truncate_to_token_limit(text, 1)
        result_tokens = estimate_token_count(result)
        assert result_tokens == 1

    def test_default_encoding(self):
        text = "test string " * 100
        result_default = truncate_to_token_limit(text, 10)
        result_explicit = truncate_to_token_limit(text, 10, "cl100k_base")
        assert result_default == result_explicit


class TestRAGContextTruncation:
    """
    Tests simulating the truncation logic from process_chat_payload
    when web search documents are too large for the context window.
    """

    def _build_source_context(self, num_sources: int, content_size: int = 500) -> str:
        """Helper to build a realistic RAG context string with multiple sources."""
        context_parts = []
        for i in range(num_sources):
            content = f"Document {i} content. " * (content_size // 20)
            context_parts.append(
                f"<source><source_id>web_search_{i}</source_id>"
                f"<source_context>{content}</source_context></source>"
            )
        return "\n".join(context_parts)

    def _simulate_truncation(
        self,
        context_string: str,
        max_context_tokens: int,
        encoding_name: str = "cl100k_base",
    ) -> str:
        """
        Simulate the truncation logic from process_chat_payload.
        This mirrors the exact logic in the middleware.
        """
        estimated_tokens = estimate_token_count(context_string, encoding_name)

        if estimated_tokens <= max_context_tokens:
            return context_string

        truncation_notice = f"\n[RAG context truncated from {estimated_tokens} to ~{max_context_tokens} tokens]"
        notice_tokens = estimate_token_count(truncation_notice, encoding_name)
        target_tokens = max_context_tokens - notice_tokens

        truncated_context = truncate_to_token_limit(
            context_string, target_tokens, encoding_name
        )

        # Try to truncate at the last complete </source> tag for clean XML
        last_source_end = truncated_context.rfind("</source>")
        if last_source_end > 0:
            truncated_context = truncated_context[: last_source_end + len("</source>")]

        context_string = truncated_context + truncation_notice
        return context_string

    def test_small_context_not_truncated(self):
        """Context within token limit should not be truncated."""
        context = self._build_source_context(2, content_size=50)
        max_tokens = 10000
        result = self._simulate_truncation(context, max_tokens)
        assert "[RAG context truncated" not in result
        assert result == context

    def test_large_web_search_context_is_truncated(self):
        """Large web search results exceeding context window should be truncated."""
        # Build a very large context simulating many web search results
        context = self._build_source_context(20, content_size=2000)
        max_tokens = 500  # Small limit to force truncation

        result = self._simulate_truncation(context, max_tokens)

        assert "[RAG context truncated" in result
        # The result (minus the notice) should be within token limits
        result_tokens = estimate_token_count(result)
        # Allow some slack for the truncation notice itself
        assert result_tokens <= max_tokens + 50

    def test_truncation_preserves_complete_source_tags(self):
        """Truncation should cut at the last complete </source> tag boundary."""
        context = self._build_source_context(10, content_size=500)
        max_tokens = 200

        result = self._simulate_truncation(context, max_tokens)

        # Find the truncation notice and check content before it
        notice_idx = result.find("\n[RAG context truncated")
        if notice_idx > 0:
            content_before_notice = result[:notice_idx]
            # Should end with a complete </source> tag
            assert content_before_notice.rstrip().endswith("</source>")

    def test_truncation_notice_contains_token_counts(self):
        """Truncation notice should include original and target token counts."""
        context = self._build_source_context(15, content_size=1000)
        max_tokens = 300

        original_tokens = estimate_token_count(context)
        result = self._simulate_truncation(context, max_tokens)

        assert f"from {original_tokens}" in result
        assert f"to ~{max_tokens}" in result

    def test_truncation_with_single_huge_document(self):
        """A single enormous web search document should be truncated."""
        huge_content = "This is a very long web search result. " * 5000
        context = (
            f"<source><source_id>web_search_0</source_id>"
            f"<source_context>{huge_content}</source_context></source>"
        )
        max_tokens = 500

        result = self._simulate_truncation(context, max_tokens)

        assert "[RAG context truncated" in result
        assert len(result) < len(context)

    def test_truncation_result_is_shorter_than_original(self):
        """Truncated result must be shorter than the original context."""
        context = self._build_source_context(10, content_size=1000)
        max_tokens = 200

        result = self._simulate_truncation(context, max_tokens)
        assert len(result) < len(context)

    def test_no_truncation_when_exactly_at_limit(self):
        """Context exactly at the token limit should not be truncated."""
        # Build context and measure its tokens, then use that as the limit
        context = self._build_source_context(1, content_size=50)
        exact_tokens = estimate_token_count(context)

        result = self._simulate_truncation(context, exact_tokens)
        assert "[RAG context truncated" not in result

    def test_truncation_with_multiple_web_search_urls(self):
        """Simulate multiple web search results with URLs in source IDs."""
        context_parts = []
        for i in range(15):
            url = f"https://example.com/page-{i}"
            content = f"Web search result {i}. " * 20
            context_parts.append(
                f"<source><source_id>{url}</source_id>"
                f"<source_context>{content}</source_context></source>"
            )
        context = "\n".join(context_parts)
        max_tokens = 400

        result = self._simulate_truncation(context, max_tokens)

        assert "[RAG context truncated" in result
        # At least some complete sources should remain
        assert "<source>" in result
        assert "</source>" in result

    def test_truncation_preserves_at_least_some_content(self):
        """Even after truncation, some source content should remain."""
        context = self._build_source_context(10, content_size=1000)
        max_tokens = 300

        result = self._simulate_truncation(context, max_tokens)

        # Should still contain at least one source
        assert "<source>" in result

    def test_truncation_with_very_small_limit(self):
        """Very small token limit should still produce valid output."""
        context = self._build_source_context(5, content_size=500)
        max_tokens = 50

        result = self._simulate_truncation(context, max_tokens)

        assert "[RAG context truncated" in result

    def test_context_percentage_calculation(self):
        """Test that percentage-based context limits are calculated correctly."""
        model_context_length = 8192
        context_limit_pct = 0.30

        max_context_tokens = int(model_context_length * context_limit_pct)
        assert max_context_tokens == 2457  # 30% of 8192

    def test_context_percentage_50_percent(self):
        """Test 50% context limit calculation."""
        model_context_length = 16000
        context_limit_pct = 0.50

        max_context_tokens = int(model_context_length * context_limit_pct)
        assert max_context_tokens == 8000

    def test_fallback_when_no_model_context_length(self):
        """When model context length is 0 or missing, use fallback."""
        model_context_length = 0
        fallback_max_tokens = 8000
        context_limit_pct = 0.30

        if model_context_length and model_context_length > 0:
            max_context_tokens = int(model_context_length * context_limit_pct)
        else:
            max_context_tokens = fallback_max_tokens

        assert max_context_tokens == fallback_max_tokens

    def test_truncation_does_not_leave_partial_xml_tags(self):
        """After truncation at </source> boundary, no partial XML tags should remain."""
        context = self._build_source_context(10, content_size=500)
        max_tokens = 250

        result = self._simulate_truncation(context, max_tokens)

        notice_idx = result.find("\n[RAG context truncated")
        if notice_idx > 0:
            content_before = result[:notice_idx]
            # Count opening and closing source tags - closings should >= openings
            # since we truncate at </source>
            open_count = content_before.count("<source>")
            close_count = content_before.count("</source>")
            # Each complete source has one open and one close
            # Should have equal counts since we truncate at a complete </source>
            assert close_count >= open_count or close_count == open_count

    def test_truncation_notice_tokens_subtracted(self):
        """The truncation notice itself should be accounted for in the token budget."""
        context = self._build_source_context(10, content_size=1000)
        max_tokens = 300
        encoding_name = "cl100k_base"

        original_tokens = estimate_token_count(context, encoding_name)
        truncation_notice = (
            f"\n[RAG context truncated from {original_tokens} to ~{max_tokens} tokens]"
        )
        notice_tokens = estimate_token_count(truncation_notice, encoding_name)
        target_tokens = max_tokens - notice_tokens

        # target_tokens should be less than max_tokens
        assert target_tokens < max_tokens
        assert target_tokens > 0

    def test_realistic_web_search_scenario(self):
        """
        Simulate a realistic scenario: user does a web search that returns
        10 large web pages, and the model has a 4096 context window with
        30% allocated to RAG context.
        """
        # Simulate 10 web search results, each ~500 words
        sources = []
        for i in range(10):
            page_content = (
                f"This is the content of web page {i}. It contains detailed information "
                f"about the topic the user searched for. The page discusses various aspects "
                f"of the subject matter including history, current developments, and future "
                f"prospects. Each page provides unique insights and data points. "
            ) * 20  # ~100 words * 20 = ~2000 words per page
            sources.append(
                f"<source><source_id>https://example.com/result-{i}</source_id>"
                f"<source_context>{page_content}</source_context></source>"
            )
        context = "\n".join(sources)

        # Model has 4096 tokens, 30% for RAG = 1228 tokens
        model_context_length = 4096
        context_limit_pct = 0.30
        max_context_tokens = int(model_context_length * context_limit_pct)

        original_tokens = estimate_token_count(context)
        # The context should be way larger than the limit
        assert original_tokens > max_context_tokens

        result = self._simulate_truncation(context, max_context_tokens)

        # Should be truncated
        assert "[RAG context truncated" in result

        # Result should fit roughly within the budget
        result_tokens = estimate_token_count(result)
        # Allow some tolerance due to source tag boundary truncation
        assert result_tokens <= max_context_tokens + 100

        # Should still have valid structure
        assert "<source>" in result
        assert "</source>" in result

    def test_empty_context_not_truncated(self):
        """Empty context string should pass through without truncation."""
        result = self._simulate_truncation("", 500)
        assert result == ""
        assert "[RAG context truncated" not in result

    def test_context_with_unicode_content(self):
        """Web search results with unicode/French content should truncate correctly."""
        context_parts = []
        for i in range(10):
            content = (
                f"Résultat de recherche {i}. Le contenu inclut des caractères spéciaux: "
                f"é, è, ê, ë, à, â, ù, û, ç, ô, î. Cette page traite du sujet en détail. "
            ) * 50
            context_parts.append(
                f"<source><source_id>https://fr.wikipedia.org/wiki/Page_{i}</source_id>"
                f"<source_context>{content}</source_context></source>"
            )
        context = "\n".join(context_parts)
        max_tokens = 400

        result = self._simulate_truncation(context, max_tokens)

        assert "[RAG context truncated" in result
        assert len(result) < len(context)
