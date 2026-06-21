import pytest
from synepd.web.server import (
    get_db_info,
    get_taxonomy,
    search_reactions,
    query_epd,
    EPDQueryRequest,
)


def test_db_info_endpoint():
    data = get_db_info()
    assert "version" in data
    assert "backend" in data


def test_taxonomy_endpoint():
    data = get_taxonomy()
    assert "taxonomy" in data

    def find_reaction(nodes):
        for node in nodes:
            if node["reactions"]:
                assert "name" in node["reactions"][0]
                return True
            if node["children"]:
                if find_reaction(node["children"]):
                    return True
        return False

    assert find_reaction(data["taxonomy"])


def test_search_endpoint():
    data = search_reactions(query="POLAR")
    assert isinstance(data, list)
    assert len(data) > 0
    assert "name" in data[0]


def test_query_epd_endpoint():
    req = EPDQueryRequest(rsmi="CC[O-].[NH4+]>>CCO")
    data = query_epd(req)
    assert "success" in data
    assert data["success"] is True
    # CC[O-].[NH4+]>>CCO matches polar06_699 exactly
    assert "name" in data
    assert data["name"] == "Alcohol protonation deprotonation"
