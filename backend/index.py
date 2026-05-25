from app.indexer.fts_index import SearchIndex


def create_index():
    return SearchIndex()


WorkspaceIndex = SearchIndex

