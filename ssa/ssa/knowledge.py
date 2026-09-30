"""Small, traceable local knowledge base for the voice-agent demo."""

from __future__ import annotations

import csv
import hashlib
import html
import ipaddress
import json
import math
import os
import re
import tempfile
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


STOP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}
BLOCK_TAGS = {"p", "li", "tr", "br", "section", "div", "article", "main", "blockquote", "ul", "ol"}
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
LONG_NUMBER_RE = re.compile(r"(?<!\d)\+?\d(?:[\s().-]*\d){9,14}(?!\d)")
TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
STOP_WORDS = {
    "a", "about", "and", "an", "are", "as", "before", "can", "does", "for", "from", "has", "how", "i", "if", "in", "is", "it", "me",
    "of", "on", "or", "should", "says", "the", "this", "to", "what", "when", "which", "with", "you", "your", "demo", "demonstration",
    "apa", "apakah", "bagaimana", "ini", "itu", "dan", "dengan", "untuk", "yang", "saya", "bisa", "dalam",
}
MEDICAL_CONDITIONS = {
    "asthma", "cancer", "diabetes", "epilepsy", "hiv", "hypertension", "kidney", "pregnancy", "stroke", "tuberculosis",
}
PRICE_QUERY_RE = re.compile(
    r"\b(?:how much|what(?:'s| is) the amount|what is my premium)\b.{0,80}\b(?:premium|rider|coverage|installment|payment|balance|fee|denda|cicilan)\b|"
    r"\b(?:premium|rider|coverage|installment|payment|balance|fee|denda|cicilan)\b.{0,50}\b(?:amount|cost|how much)\b",
    re.I,
)
CURRENCY_AMOUNT_RE = re.compile(r"(?:₱|\bPHP\b|\bIDR\b|\bRp\.?|\$|\bUSD\b)\s*\d|\d[\d,]*(?:\.\d{1,2})?\s*(?:PHP|IDR|rupiah|pesos?|dollars?)\b", re.I)
DEFINITION_QUERY_RE = re.compile(r"\b(?:what is (?:a|an)|what does .{1,40} mean|what is the definition of|define|meaning of|ano ang ibig sabihin|ano ang kahulugan|apa itu|apa arti)\b", re.I)
DEFINABLE_TERMS = {"premium", "rider", "riders", "beneficiary", "beneficiaries", "coverage", "tenor", "dp", "angsuran", "cicilan"}


