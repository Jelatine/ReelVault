import unittest
from urllib.parse import parse_qs, urlsplit

from deploy.cleanup_test_image import PACKAGE, TARGET, Registry, candidate, cleanup


def version(identifier=1, tags=None):
    return {
        "id": identifier,
        "name": f"sha256:{identifier}",
        "metadata": {"container": {"tags": [TARGET] if tags is None else tags}},
    }


class FakeRegistry(Registry):
    def __init__(self, *, fresh=None):
        self.active = [version(), version(2, ["latest", "0.2", "0.2.4"]), version(3, [])]
        self.fresh = fresh
        self.deleted = []

    def versions(self):
        return list(self.active)

    def request(self, path, method="GET"):
        if method == "DELETE":
            assert path == PACKAGE + "/1"
            self.deleted.append(path)
            self.active = [v for v in self.active if v["id"] != 1]
            return None
        return self.fresh if self.fresh is not None else self.active[0]


class CleanupTest(unittest.TestCase):
    def test_dry_run_and_missing_tag_never_delete(self):
        registry = FakeRegistry()
        self.assertEqual(cleanup(registry)["status"], "dry_run")
        self.assertEqual(registry.deleted, [])
        registry.active = registry.active[1:]
        self.assertEqual(cleanup(registry, delete=True)["status"], "already_absent")
        self.assertEqual(registry.deleted, [])

    def test_deletion_is_exact_and_preserves_stable_tags_and_untagged_manifests(self):
        registry = FakeRegistry()
        report = cleanup(registry, delete=True)
        self.assertEqual(report["status"], "deleted_and_verified")
        self.assertEqual(registry.deleted, [PACKAGE + "/1"])
        self.assertEqual(registry.active, [version(2, ["latest", "0.2", "0.2.4"]), version(3, [])])

    def test_aliases_ambiguous_targets_and_invalid_ids_refused(self):
        for versions in (
            [version(tags=[TARGET, "latest"])],
            [version(), version(2)],
            [version(True)],
        ):
            with self.subTest(versions=versions), self.assertRaises(RuntimeError):
                candidate(versions)

    def test_new_alias_or_changed_digest_before_delete_is_refused(self):
        for fresh in (version(tags=[TARGET, "0.1"]), version(2), version(tags=[])):
            registry = FakeRegistry(fresh=fresh)
            with self.subTest(fresh=fresh), self.assertRaises(RuntimeError):
                cleanup(registry, delete=True)
            self.assertEqual(registry.deleted, [])

    def test_registry_pagination_does_not_assume_first_page_contains_target(self):
        registry = Registry("unused")
        pages = []

        def read(path, method="GET"):
            pages.append(path)
            page = parse_qs(urlsplit(path).query)["page"][0]
            return [version(i, []) for i in range(2, 102)] if page == "1" else [version()]

        registry.request = read
        self.assertEqual(candidate(registry.versions())["id"], 1)
        self.assertEqual(len(pages), 2)


if __name__ == "__main__":
    unittest.main()
