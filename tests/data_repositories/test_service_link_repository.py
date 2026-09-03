"""The link store: per-(service, person) credentials, against a real SQLite file.

The isolation properties are the point. A composite key means one person's link
to one service says nothing about anyone else's, and two platforms that happen
to issue the same numeric id are different people — which is exactly what the
namespaced ``end_user_id`` buys and what a bare id would lose silently.
"""
from application.data_repositories.service_link_repository import ServiceLinkRepository


def _repo(tmp_path):
    return ServiceLinkRepository(str(tmp_path / "agent.db"))


def test_put_then_get_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "token-a")
    assert repo.get("invintiry", "telegram:1") == "token-a"


def test_unlinked_is_none_not_an_error(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get("invintiry", "telegram:404") is None
    assert repo.credentials("telegram:404") == {}


def test_relinking_replaces_rather_than_duplicates(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "old")
    repo.put("invintiry", "telegram:1", "new")
    assert repo.get("invintiry", "telegram:1") == "new"
    assert repo.credentials("telegram:1") == {"invintiry": "new"}


def test_two_people_never_share_a_credential(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "token-a")
    repo.put("invintiry", "telegram:2", "token-b")
    assert repo.get("invintiry", "telegram:1") == "token-a"
    assert repo.get("invintiry", "telegram:2") == "token-b"


def test_same_numeric_id_on_two_platforms_is_two_people(tmp_path):
    # The reason end_user_id is namespaced: a bare 12345 would merge these.
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:12345", "telegram-token")
    repo.put("invintiry", "whatsapp:12345", "whatsapp-token")
    assert repo.get("invintiry", "telegram:12345") == "telegram-token"
    assert repo.get("invintiry", "whatsapp:12345") == "whatsapp-token"


def test_credentials_returns_every_service_for_one_person(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "token-inv")
    repo.put("other", "telegram:1", "token-other")
    repo.put("invintiry", "telegram:2", "someone-else")
    assert repo.credentials("telegram:1") == {
        "invintiry": "token-inv",
        "other": "token-other",
    }


def test_delete_reports_whether_it_removed_anything(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "token-a")
    assert repo.delete("invintiry", "telegram:1") is True
    assert repo.delete("invintiry", "telegram:1") is False  # already gone


def test_delete_all_drops_only_that_person(tmp_path):
    repo = _repo(tmp_path)
    repo.put("invintiry", "telegram:1", "a")
    repo.put("other", "telegram:1", "b")
    repo.put("invintiry", "telegram:2", "untouched")

    dropped = repo.delete_all("telegram:1")

    assert dropped == ["invintiry", "other"]  # sorted, and it names what it dropped
    assert repo.credentials("telegram:1") == {}
    assert repo.get("invintiry", "telegram:2") == "untouched"


def test_delete_all_on_an_unlinked_person_is_empty_not_an_error(tmp_path):
    repo = _repo(tmp_path)
    assert repo.delete_all("telegram:404") == []


def test_survives_reopening_the_same_file(tmp_path):
    # The store outlives the process: a restart must not silently unlink anyone.
    first = ServiceLinkRepository(str(tmp_path / "agent.db"))
    first.put("invintiry", "telegram:1", "token-a")
    first.close()

    second = ServiceLinkRepository(str(tmp_path / "agent.db"))
    assert second.get("invintiry", "telegram:1") == "token-a"
