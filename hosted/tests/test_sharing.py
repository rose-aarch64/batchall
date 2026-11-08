from django.test import TestCase

from hosted import accounts, sharing
from hosted.models import ShareLink

PASSWORD = "correct horse battery"


class ShareLinkTests(TestCase):
    def setUp(self):
        self.alice = accounts.create_user("alice", PASSWORD)

    def test_create_then_resolve(self):
        link = sharing.create_link(self.alice)
        self.assertEqual(sharing.resolve(link.token), self.alice)

    def test_new_link_replaces_old(self):
        old = sharing.create_link(self.alice)
        new = sharing.create_link(self.alice)
        self.assertIsNone(sharing.resolve(old.token))
        self.assertEqual(sharing.resolve(new.token), self.alice)
        self.assertEqual(ShareLink.objects.filter(user=self.alice).count(), 1)

    def test_revoke(self):
        link = sharing.create_link(self.alice)
        self.assertTrue(sharing.revoke_link(self.alice))
        self.assertIsNone(sharing.resolve(link.token))
        self.assertFalse(sharing.revoke_link(self.alice))

    def test_malformed_tokens_resolve_to_none(self):
        link = sharing.create_link(self.alice)
        pk = link.pk
        for bad in ["", ".", f"{pk}.", f".{link.secret}", f"x.{link.secret}", f"{pk}.wrong",
                    f"{pk}.{link.secret}x", "9" * 40 + ".abc", "é.ü", "a" * 5000,
                    f"{pk}.{link.secret}.extra"]:
            self.assertIsNone(sharing.resolve(bad), bad)

    def test_deleting_the_user_removes_the_link(self):
        link = sharing.create_link(self.alice)
        self.alice.delete()
        self.assertIsNone(sharing.resolve(link.token))

    def test_current_link_and_path(self):
        self.assertIsNone(sharing.current_link(self.alice))
        link = sharing.create_link(self.alice)
        self.assertEqual(sharing.current_link(self.alice), link)
        self.assertEqual(sharing.link_path(link), f"/s/{link.token}/")
