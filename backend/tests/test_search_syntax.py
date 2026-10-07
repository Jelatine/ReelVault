import pytest
from fastapi.testclient import TestClient

from reelvault.models import Video
from reelvault.search_syntax import parse_search

from .test_organization import seed_video
from .test_search import result_ids


def test_conditions_intersect_keywords_filters_saved_search_and_live_tags(
    client: TestClient,
) -> None:
    target = seed_video(client, "2024 旅行")
    low = seed_video(client, "2024 旅行")
    short = seed_video(client, "2024 旅行")
    other = seed_video(client, "2023 旅行")
    for video_id in (target, low, short, other):
        client.patch(f"/api/videos/{video_id}", json={"tags": ["旅行", "家庭 旅行", "精选"]})
    with client.app.state.sessionmaker() as db:
        for video_id, rating, duration in (
            (target, 4, 601),
            (low, 3, 601),
            (short, 5, 600),
            (other, 5, 900),
        ):
            video = db.get(Video, video_id)
            video.rating, video.duration = rating, duration
        db.commit()
    query = "tag:旅行 rating:>=4 duration:>10m 2024"
    assert result_ids(client, q=query) == [target]
    page = client.get("/api/videos", params={"q": query, "page_size": 1}).json()
    assert page["total"] == 1 and page["search_terms"] == ["2024"]
    assert "2024" in page["items"][0]["search_excerpt"]
    assert result_ids(client, q=query, duration_max=600) == []
    assert result_ids(client, q='TAG:"家庭 旅行" tag:精选 rating:=4') == [target]
    assert result_ids(client, q="rating:>=4 rating:<=4") == [target]
    assert result_ids(client, q="duration:>=10.016m duration:<0.167h rating:4") == [target]
    assert result_ids(client, q="tag:不存在") == []
    saved = client.post("/api/smart-folders", json={"name": "语法条件", "filters": {"q": query}})
    assert saved.status_code == 200, saved.text
    url = f"/api/smart-folders/{saved.json()['id']}/videos"
    assert [v["id"] for v in client.get(url).json()["items"]] == [target]
    client.patch(f"/api/videos/{target}", json={"tags": ["精选"]})
    assert client.get(url).json()["items"] == []
    client.patch(f"/api/videos/{target}", json={"tags": ["旅行"]})
    client.delete(f"/api/videos/{target}")
    assert result_ids(client, q=query) == []
    assert result_ids(client, q=query, trash=True) == [target]


@pytest.mark.parametrize(
    "query",
    [
        "tag:",
        'tag:"   "',
        'tag:"unclosed',
        'tag:"name"suffix',
        "tag:" + "长" * 65,
        "rating:6",
        "rating:-1",
        "rating:1.5",
        "rating:4s",
        "rating:NaN",
        "rating:>=",
        "duration:-1m",
        "duration:10ms",
        "duration:NaN",
        "duration:inf",
        "duration:1e3",
        "duration:=>1",
        "duration:" + "9" * 5000,
        "rating:" + "9" * 5000,
    ],
)
def test_invalid_known_conditions_fail_closed_and_cannot_be_saved(
    client: TestClient, query: str
) -> None:
    seed_video(client)
    response = client.get("/api/videos", params={"q": query})
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "search_syntax_invalid"
    assert len(response.json()["params"]["token"]) <= 160
    # Saved queries have a separate length bound; shorter syntax errors use the same parser.
    if len(query) <= 512:
        saved = client.post("/api/smart-folders", json={"name": "无效", "filters": {"q": query}})
        assert saved.status_code == 400 and saved.json()["code"] == "search_syntax_invalid"


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("rating:>3", True),
        ("rating:>=4", True),
        ("rating:<4", False),
        ("rating:<=4", True),
        ("rating:4", True),
        ("rating:=3", False),
        ("duration:>2m", False),
        ("duration:>=2m", True),
        ("duration:<121", True),
        ("duration:<=120s", True),
        ("duration:2M", True),
        ("duration:=0.5h", False),
    ],
)
def test_numeric_boundaries_units_and_no_keyword_excerpts(
    client: TestClient, condition: str, expected: bool
) -> None:
    video_id = seed_video(client)
    client.patch(f"/api/videos/{video_id}", json={"rating": 4})
    response = client.get("/api/videos", params={"q": condition}).json()
    assert response["search_terms"] == []
    assert response["total"] == int(expected)
    if expected:
        assert "search_excerpt" not in response["items"][0]


def test_tag_escaping_literal_unknown_prefixes_and_sql_characters(client: TestClient) -> None:
    target = seed_video(client, "url:https://example.org 2024")
    name = '旅行 "A" \\ path %_ OR 1=1'
    client.patch(f"/api/videos/{target}", json={"tags": [name]})
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    assert result_ids(client, q=f'tag:"{escaped}"') == [target]
    assert result_ids(client, q="url:https://example.org") == [target]
    assert parse_search('" rating:4 2024').terms == ['"', "2024"]
    assert result_ids(client, q="tag:旅行%_") == []


def test_saved_tag_conditions_follow_rename_merge_and_keep_deleted_rule(client: TestClient) -> None:
    target = seed_video(client, "旅行")
    client.patch(f"/api/videos/{target}", json={"tags": ["旅行", "目的地"]})
    query = '旅行  TAG:旅行 tag:"旅行" rating:0'
    saved = client.post(
        "/api/smart-folders", json={"name": "标签语法", "filters": {"q": query}}
    ).json()
    tags = {t["name"]: t["id"] for t in client.get("/api/tags").json()}
    new_name = '家庭 "旅行" \\ 路线'
    renamed = client.patch(f"/api/tags/{tags['旅行']}", json={"name": new_name})
    assert renamed.status_code == 200
    folders = client.get("/api/smart-folders").json()
    updated = folders[0]["filters"]["q"]
    assert updated.startswith("旅行  tag:")  # Literal keyword and whitespace are retained.
    assert [s[2] for s in parse_search(updated).tag_spans] == [new_name, new_name]
    url = f"/api/smart-folders/{saved['id']}/videos"
    assert client.get(url).json()["total"] == 1
    assert (
        client.post(
            f"/api/tags/{tags['旅行']}/merge", json={"target_id": tags["目的地"]}
        ).status_code
        == 200
    )
    assert client.get(url).json()["total"] == 1
    client.delete(f"/api/tags/{tags['目的地']}")
    assert client.get(url).json()["total"] == 0


def test_renaming_can_expand_stored_queries_without_breaking_read_validation(
    client: TestClient,
) -> None:
    target = seed_video(client)
    client.patch(f"/api/videos/{target}", json={"tags": ["a"]})
    saved = client.post(
        "/api/smart-folders", json={"name": "扩展规则", "filters": {"q": " ".join(["tag:a"] * 80)}}
    ).json()
    tag_id = client.get("/api/tags").json()[0]["id"]
    assert client.patch(f"/api/tags/{tag_id}", json={"name": "新" * 64}).status_code == 200
    response = client.get(f"/api/smart-folders/{saved['id']}/videos")
    assert response.status_code == 200 and response.json()["total"] == 1
    assert (
        client.post(
            "/api/smart-folders", json={"name": "过长", "filters": {"q": "a" * 513}}
        ).status_code
        == 422
    )
