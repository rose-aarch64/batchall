from django.db import connection
from django.test import TestCase

from batchall import accountsdb


class AccountsDbContractTests(TestCase):
    def test_lookup_works_against_a_real_django_accounts_database(self):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA table_info(auth_user)")
            columns = {row[1] for row in cursor.fetchall()}
        self.assertTrue({"id", "username", "is_active", "is_superuser"} <= columns)

        from django.contrib.auth import get_user_model

        User = get_user_model()
        admin = User.objects.create_superuser("Admin", password="correct horse battery")
        User.objects.create_user("bob", password="correct horse battery")
        with connection.cursor() as cursor:
            cursor.execute(accountsdb.BY_HANDLE_SQL, ["admin"])
            self.assertEqual(cursor.fetchone(), (admin.id,))
            cursor.execute(accountsdb.FIRST_ADMIN_SQL)
            self.assertEqual(cursor.fetchone(), (admin.id,))
