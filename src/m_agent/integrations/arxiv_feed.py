"""Small, injectable arXiv RSS/Atom client and normalizer.

This module deliberately stops at source normalization.  It does not know
about Heartbeat, runtime ingestion, or how a matching paper is consumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Dict, Iterable, Optional, Protocol, Sequence, Tuple
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


ARXIV_ATOM_FEED_BASE_URL = "https://rss.arxiv.org/atom"
ARXIV_PROVIDER_ITEM_CAP = 2_000

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_ID_PATTERN = re.compile(
    r"(?i)(?:arxiv(?:\.org)?[:/]|oai:arxiv\.org:)?"
    r"(?P<base>(?:[a-z][a-z0-9.-]*/\d{7}|\d{4}\.\d{4,5}))"
    r"(?:v(?P<version>\d+))?"
)
_SUMMARY_PREFIX = re.compile(
    r"^\s*arxiv:\s*\S+\s+announce\s+type:\s*\S+\s*"
    r"(?:abstract:\s*)?",
    re.IGNORECASE,
)


class ArxivFeedError(RuntimeError):
    """Base error for source retrieval and parsing."""


class ArxivFeedTooLargeError(ArxivFeedError):
    """Raised before parsing an unexpectedly large feed."""


class ArxivFeedParseError(ArxivFeedError):
    """Raised when an arXiv feed cannot be normalized safely."""


@dataclass(frozen=True)
class ArxivFeedResponse:
    status_code: int
    content: bytes = b""
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class ArxivPaper:
    """Provider-neutral metadata for one announced arXiv revision."""

    base_id: str
    version: int
    title: str
    summary: str
    url: str
    authors: Tuple[str, ...]
    categories: Tuple[str, ...]
    announce_type: str
    announced_at: str
    doi: str = ""

    @property
    def revision_id(self) -> str:
        return f"{self.base_id}v{self.version}"

    def to_dict(self) -> Dict[str, object]:
        return {
            "base_id": self.base_id,
            "version": self.version,
            "revision_id": self.revision_id,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "authors": list(self.authors),
            "categories": list(self.categories),
            "announce_type": self.announce_type,
            "announced_at": self.announced_at,
            "doi": self.doi,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "ArxivPaper":
        raw_authors = payload.get("authors")
        authors = (
            raw_authors
            if isinstance(raw_authors, (list, tuple))
            else ()
        )
        raw_categories = payload.get("categories")
        categories = (
            raw_categories
            if isinstance(raw_categories, (list, tuple))
            else ()
        )
        return cls(
            base_id=str(payload.get("base_id", "") or "").strip(),
            version=max(1, int(payload.get("version", 1) or 1)),
            title=str(payload.get("title", "") or "").strip(),
            summary=str(payload.get("summary", "") or "").strip(),
            url=str(payload.get("url", "") or "").strip(),
            authors=tuple(
                str(item or "").strip()
                for item in authors
                if str(item or "").strip()
            ),
            categories=tuple(
                str(item or "").strip()
                for item in categories
                if str(item or "").strip()
            ),
            announce_type=str(
                payload.get("announce_type", "") or ""
            ).strip(),
            announced_at=str(
                payload.get("announced_at", "") or ""
            ).strip(),
            doi=str(payload.get("doi", "") or "").strip(),
        )


class ArxivFeedClient(Protocol):
    """Injectable boundary used by the Heartbeat monitor."""

    @property
    def source_identity(self) -> str:
        """Stable endpoint/base-URL identity for validator scoping."""

    def fetch(
        self,
        *,
        categories: Sequence[str],
        etag: str = "",
        last_modified: str = "",
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 4_000_000,
    ) -> ArxivFeedResponse:
        """Fetch one combined category feed, optionally conditionally."""


class UrllibArxivFeedClient:
    """Dependency-free HTTP client for the public arXiv Atom feed."""

    def __init__(
        self,
        *,
        feed_base_url: str = ARXIV_ATOM_FEED_BASE_URL,
        user_agent: str = "M-Agent arXiv observer",
    ) -> None:
        self.feed_base_url = str(feed_base_url or "").strip().rstrip("/")
        if not self.feed_base_url:
            raise ValueError("feed_base_url must be non-empty")
        self.user_agent = str(user_agent or "").strip()

    @property
    def source_identity(self) -> str:
        """Stable resource identity used to scope HTTP validators."""

        return self.feed_base_url

    def fetch(
        self,
        *,
        categories: Sequence[str],
        etag: str = "",
        last_modified: str = "",
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 4_000_000,
    ) -> ArxivFeedResponse:
        normalized_categories = _normalize_categories(categories)
        if not normalized_categories:
            raise ValueError("at least one arXiv category is required")
        category_path = quote("+".join(normalized_categories), safe="+.")
        request = Request(
            f"{self.feed_base_url}/{category_path}",
            headers={
                **({"User-Agent": self.user_agent} if self.user_agent else {}),
                **({"If-None-Match": etag} if str(etag or "").strip() else {}),
                **(
                    {"If-Modified-Since": last_modified}
                    if str(last_modified or "").strip()
                    else {}
                ),
                "Accept": "application/atom+xml, application/rss+xml;q=0.9",
            },
        )
        byte_limit = max(1, int(max_response_bytes or 1))
        try:
            with urlopen(  # noqa: S310 - fixed/configured HTTPS source boundary
                request,
                timeout=max(0.1, float(timeout_seconds or 5.0)),
            ) as response:
                content = response.read(byte_limit + 1)
                if len(content) > byte_limit:
                    raise ArxivFeedTooLargeError(
                        f"arXiv feed exceeded {byte_limit} bytes"
                    )
                return ArxivFeedResponse(
                    status_code=int(getattr(response, "status", 200) or 200),
                    content=content,
                    etag=str(response.headers.get("ETag", "") or "").strip(),
                    last_modified=str(
                        response.headers.get("Last-Modified", "") or ""
                    ).strip(),
                )
        except HTTPError as exc:
            if int(exc.code) == 304:
                return ArxivFeedResponse(
                    status_code=304,
                    etag=str(exc.headers.get("ETag", "") or etag).strip(),
                    last_modified=str(
                        exc.headers.get("Last-Modified", "")
                        or last_modified
                    ).strip(),
                )
            raise ArxivFeedError(
                f"arXiv feed returned HTTP {int(exc.code)}"
            ) from exc


def split_arxiv_identifier(value: str) -> Tuple[str, int]:
    """Return the canonical base id and explicit/default version."""

    match = _ID_PATTERN.search(str(value or "").strip())
    if match is None:
        raise ArxivFeedParseError("arXiv feed entry has no valid identifier")
    base_id = str(match.group("base") or "").strip()
    version = max(1, int(match.group("version") or 1))
    return base_id, version


def parse_arxiv_feed(
    content: bytes,
    *,
    max_items: int = ARXIV_PROVIDER_ITEM_CAP,
    provider_item_cap: int = ARXIV_PROVIDER_ITEM_CAP,
) -> Tuple[ArxivPaper, ...]:
    """Parse either official RSS 2.0 or Atom, merging cross-list duplicates."""

    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError) as exc:
        raise ArxivFeedParseError("arXiv feed is not valid XML") from exc

    root_name = _local_name(root.tag)
    if root_name == "feed":
        nodes = list(root.findall(f"{{{_ATOM_NS}}}entry"))
        parser = _parse_atom_entry
    elif root_name == "rss":
        channel = root.find("channel")
        nodes = list(channel.findall("item")) if channel is not None else []
        parser = _parse_rss_item
    else:
        raise ArxivFeedParseError(
            f"unsupported arXiv feed root element: {root_name or '<empty>'}"
        )

    safe_limit = max(1, int(max_items or 1))
    source_cap = max(1, int(provider_item_cap or 1))
    # The official feed cannot represent whether a response containing its
    # exact cap was truncated.  Treat that boundary as ambiguous and never
    # advance a source frontier from it.
    if len(nodes) >= source_cap:
        raise ArxivFeedTooLargeError(
            "arXiv feed reached the provider cap "
            f"of {source_cap} items; completeness is unknown"
        )
    if len(nodes) > safe_limit:
        raise ArxivFeedTooLargeError(
            f"arXiv feed contained {len(nodes)} items; limit is {safe_limit}"
        )

    by_revision: Dict[str, ArxivPaper] = {}
    for node in nodes:
        paper = parser(node)
        existing = by_revision.get(paper.revision_id)
        by_revision[paper.revision_id] = (
            _merge_duplicate(existing, paper) if existing is not None else paper
        )
    return tuple(by_revision.values())


def _parse_atom_entry(node: ET.Element) -> ArxivPaper:
    raw_id = _child_text(node, "id", namespace=_ATOM_NS)
    summary = _clean_summary(_child_text(node, "summary", namespace=_ATOM_NS))
    base_id, version = split_arxiv_identifier(raw_id or summary)
    links = node.findall(f"{{{_ATOM_NS}}}link")
    url = next(
        (
            str(link.attrib.get("href", "") or "").strip()
            for link in links
            if str(link.attrib.get("rel", "alternate") or "alternate")
            == "alternate"
        ),
        f"https://arxiv.org/abs/{base_id}",
    )
    categories = tuple(
        str(item.attrib.get("term", "") or "").strip()
        for item in node.findall(f"{{{_ATOM_NS}}}category")
        if str(item.attrib.get("term", "") or "").strip()
    )
    atom_authors = tuple(
        _child_text(author, "name", namespace=_ATOM_NS)
        for author in node.findall(f"{{{_ATOM_NS}}}author")
        if _child_text(author, "name", namespace=_ATOM_NS)
    )
    dc_creator = _child_text(node, "creator", namespace=_DC_NS)
    authors = atom_authors or tuple(
        item.strip() for item in dc_creator.split(",") if item.strip()
    )
    announce_type = _child_text(
        node,
        "announce_type",
        namespace=_ARXIV_NS,
    ) or _announce_type_from_text(
        _child_text(node, "summary", namespace=_ATOM_NS)
    )
    return ArxivPaper(
        base_id=base_id,
        version=version,
        title=_collapse_space(_child_text(node, "title", namespace=_ATOM_NS)),
        summary=summary,
        url=url,
        authors=authors,
        categories=_normalize_categories(categories),
        announce_type=announce_type.lower(),
        announced_at=_normalize_datetime(
            _child_text(node, "published", namespace=_ATOM_NS)
            or _child_text(node, "updated", namespace=_ATOM_NS)
        ),
        doi=_child_text(node, "DOI", namespace=_ARXIV_NS),
    )


def _parse_rss_item(node: ET.Element) -> ArxivPaper:
    raw_description = _child_text(node, "description")
    raw_id = _child_text(node, "guid") or raw_description
    base_id, version = split_arxiv_identifier(raw_id)
    categories = tuple(
        _collapse_space(item.text or "")
        for item in node.findall("category")
        if _collapse_space(item.text or "")
    )
    creator = _child_text(node, "creator", namespace=_DC_NS)
    authors = tuple(
        item.strip() for item in creator.split(",") if item.strip()
    )
    announce_type = _child_text(
        node,
        "announce_type",
        namespace=_ARXIV_NS,
    ) or _announce_type_from_text(raw_description)
    return ArxivPaper(
        base_id=base_id,
        version=version,
        title=_collapse_space(_child_text(node, "title")),
        summary=_clean_summary(raw_description),
        url=_child_text(node, "link") or f"https://arxiv.org/abs/{base_id}",
        authors=authors,
        categories=_normalize_categories(categories),
        announce_type=announce_type.lower(),
        announced_at=_normalize_datetime(_child_text(node, "pubDate")),
        doi=_child_text(node, "DOI", namespace=_ARXIV_NS),
    )


def _merge_duplicate(first: ArxivPaper, second: ArxivPaper) -> ArxivPaper:
    return ArxivPaper(
        base_id=first.base_id,
        version=first.version,
        title=first.title or second.title,
        summary=first.summary or second.summary,
        url=first.url or second.url,
        authors=first.authors or second.authors,
        categories=_normalize_categories((*first.categories, *second.categories)),
        announce_type=first.announce_type or second.announce_type,
        announced_at=first.announced_at or second.announced_at,
        doi=first.doi or second.doi,
    )


def _child_text(
    node: ET.Element,
    name: str,
    *,
    namespace: str = "",
) -> str:
    tag = f"{{{namespace}}}{name}" if namespace else name
    child = node.find(tag)
    return _collapse_space(child.text or "") if child is not None else ""


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _collapse_space(value: str) -> str:
    return " ".join(str(value or "").split())


def _clean_summary(value: str) -> str:
    return _collapse_space(_SUMMARY_PREFIX.sub("", str(value or ""), count=1))


def _announce_type_from_text(value: str) -> str:
    match = re.search(
        r"announce\s+type:\s*([^\s]+)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return str(match.group(1) if match is not None else "new").strip()


def _normalize_categories(values: Iterable[str]) -> Tuple[str, ...]:
    normalized = {
        str(value or "").strip()
        for value in values
        if str(value or "").strip()
    }
    return tuple(sorted(normalized, key=str.casefold))


def _normalize_datetime(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "ARXIV_ATOM_FEED_BASE_URL",
    "ARXIV_PROVIDER_ITEM_CAP",
    "ArxivFeedClient",
    "ArxivFeedError",
    "ArxivFeedParseError",
    "ArxivFeedResponse",
    "ArxivFeedTooLargeError",
    "ArxivPaper",
    "UrllibArxivFeedClient",
    "parse_arxiv_feed",
    "split_arxiv_identifier",
]
