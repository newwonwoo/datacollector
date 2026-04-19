"""Real service adapters. Each matches the collector.services.Services contract.

Adapters depend only on stdlib (urllib + subprocess + json). A pluggable
`http` callable is injected so tests can avoid network.
"""
from .youtube import YouTubeAdapter
from .llm_anthropic import AnthropicAdapter
from .llm_gemini import GeminiAdapter
from .git_sync import GitSyncAdapter

__all__ = [
    "YouTubeAdapter",
    "AnthropicAdapter",
    "GeminiAdapter",
    "GitSyncAdapter",
]
