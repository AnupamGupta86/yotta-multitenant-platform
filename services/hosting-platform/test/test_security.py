import unittest

from src.security import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        first, second = hash_password("s3cret-pass"), hash_password("s3cret-pass")
        self.assertNotEqual(first, second)  # different salt each time
        self.assertTrue(first.startswith("scrypt$"))
        self.assertTrue(verify_password("s3cret-pass", first))
        self.assertFalse(verify_password("wrong-pass", first))

    def test_malformed_hashes_never_verify(self):
        for stored in ("", "plain-text", "md5$abc", "scrypt$x$y$z$a$b"):
            self.assertFalse(verify_password("anything", stored))
