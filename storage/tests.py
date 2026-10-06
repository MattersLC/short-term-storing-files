import base64
import time
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from config.redis_client import client

from . import crypto
from .views import decrypt_field, record_aad, redis_key

URL = "/api/storage/"


class StorageTests(SimpleTestCase):
    def setUp(self):
        self.created_keys = []

    def tearDown(self):
        if self.created_keys:
            client.delete(*self.created_keys)

    def payload(self, file_1=b"hello \x00\xff binary", file_2=b"second file", text="Texto de prueba"):
        return {
            "file_1": SimpleUploadedFile("file1.bin", file_1),
            "file_2": SimpleUploadedFile("file2.txt", file_2),
            "text": text,
        }

    def post(self, data):
        response = self.client.post(URL, data)
        if response.status_code == 201:
            self.created_keys.append(redis_key(response.json()["id"]))
        return response

    @override_settings(REDIS_TTL_SECONDS=120)
    def test_post_stores_data(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["ttl_seconds"], 120)
        uuid.UUID(body["id"], version=4)

        stored = client.hgetall(redis_key(body["id"]))
        self.assertEqual(
            set(stored),
            {
                b"file_1_name", b"file_1_ciphertext", b"file_1_nonce",
                b"file_2_name", b"file_2_ciphertext", b"file_2_nonce",
                b"text_ciphertext", b"text_nonce", b"encryption_version",
            },
        )
        self.assertEqual(stored[b"file_1_name"], b"file1.bin")
        self.assertEqual(stored[b"file_2_name"], b"file2.txt")
        self.assertEqual(stored[b"encryption_version"], b"v1")

    def test_missing_fields_returns_400(self):
        response = self.post({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(sorted(response.json()["fields"]), ["file_1", "file_2", "text"])

        data = self.payload(text="   ")
        response = self.post(data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["fields"], ["text"])

    @override_settings(MAX_UPLOAD_SIZE_BYTES=10)
    def test_file_too_large_returns_413(self):
        response = self.post(self.payload(file_2=b"x" * 11, file_1=b"ok"))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["fields"], ["file_2"])

    def test_get_existing_record(self):
        record_id = self.post(self.payload()).json()["id"]
        response = self.client.get(f"{URL}{record_id}/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], record_id)
        self.assertTrue(body["exists"])
        self.assertEqual(body["file_1_name"], "file1.bin")
        self.assertEqual(body["file_2_name"], "file2.txt")
        self.assertEqual(body["text"], "Texto de prueba")
        self.assertNotIn("file_1_content", body)
        self.assertGreater(body["ttl_remaining_seconds"], 0)

    def test_get_missing_record_returns_404(self):
        record_id = str(uuid.uuid4())
        response = self.client.get(f"{URL}{record_id}/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"id": record_id, "exists": False})

        response = self.client.get(f"{URL}not-a-uuid/")
        self.assertEqual(response.status_code, 404)

    @override_settings(REDIS_TTL_SECONDS=60)
    def test_redis_key_has_ttl(self):
        record_id = self.post(self.payload()).json()["id"]
        ttl = client.ttl(redis_key(record_id))
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, 60)

    @override_settings(REDIS_TTL_SECONDS=1)
    def test_record_expires_automatically(self):
        record_id = self.post(self.payload()).json()["id"]
        self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 200)
        time.sleep(1.5)
        self.assertFalse(client.exists(redis_key(record_id)))
        self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 404)
        # Touch must not bring expired data back.
        self.assertEqual(self.client.post(f"{URL}{record_id}/touch/").status_code, 404)
        self.assertFalse(client.exists(redis_key(record_id)))

    @override_settings(REDIS_TTL_SECONDS=60)
    def test_touch_renews_ttl(self):
        record_id = self.post(self.payload()).json()["id"]
        client.expire(redis_key(record_id), 5)

        response = self.client.post(f"{URL}{record_id}/touch/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], record_id)
        self.assertTrue(body["touched"])
        self.assertEqual(body["ttl_seconds"], 60)
        self.assertGreater(body["ttl_remaining_seconds"], 55)
        self.assertGreater(client.ttl(redis_key(record_id)), 55)

    def test_touch_missing_record_returns_404(self):
        record_id = str(uuid.uuid4())
        response = self.client.post(f"{URL}{record_id}/touch/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"id": record_id, "exists": False})
        self.assertFalse(client.exists(redis_key(record_id)))

    def test_delete_removes_record(self):
        record_id = self.post(self.payload()).json()["id"]

        response = self.client.delete(f"{URL}{record_id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": record_id, "deleted": True})
        self.assertFalse(client.exists(redis_key(record_id)))

        self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 404)
        self.assertEqual(self.client.delete(f"{URL}{record_id}/").status_code, 404)

    def test_delete_missing_record_returns_404(self):
        response = self.client.delete(f"{URL}{uuid.uuid4()}/")
        self.assertEqual(response.status_code, 404)


