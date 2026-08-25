"""
okf_client.py — REST client for OKF Bundle Agent Service

Provides simple HTTP wrapper for calling OKF Bundle Agent endpoints.
This client abstracts the REST communication, allowing NL2SqlGen-OKF-Agents
to use OKF operations from the standalone okf-bundle-agent service.
"""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("clearbank.okf_client")


class OKFBundleClient:
    """HTTP client for OKF Bundle Agent service."""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 30):
        """
        Initialize OKF Bundle Client.

        Args:
            base_url: OKF service URL (default: from OKF_BUNDLE_AGENT_URL env var)
            timeout: Request timeout in seconds
        """
        self.base_url = base_url or os.getenv(
            "OKF_BUNDLE_AGENT_URL", "http://localhost:8002"
        )
        self.timeout = timeout
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout)
        logger.info(
            "OKFBundleClient initialized: base_url=%s, timeout=%d", self.base_url, timeout)

    def select_section(self, query: str) -> tuple[str, str]:
        """
        Classify query into OKF section and domain.

        Args:
            query: User query

        Returns:
            (section, domain) tuple. section is one of "Tables", "Metrics",
            "Runbooks", "Datasets". domain is "retail_banking",
            "customer_support", or "" if the query is not confidently
            scoped to a single domain.

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        logger.debug("Selecting section for query: %s", query[:100])
        response = self.client.post(
            "/section-selection",
            json={"query": query}
        )
        response.raise_for_status()
        data = response.json()
        section = data["section"]
        domain = data.get("domain") or ""
        logger.info("Section selected: %s (domain=%s)",
                    section, domain or "all")
        return section, domain

    def retrieve_section(self, section_type: str, domain: str = "") -> str:
        """
        Retrieve OKF content for section.

        Args:
            section_type: Section type (Tables, Metrics, Runbooks, Datasets)
            domain: Optional domain filter ("retail_banking" | "customer_support").
                    Empty string returns content from all domains.

        Returns:
            Markdown content from all concepts in section

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        logger.debug("Retrieving section: %s (domain=%s)",
                     section_type, domain or "all")
        response = self.client.post(
            "/section-retrieval",
            json={"section_type": section_type, "domain": domain or None}
        )
        response.raise_for_status()
        content = response.json()["content"]
        logger.info("Section retrieved: %d chars", len(content))
        return content

    def build_context(self, query: str, okf_content: str, domain: str = "") -> str:
        """
        Build structured context from OKF content.

        Args:
            query: User query
            okf_content: Raw OKF markdown
            domain: Optional domain ("retail_banking" | "customer_support") so
                    the SQL generator knows whether to schema-qualify tables.

        Returns:
            Structured system prompt

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        logger.debug("Building context for query: %s", query[:100])
        response = self.client.post(
            "/context-building",
            json={
                "query": query,
                "okf_content": okf_content,
                "domain": domain or None
            }
        )
        response.raise_for_status()
        context = response.json()["system_context"]
        logger.info("Context built: %d chars", len(context))
        return context

    def query_knowledge_base(self, query: str, okf_content: str) -> str:
        """
        Query knowledge base (runbooks/datasets).

        Args:
            query: User question
            okf_content: OKF markdown content

        Returns:
            Answer from knowledge base

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        logger.debug("Querying knowledge base: %s", query[:100])
        response = self.client.post(
            "/knowledge-base-query",
            json={
                "query": query,
                "okf_content": okf_content
            }
        )
        response.raise_for_status()
        answer = response.json()["answer"]
        logger.info("Knowledge base answer received: %d chars", len(answer))
        return answer

    def run_pipeline(self, query: str) -> dict:
        """
        Run complete OKF pipeline.

        Args:
            query: User query

        Returns:
            Dict with section, context, answer, raw_content

        Raises:
            httpx.HTTPStatusError: If request fails
        """
        logger.debug("Running unified pipeline for query: %s", query[:100])
        response = self.client.post(
            "/pipeline/run",
            json={"query": query}
        )
        response.raise_for_status()
        result = response.json()
        logger.info("Pipeline complete: section=%s", result.get("section"))
        return result

    def close(self):
        """Close HTTP client connection."""
        self.client.close()
        logger.info("OKFBundleClient closed")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Global singleton instance
_client: Optional[OKFBundleClient] = None


def get_okf_client() -> OKFBundleClient:
    """
    Get or create global OKF client instance.

    Returns:
        OKFBundleClient singleton

    Usage:
        client = get_okf_client()
        section = client.select_section("Show delinquent loans")
    """
    global _client
    if _client is None:
        _client = OKFBundleClient()
    return _client


def close_okf_client():
    """Close global OKF client."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
