from typing import Protocol

from app.services.drug_identity import search_with_similarity


class DrugSearchProvider(Protocol):
    def search(self, client, query: str) -> dict: ...


class OfficialTextSearch:
    def search(self, client, query):
        from app.medicare.drug_search import search_drug

        return search_with_similarity(client, query, search_drug)


# Future vector implementations must return official item_seq-backed candidates.
# They must preserve errors/truncated metadata and must not confirm identity by score.
