"""Publication seeds — NCBI E-utilities (PubMed).

Publications are not decoration. `PRIM_E01 compute_SA` decays source authority by
`1/log(age_years + e)`, so every edge's score depends on a real publication year,
and SCAN-11's audit verdict depends on a resolvable identifier. Fabricated PMIDs
would make both untestable.
"""

from __future__ import annotations

from typing import Any

from .http import Fetcher

PUBMED = Fetcher("pubmed", rate_key="ncbi")
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# PRIM_P05 load_source_authority_table. Journal tiering feeds PRIM_P06.
HIGH_AUTHORITY_JOURNALS = {
    "nature", "science", "cell", "the new england journal of medicine", "lancet",
    "nature medicine", "nature genetics", "cancer cell", "immunity", "neuron",
    "the lancet", "jama", "nature biotechnology",
}


def search_pubmed(term: str, retmax: int = 30, mindate: int = 2015,
                  maxdate: int = 2026) -> list[str]:
    res = PUBMED.get(
        f"{EUTILS}/esearch.fcgi",
        params={
            "db": "pubmed", "term": term, "retmode": "json", "retmax": retmax,
            "mindate": mindate, "maxdate": maxdate, "datetype": "pdat",
            "sort": "relevance",
        },
    )
    if not res:
        return []
    return list(((res.data.get("esearchresult") or {}).get("idlist")) or [])


def fetch_pubmed_summaries(pmids: list[str]) -> list[dict[str, Any]]:
    """Batched esummary. NCBI allows ~200 ids per call; we chunk at 100."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(pmids), 100):
        chunk = pmids[i : i + 100]
        res = PUBMED.get(
            f"{EUTILS}/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(chunk), "retmode": "json"},
        )
        if not res:
            continue
        result = res.data.get("result") or {}
        for pmid in result.get("uids") or []:
            r = result.get(pmid) or {}
            year = None
            pubdate = r.get("pubdate") or ""
            if pubdate[:4].isdigit():
                year = int(pubdate[:4])
            journal = (r.get("fulljournalname") or r.get("source") or "").strip()
            doi = next(
                (a.get("value") for a in (r.get("articleids") or [])
                 if a.get("idtype") == "doi"),
                None,
            )
            out.append(
                {
                    "publication_id": f"PMID:{pmid}",
                    "title": r.get("title"),
                    "journal": journal,
                    "issn": r.get("issn") or None,
                    "publication_year": year,
                    "doi": doi,
                    "source_type": "peer_reviewed",
                    "is_high_authority": journal.lower() in HIGH_AUTHORITY_JOURNALS,
                    "_url": res.url,
                    "_retrieved_at": res.retrieved_at,
                }
            )
    return out


def fetch_publications_for(term: str, retmax: int = 30) -> list[dict[str, Any]]:
    pmids = search_pubmed(term, retmax=retmax)
    return fetch_pubmed_summaries(pmids) if pmids else []
