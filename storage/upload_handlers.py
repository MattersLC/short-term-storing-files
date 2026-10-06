from io import BytesIO

from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler


class BoundedMemoryUploadHandler(FileUploadHandler):
    """Keeps uploads in memory only (never on disk).

    Stores at most MAX_UPLOAD_SIZE_BYTES + 1 bytes per file, so memory stays
    bounded, while still reporting the real received size. The view uses that
    size to reject oversized files with 413.
    """

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.file = BytesIO()
        self.received = 0
        self.max_stored = settings.MAX_UPLOAD_SIZE_BYTES + 1

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        remaining = self.max_stored - self.file.tell()
        if remaining > 0:
            self.file.write(raw_data[:remaining])
        return None

    def file_complete(self, file_size):
        self.file.seek(0)
        return InMemoryUploadedFile(
            file=self.file,
            field_name=self.field_name,
            name=self.file_name,
            content_type=self.content_type,
            size=self.received,
            charset=self.charset,
            content_type_extra=self.content_type_extra,
        )
