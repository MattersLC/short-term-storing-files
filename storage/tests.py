import time
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, override_settings

from config.redis_client import client

from .views import redis_key

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
        self.assertEqual(stored[b"file_1_name"], b"file1.bin")
        self.assertEqual(stored[b"file_1_content"], b"hello \x00\xff binary")
        self.assertEqual(stored[b"file_2_name"], b"file2.txt")
        self.assertEqual(stored[b"file_2_content"], b"second file")
        self.assertEqual(stored[b"text"].decode(), "Texto de prueba")

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
