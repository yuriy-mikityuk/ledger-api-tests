import requests


def test_schema_is_served(api):
    schema = 'api/schema/'
    r = api.get(schema)
    assert r.status_code == 200
    assert "vnd.oai.openapi" in r.headers["Content-Type"]
    assert "/api/v1/accounts/" in r.text