class EncryptionTests(SimpleTestCase):
    FILE_1 = b"SUPER_SECRET_FILE_ONE \x00\xff"
    FILE_2 = b"SUPER_SECRET_FILE_TWO"
    TEXT = "SUPER_SECRET_TEXT con acentos: áéí"

    def setUp(self):
        self.created_keys = []

    def tearDown(self):
        if self.created_keys:
            client.delete(*self.created_keys)

    def store(self):
        response = self.client.post(
            URL,
            {
                "file_1": SimpleUploadedFile("file1.bin", self.FILE_1),
                "file_2": SimpleUploadedFile("file2.txt", self.FILE_2),
                "text": self.TEXT,
            },
        )
        self.assertEqual(response.status_code, 201)
        record_id = response.json()["id"]
        self.created_keys.append(redis_key(record_id))
        return record_id

    def stored(self, record_id):
        return client.hgetall(redis_key(record_id))

    def decrypt(self, record_id, field, stored=None):
        stored = stored if stored is not None else self.stored(record_id)
        return decrypt_field(
            record_id, field,
            stored.get(f"{field}_ciphertext".encode()),
            stored.get(f"{field}_nonce".encode()),
            stored.get(b"encryption_version"),
        )

    # Case 1
    def test_redis_holds_ciphertext_not_plaintext(self):
        record_id = self.store()
        stored = self.stored(record_id)
        blob = b"".join(stored.values())
        for secret in (self.FILE_1, self.FILE_2, self.TEXT.encode(), b"SUPER_SECRET"):
            self.assertNotIn(secret, blob)
        for field in ("file_1", "file_2", "text"):
            self.assertEqual(len(stored[f"{field}_nonce".encode()]), 12)
        # GCM ciphertext = plaintext length + 16-byte tag.
        self.assertEqual(len(stored[b"file_1_ciphertext"]), len(self.FILE_1) + 16)

    # Case 2
    def test_stored_values_decrypt_to_original(self):
        record_id = self.store()
        self.assertEqual(self.decrypt(record_id, "file_1"), self.FILE_1)
        self.assertEqual(self.decrypt(record_id, "file_2"), self.FILE_2)
        self.assertEqual(self.decrypt(record_id, "text").decode(), self.TEXT)
        body = self.client.get(f"{URL}{record_id}/").json()
        self.assertEqual(body["text"], self.TEXT)

    # Case 3
    def test_same_input_produces_different_ciphertext(self):
        first, second = self.stored(self.store()), self.stored(self.store())
        for field in ("file_1", "file_2", "text"):
            self.assertNotEqual(first[f"{field}_nonce".encode()], second[f"{field}_nonce".encode()])
            self.assertNotEqual(
                first[f"{field}_ciphertext".encode()], second[f"{field}_ciphertext".encode()]
            )

    # Case 4
    def test_tampered_ciphertext_fails_authentication(self):
        record_id = self.store()
        key = redis_key(record_id)
        for field in ("file_1", "file_2", "text"):
            stored = self.stored(record_id)
            tampered = bytearray(stored[f"{field}_ciphertext".encode()])
            tampered[0] ^= 0x01
            stored[f"{field}_ciphertext".encode()] = bytes(tampered)
            with self.assertRaises(crypto.DecryptionError):
                self.decrypt(record_id, field, stored)

        stored = self.stored(record_id)
        tampered = bytearray(stored[b"text_ciphertext"])
        tampered[-1] ^= 0x01  # flip a bit in the GCM tag
        client.hset(key, "text_ciphertext", bytes(tampered))
        with self.assertLogs("storage.views", level="ERROR") as logs:
            response = self.client.get(f"{URL}{record_id}/")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "storage_integrity_error")
        self.assertNotIn("SUPER_SECRET", response.content.decode())
        self.assertIn("Encrypted storage payload authentication failed", logs.output[0])
        self.assertNotIn("SUPER_SECRET", logs.output[0])

    def test_wrong_nonce_fails_authentication(self):
        record_id = self.store()
        stored = self.stored(record_id)
        stored[b"text_nonce"] = stored[b"file_1_nonce"]
        with self.assertRaises(crypto.DecryptionError):
            self.decrypt(record_id, "text", stored)

    # Case 5
    def test_aad_of_another_id_fails_authentication(self):
        record_id, other_id = self.store(), self.store()
        stored = self.stored(record_id)
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(stored[b"text_ciphertext"], stored[b"text_nonce"], record_aad(other_id, "text"))

        # Copying a record's ciphertext into another record is detected on GET.
        client.hset(
            redis_key(other_id),
            mapping={"text_ciphertext": stored[b"text_ciphertext"], "text_nonce": stored[b"text_nonce"]},
        )
        with self.assertLogs("storage.views", level="ERROR"):
            self.assertEqual(self.client.get(f"{URL}{other_id}/").status_code, 500)

    def test_swapping_fields_fails_authentication(self):
        record_id = self.store()
        stored = self.stored(record_id)
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(stored[b"file_1_ciphertext"], stored[b"file_1_nonce"], record_aad(record_id, "file_2"))

    def test_unknown_encryption_version_is_rejected(self):
        record_id = self.store()
        client.hset(redis_key(record_id), "encryption_version", "v0")
        with self.assertLogs("storage.views", level="ERROR"):
            self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 500)

    # Case 6
    @override_settings(REDIS_TTL_SECONDS=1)
    def test_encrypted_record_expires(self):
        record_id = self.store()
        self.assertGreater(client.ttl(redis_key(record_id)), 0)
        self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 200)
        time.sleep(1.5)
        self.assertFalse(client.exists(redis_key(record_id)))
        self.assertEqual(self.client.get(f"{URL}{record_id}/").status_code, 404)

    # Case 7
    @override_settings(REDIS_TTL_SECONDS=60)
    def test_touch_renews_ttl_without_changing_ciphertext(self):
        record_id = self.store()
        before = self.stored(record_id)
        client.expire(redis_key(record_id), 5)
        self.assertEqual(self.client.post(f"{URL}{record_id}/touch/").status_code, 200)
        self.assertGreater(client.ttl(redis_key(record_id)), 55)
        self.assertEqual(self.stored(record_id), before)

    # Case 8
    def test_delete_removes_encrypted_record(self):
        record_id = self.store()
        self.assertEqual(self.client.delete(f"{URL}{record_id}/").status_code, 200)
        self.assertFalse(client.exists(redis_key(record_id)))
        self.assertEqual(self.stored(record_id), {})


