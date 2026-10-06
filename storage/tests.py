import time
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

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