class _ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0
        self._stack: list[tuple[str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        class_text = f"{attrs_map.get('class', '')} {attrs_map.get('id', '')}".lower()
        skip = tag in STOP_TAGS or any(term in class_text for term in ("cookie", "navigation", "site-nav"))
        self._stack.append((tag, skip))
        if skip:
            self._ignored += 1
        elif self._ignored == 0 and tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif self._ignored == 0 and tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif self._ignored == 0 and tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        closing_visible = self._ignored == 0
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                popped = self._stack[index:]
                del self._stack[index:]
                self._ignored = max(0, self._ignored - sum(1 for _, skip in popped if skip))
                break
        if closing_visible and (tag in BLOCK_TAGS or tag in {"h1", "h2", "h3", "h4"}):
            self.parts.append("\n")
        elif closing_visible and tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_data(self, data: str) -> None:
        if self._ignored == 0 and data.strip():
            self.parts.append(data)


def redact_pii(text: str) -> tuple[str, bool]:
    """Mask common email and phone patterns before indexing or returning content."""
    cleaned, email_hits = EMAIL_RE.subn("[REDACTED_EMAIL]", text)
    cleaned, phone_hits = LONG_NUMBER_RE.subn("[REDACTED_PHONE]", cleaned)
    return cleaned, bool(email_hits or phone_hits)


def _normalize_text(text: str) -> str:
    text = html.unescape(text).replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_html(text: str) -> str:
    parser = _ContentParser()
    parser.feed(text)
    parser.close()
    return _normalize_text("".join(parser.parts))


def _split_sections(text: str, fallback_title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    title = fallback_title
    body: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            if body:
                sections.append((title, "\n".join(body).strip()))
                body = []
            title = heading.group(2).strip()
        else:
            body.append(line)
    if body and "\n".join(body).strip():
        sections.append((title, "\n".join(body).strip()))
    return sections or [(fallback_title, text.strip())]


def _record_from_text(title: str, content: str, source: Path, metadata: dict[str, Any], section: str) -> dict[str, Any]:
    content = _normalize_text(content)
    content, pii_found = redact_pii(content)
    clean_title, title_pii = redact_pii(title)
    clean_section, section_pii = redact_pii(section)
    source_id, source_id_pii = redact_pii(str(metadata.get("source_id", source.stem)))
    source_uri, source_uri_pii = redact_pii(str(metadata.get("source_uri", source.resolve().as_uri())))
    search_terms, search_terms_pii = redact_pii(str(metadata.get("search_terms", "")))
    response_text = metadata.get("response_text")
    response_pii = False
    if response_text is not None:
        response_text, response_pii = redact_pii(str(response_text))
    record_id = "kb_" + hashlib.sha256(f"{source.resolve()}|{section}|{clean_title}|{content}".encode("utf-8")).hexdigest()[:14]
    record = {
        "record_id": record_id,
        "title": clean_title,
        "content": content,
        "record_type": metadata.get("record_type", "customer_facing"),
        "response_text": response_text,
        "search_terms": search_terms,
        "category": metadata.get("category", "faq"),
        "product": metadata.get("product", "general"),
        "market": metadata.get("market", "GLOBAL"),
        "locale": metadata.get("locale", "en"),
        "source_id": source_id,
        "source_uri": source_uri,
        "source_section": clean_section,
        "effective_from": metadata.get("effective_from"),
        "effective_to": metadata.get("effective_to"),
        "version": str(metadata.get("version", "1.0")),
        "pii_status": "redacted" if pii_found or title_pii or section_pii or source_id_pii or source_uri_pii or search_terms_pii or response_pii else "false",
        "review_status": metadata.get("review_status", "needs_review"),
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "conflict_group": metadata.get("conflict_group"),
    }
    return record


def _fetch_webpage(url: str) -> tuple[str | None, str | None]:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.lower() != "https" or not host:
        return None, "Website extraction accepts HTTPS URLs only."
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return None, "Local and private website addresses cannot be ingested."
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            return None, "Local and private website addresses cannot be ingested."
    except ValueError:
        pass
    request = urllib.request.Request(url, headers={"User-Agent": "KnowledgeGroundedVoiceDemo/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None, f"Expected an HTML webpage but received {content_type}."
            payload = response.read(5 * 1024 * 1024 + 1)
            if len(payload) > 5 * 1024 * 1024:
                return None, "Webpage exceeded the 5 MiB extraction limit."
            charset = response.headers.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace"), None
    except (OSError, UnicodeError, urllib.error.URLError, ValueError) as exc:
        return None, f"Could not fetch webpage: {exc}"


def ingest_file(path: str | Path, metadata: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract supported local content while preserving source and section metadata."""
    metadata = metadata or {}
    location = str(path)
    has_url_scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", location) is not None
    parsed = urllib.parse.urlsplit(location) if has_url_scheme else None
    if parsed is not None:
        if parsed.scheme.lower() != "https":
            return [], ["Website extraction accepts HTTPS URLs only."]
        html_text, error = _fetch_webpage(location)
        if error:
            return [], [error]
        source = Path(Path(parsed.path).name or parsed.hostname or "web-source")
        metadata = {**metadata, "source_uri": location, "source_id": metadata.get("source_id", parsed.hostname)}
        text = _extract_html(html_text or "")
        records = []
        for title, content in _split_sections(text, source.stem):
            if content.strip():
                records.append(_record_from_text(title, content, source, metadata, title))
        if not records:
            return [], ["Webpage had no extractable main content."]
        return records, []
    source = Path(path)
    warnings: list[str] = []
    suffix = source.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".markdown"}:
            text = source.read_text(encoding="utf-8-sig")
        elif suffix in {".html", ".htm"}:
            text = _extract_html(source.read_text(encoding="utf-8-sig", errors="replace"))
        elif suffix == ".csv":
            with source.open(encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            records: list[dict[str, Any]] = []
            for row_number, row in enumerate(rows, start=2):
                content = row.get("content") or row.get("description") or "; ".join(f"{key}: {value}" for key, value in row.items() if value)
                title = row.get("title") or row.get("name") or f"{source.stem} row {row_number}"
                row_metadata = {**metadata, **{key: row[key] for key in ("category", "product", "market", "locale", "version", "record_type", "response_text") if row.get(key)}}
                records.append(_record_from_text(title, content, source, row_metadata, f"row {row_number}"))
            if not rows:
                warnings.append("CSV had no data rows.")
            return records, warnings
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return [], ["PDF extraction requires pypdf, which is not installed."]
            reader = PdfReader(str(source))
            page_text = []
            for page_no, page in enumerate(reader.pages, start=1):
                extracted = page.extract_text() or ""
                if extracted.strip():
                    page_text.append((page_no, extracted))
            records = []
            for page_no, page in page_text:
                for title, content in _split_sections(page, f"{source.stem} page {page_no}"):
                    if content.strip():
                        records.append(_record_from_text(title, content, source, metadata, f"page {page_no}: {title}"))
            if not records:
                warnings.append("PDF contained no extractable text; check whether it is scanned or image-only.")
            return records, warnings
        else:
            return [], [f"Unsupported file type: {suffix or '(none)'}"]
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return [], [f"Could not extract {source.name}: {exc}"]

    records = []
    for title, content in _split_sections(text, source.stem):
        if content.strip():
            records.append(_record_from_text(title, content, source, metadata, title))
    if not records:
        warnings.append("Source had no extractable text.")
    return records, warnings


def _tokens(text: str) -> list[str]:
    return [part.casefold() for part in TOKEN_RE.findall(text) if part.casefold() not in STOP_WORDS]


def _active(record: dict[str, Any], today: date) -> bool:
    if record.get("review_status") not in {"approved", "demo_approved"}:
        return False
    effective_to = record.get("effective_to")
    if effective_to:
        try:
            if date.fromisoformat(str(effective_to)[:10]) < today:
                return False
        except ValueError:
            return False
    effective_from = record.get("effective_from")
    if effective_from:
        try:
            if date.fromisoformat(str(effective_from)[:10]) > today:
                return False
        except ValueError:
            return False
    return True


def _dedupe_scope(record: dict[str, Any]) -> tuple[str, ...]:
    fields = (
        "market", "locale", "product", "category", "source_id", "title", "version", "effective_from", "effective_to", "review_status", "conflict_group",
    )
    return tuple(str(record.get(key, "")).casefold() for key in fields)


def _deduplicate(records: list[dict[str, Any]], threshold: float = 0.92) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen_hashes: set[tuple[tuple[str, ...], str]] = set()
    for record in records:
        content = _normalize_text(str(record.get("content", "")))
        digest = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()
        scope = _dedupe_scope(record)
        scoped_digest = (scope, digest)
        if scoped_digest in seen_hashes:
            continue
        if any(
            scope == _dedupe_scope(prev)
            and SequenceMatcher(None, content.casefold(), str(prev.get("content", "")).casefold()).ratio() >= threshold
            for prev in unique
        ):
            continue
        seen_hashes.add(scoped_digest)
        unique.append(record)
    return unique


def _same_conflict_identity(first: dict[str, Any], second: dict[str, Any]) -> bool:
    group = first.get("conflict_group")
    if group and group == second.get("conflict_group"):
        return True
    identity = ("title", "category", "market", "locale", "product")
    return all(str(first.get(key, "")).casefold() == str(second.get(key, "")).casefold() for key in identity)


def _has_definition(text: str, term: str, locale: str) -> bool:
    singular = {"riders": "rider", "beneficiaries": "beneficiary"}.get(term, term)
    language = locale.lower().split("-", 1)[0]
    if language == "fil":
        marker = r"(?:ay|ibig sabihin)"
    elif language == "id":
        marker = r"(?:adalah|berarti)"
    else:
        marker = r"(?:is|means|refers to|is defined as)"
    return bool(re.search(rf"\b{re.escape(singular)}\b.{{0,24}}\b{marker}\b", text, re.I))


class KnowledgeBase:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = _deduplicate(records)

    def search(
        self,
        query: str,
        *,
        market: str | None = None,
        locale: str | None = None,
        product: str | None = None,
        category: str | None = None,
        top_k: int = 5,
        today: date | None = None,
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        today = today or date.today()
        candidates = []
        for record in self.records:
            if not _active(record, today):
                continue
            if market and str(record.get("market", "")).casefold() not in {market.casefold(), "global"}:
                continue
            if locale and record.get("locale") not in {locale, "en", "en-PH"}:
                continue
            if product and record.get("product") not in {product, "general"}:
                continue
            if category and record.get("category") != category:
                continue
            doc_tokens = _tokens(f"{record.get('title', '')} {record.get('category', '')} {record.get('content', '')} {record.get('search_terms', '')}")
            counts: dict[str, int] = {}
            for token in doc_tokens:
                counts[token] = counts.get(token, 0) + 1
            unique_query = set(query_tokens)
            matched = unique_query.intersection(counts)
            if not matched:
                continue
            weighted = sum((1.0 + math.log(counts[token])) for token in matched)
            coverage = len(matched) / len(unique_query)
            score = weighted / math.sqrt(max(len(doc_tokens), 1)) + 0.75 * coverage
            query_phrase = " ".join(query_tokens)
            title_phrase = " ".join(_tokens(str(record.get("title", ""))))
            content_phrase = " ".join(_tokens(str(record.get("content", ""))))
            if query_phrase and (query_phrase in title_phrase or query_phrase in content_phrase):
                score += 0.25
            citation = {
                "record_id": record["record_id"],
                "title": record["title"],
                "source_id": record["source_id"],
                "source_uri": record["source_uri"],
                "source_section": record["source_section"],
                "version": record["version"],
            }
            candidates.append({"record": record, "score": round(score, 4), "citation": citation})
            candidates[-1]["coverage"] = round(coverage, 4)
        candidates.sort(key=lambda item: (-item["score"], item["record"]["record_id"]))
        return candidates[: max(0, top_k)]

    def answer(
        self,
        question: str,
        *,
        locale: str = "en-PH",
        market: str | None = None,
        product: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        found = self.search(question, locale=locale, market=market, product=product, category=category, top_k=len(self.records))
        if not found or found[0]["score"] < 0.58:
            return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": found[0]["score"] if found else 0.0}
        best = found[0]
        medical_conditions = MEDICAL_CONDITIONS.intersection(_tokens(question))
        if medical_conditions and not medical_conditions.issubset(_tokens(best["record"].get("content", ""))):
            return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": best["score"]}
        answer_text = best["record"].get("response_text") or best["record"].get("content", "")
        if best["record"].get("record_type") == "agent_guidance" and not best["record"].get("response_text"):
            return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": best["score"]}
        if PRICE_QUERY_RE.search(question) and not CURRENCY_AMOUNT_RE.search(str(answer_text)):
            return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": best["score"]}
        if DEFINITION_QUERY_RE.search(question):
            asked_terms = set(_tokens(question)).intersection(DEFINABLE_TERMS)
            if asked_terms and any(not _has_definition(str(answer_text), term, locale) for term in asked_terms):
                return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": best["score"]}
        conflicting = [
            item for item in found
            if item["record"]["record_id"] != best["record"]["record_id"]
            and item["score"] >= 0.58
            and _same_conflict_identity(best["record"], item["record"])
            and _normalize_text(str(best["record"].get("content", ""))).casefold() != _normalize_text(str(item["record"].get("content", ""))).casefold()
        ]
        if conflicting:
            citations = [best["citation"], conflicting[0]["citation"]]
            return {
                "status": "conflict",
                "answer": _conflict_message(locale),
                "citations": citations,
                "retrieval_score": best["score"],
            }
        if best.get("coverage", 1.0) < 0.35:
            return {"status": "unavailable", "answer": _unavailable_message(locale), "citations": [], "retrieval_score": best["score"]}
        return {
            "status": "answered",
            "answer": str(answer_text).strip(),
            "citations": [best["citation"]],
            "retrieval_score": best["score"],
        }


def _unavailable_message(locale: str) -> str:
    if locale.startswith("id"):
        return "Maaf, informasi itu belum tersedia di sumber demo yang disetujui. Saya bisa meminta bantuan petugas."
    if locale.startswith("fil"):
        return "Paumanhin, wala sa naaprubahang demo source ang impormasyong iyon. Maaari akong humingi ng tulong ng kinatawan."
    return "That information is unavailable in the approved demo sources. I can ask a representative to help."


def _conflict_message(locale: str) -> str:
    if locale.startswith("id"):
        return "Ada informasi demo yang saling bertentangan. Saya belum bisa memastikan jawabannya sebelum petugas meninjau sumber."
    if locale.startswith("fil"):
        return "May hindi magkatugmang impormasyon sa demo. Ipapasuri ko muna ito sa kinatawan bago sumagot."
    return "I found conflicting demo information. I can't confirm the answer until a representative reviews it."


def load_records(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, list):
        raise ValueError("Knowledge base JSON must contain a list of records.")
    return payload


def save_records(path: str | Path, records: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
