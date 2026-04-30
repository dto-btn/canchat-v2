import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from open_webui.utils.middleware import sanitize_tool_ids_for_features


def test_sanitize_tool_ids_for_web_search():
    tool_ids = ["mcp_news_server"]
    features = {"web_search": True}
    assert sanitize_tool_ids_for_features(tool_ids, features) is None


def test_sanitize_tool_ids_for_wiki_grounding():
    tool_ids = ["mcp_news_server"]
    features = {"wiki_grounding": True}
    assert sanitize_tool_ids_for_features(tool_ids, features) is None


def test_sanitize_tool_ids_when_no_exclusive_feature():
    tool_ids = ["mcp_news_server"]
    features = {"image_generation": True}
    assert sanitize_tool_ids_for_features(tool_ids, features) == tool_ids
