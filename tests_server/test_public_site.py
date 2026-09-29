"""Introduction/docs are public, while query authorization remains required."""


def test_public_site_without_authentication(api):
    for path, expected in (
        ("/about/", "回到原文"),
        ("/about/data.html", "lawtext/legal-data-docx"),
        ("/about/mcp.html", "YOUR_PERSONAL_TOKEN"),
    ):
        response = api.get(path)
        assert response.status_code == 200
        assert expected in response.text
    assert api.get("/about/site.css").status_code == 200
    assert api.get("/api/v1/search", params={"q": "公开"}).status_code == 401
    assert api.get("/about/library.db").status_code == 404
