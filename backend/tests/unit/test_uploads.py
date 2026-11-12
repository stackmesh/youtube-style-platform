import io

from PIL import Image

from app.core.exceptions import AppError
from app.uploads.images import jpeg_bytes
from app.uploads.objects import presign_part
from app.uploads.service import normalize_etag, part_count_for, part_length


def test_part_geometry_matches_the_fixed_part_size() -> None:
    part_size = 16_777_216
    size = part_size + 25
    count = part_count_for(size, part_size)
    assert count == 2
    assert part_length(size, part_size, count, 1) == part_size
    assert part_length(size, part_size, count, 2) == 25
    assert part_count_for(100, part_size) == 1
    assert part_length(100, part_size, 1, 1) == 100


def test_etag_comparison_ignores_quotes_and_case() -> None:
    assert normalize_etag('"AbC"') == normalize_etag("abc")


def test_presigned_part_url_binds_content_length_and_hides_the_secret() -> None:
    url = presign_part("originals/x/source", "upload-1", 1, 1024)
    assert "X-Amz-SignedHeaders=content-length%3Bhost" in url
    assert "media-app-local-secret" not in url
    assert "originals/x/source" in url


def test_non_image_thumbnail_is_rejected() -> None:
    try:
        jpeg_bytes(b"not an image")
    except AppError as exc:
        assert exc.status_code == 422
        assert exc.code == "VALIDATION_ERROR"
    else:
        raise AssertionError("expected a validation error")


def test_png_thumbnail_is_reencoded_without_exif() -> None:
    source = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(source, format="PNG")
    encoded = jpeg_bytes(source.getvalue())
    assert encoded.startswith(b"\xff\xd8")
    assert b"Exif" not in encoded
    with Image.open(io.BytesIO(encoded)) as image:
        assert image.format == "JPEG"
        assert image.size == (8, 8)