class EncryptionKeyTests(SimpleTestCase):
    def test_valid_key_decodes_to_32_bytes(self):
        key = bytes(range(32))
        self.assertEqual(crypto.decode_key(base64.b64encode(key).decode()), key)

    def test_missing_key_is_rejected(self):
        for value in (None, "", "   "):
            with self.assertRaisesMessage(ImproperlyConfigured, "STORAGE_ENCRYPTION_KEY is not set"):
                crypto.decode_key(value)

    def test_invalid_base64_is_rejected(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "not valid Base64"):
            crypto.decode_key("REPLACE_WITH_BASE64_32_BYTE_KEY")

    def test_wrong_length_is_rejected(self):
        for size in (16, 24, 31, 33, 64):
            with self.assertRaisesMessage(ImproperlyConfigured, "exactly 32 bytes"):
                crypto.decode_key(base64.b64encode(b"k" * size).decode())


@override_settings(REDIS_TTL_SECONDS=42, MAX_UPLOAD_SIZE_BYTES=1000)
class ConfigTests(SimpleTestCase):
    def test_config_exposes_only_public_settings(self):
        response = self.client.get("/api/config/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"redis_ttl_seconds": 42, "max_upload_size_bytes": 1000})


class IndexPageTests(SimpleTestCase):
    def test_index_returns_200(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Short-term Redis Storage PoC")

    def test_index_contains_expected_form(self):
        response = self.client.get("/")
        self.assertContains(response, 'id="store-form"')
        self.assertContains(response, 'name="file_1"')
        self.assertContains(response, 'name="file_2"')
        self.assertContains(response, 'name="text"')
        self.assertContains(response, "Store in Redis")
        self.assertContains(response, "Refresh status every second")
        self.assertContains(response, "Refresh TTL")
        self.assertContains(response, "Delete now")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn("csrftoken", response.cookies)


class CsrfTests(SimpleTestCase):
    def setUp(self):
        self.csrf_client = Client(enforce_csrf_checks=True)

    def payload(self):
        return {
            "file_1": SimpleUploadedFile("a.txt", b"a"),
            "file_2": SimpleUploadedFile("b.txt", b"b"),
            "text": "hola",
        }

    def test_post_without_csrf_token_is_rejected(self):
        response = self.csrf_client.post(URL, self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "csrf_failed")

    def test_post_with_csrf_token_from_page_succeeds(self):
        self.csrf_client.get("/")
        token = self.csrf_client.cookies["csrftoken"].value
        response = self.csrf_client.post(URL, self.payload(), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)
        client.delete(redis_key(response.json()["id"]))

    def test_touch_and_delete_without_csrf_token_are_rejected(self):
        record_id = uuid.uuid4()
        self.assertEqual(self.csrf_client.post(f"{URL}{record_id}/touch/").status_code, 403)
        self.assertEqual(self.csrf_client.delete(f"{URL}{record_id}/").status_code, 403)
