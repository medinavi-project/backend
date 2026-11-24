"""MedlinePlus Health Topics FAISS index (MediNavi addition).

Build once (re-run monthly to refresh):
    python -m app.triage.medlineplus_index

It downloads the official MedlinePlus topic XML, embeds every English topic
with CLOVA bge-m3 (CLOVASTUDIO_API_KEY) and writes two files next to this
module: medlineplus.faiss (vectors) and medlineplus_docs.json (title/url/summary).
At request time only the query is embedded; search runs in-process.
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import numpy as np
import requests

DIR = Path(__file__).parent / "medlineplus_index"
INDEX_PATH = DIR / "medlineplus.faiss"
DOCS_PATH = DIR / "medlineplus_docs.json"
XML_LIST_URL = "https://medlineplus.gov/xml.html"


def _normalize(vecs):
    vecs = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return vecs / np.where(norms == 0, 1, norms)


def parse_topics(xml_bytes):
    """English health topics -> [{title, url, summary, groups}]."""
    from app.triage.rag_chatbot import _clean_html

    docs = []
    for topic in ET.fromstring(xml_bytes).iter("health-topic"):
        if topic.get("language") != "English":
            continue
        summary = _clean_html(topic.findtext("full-summary") or "")[:900]
        if topic.get("title") and summary:
            docs.append({
                "title": topic.get("title"),
                "url": topic.get("url") or "",
                "summary": summary,
                "groups": [g.text for g in topic.findall("group") if g.text][:3],
            })
    return docs


def doc_text(doc):
    return f"Title: {doc['title']} / Summary: {doc['summary']}"


@lru_cache(maxsize=1)
def _load():
    import faiss

    if not INDEX_PATH.exists() or not DOCS_PATH.exists():
        return None
    return faiss.read_index(str(INDEX_PATH)), json.loads(DOCS_PATH.read_text())


def available():
    return _load() is not None


def search(query_vec, k=3):
    """Top-k documents by cosine similarity, each with similarity_score."""
    index, docs = _load()
    scores, ids = index.search(_normalize([query_vec]), k)
    return [
        dict(docs[i], similarity_score=round(float(s), 4))
        for s, i in zip(scores[0], ids[0])
        if i >= 0
    ]


def build(docs, embed, pause=1.1):
    """pause ~1s keeps under CLOVA's per-minute embedding limit (429 seen at
    20/s). Progress is checkpointed, so a re-run resumes where it stopped."""
    import faiss

    DIR.mkdir(exist_ok=True)
    partial = DIR / "partial.npy"
    vecs = list(np.load(partial)) if partial.exists() else []
    if vecs:
        print(f"  resume from {len(vecs)}/{len(docs)}")
    for n, doc in enumerate(docs[len(vecs):], len(vecs) + 1):
        for attempt in range(8):
            try:
                vecs.append(embed(doc_text(doc)))
                break
            except Exception as exc:  # rate limit / transient error: back off
                if attempt == 7:
                    np.save(partial, np.asarray(vecs, dtype=np.float32))
                    raise
                wait = min(60, 5 * 2 ** attempt)
                print(f"  retry in {wait}s ({doc['title']}): {str(exc)[:60]}", file=sys.stderr)
                time.sleep(wait)
        if n % 50 == 0:
            np.save(partial, np.asarray(vecs, dtype=np.float32))
            print(f"  {n}/{len(docs)}")
        time.sleep(pause)
    vecs = _normalize(vecs)
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss.write_index(index, str(INDEX_PATH))
    DOCS_PATH.write_text(json.dumps(docs, ensure_ascii=False))
    partial.unlink(missing_ok=True)
    _load.cache_clear()
    return index.ntotal


def _download_latest_xml():
    page = requests.get(XML_LIST_URL, timeout=30).text
    names = sorted(set(re.findall(r"mplus_topics_\d{4}-\d{2}-\d{2}\.xml", page)))
    if not names:
        raise SystemExit("MedlinePlus XML link not found on " + XML_LIST_URL)
    url = f"https://medlineplus.gov/xml/{names[-1]}"
    print("download", url)
    return requests.get(url, timeout=120).content


if __name__ == "__main__":
    from app.triage.rag_chatbot import CLOVA_API_KEY, get_embedding

    if not CLOVA_API_KEY:
        raise SystemExit("CLOVASTUDIO_API_KEY is not set (backend/.env)")
    topics = parse_topics(_download_latest_xml())
    print(f"embedding {len(topics)} topics")
    print("indexed", build(topics, get_embedding), "->", DIR)
