import pytest
from PIL import Image

from framework.integration import image_output_worker as worker


def test_third_party_png_is_byte_identical_with_transparency_and_metadata(tmp_path):
    from PIL.PngImagePlugin import PngInfo

    source, target = tmp_path / "source.png", tmp_path / "output.png"
    metadata = PngInfo()
    metadata.add_text("Author", "Original generator")
    Image.new("RGBA", (320, 200), (12, 34, 56, 128)).save(source, pnginfo=metadata)
    worker.prepare_image(source, target)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("size,expected", [((1000, 200), (500, 100)), ((400, 400), (250, 250))])
def test_large_output_only_scales_to_transport_limits(tmp_path, monkeypatch, size, expected):
    source, target = tmp_path / "source.png", tmp_path / "output.png"
    Image.new("RGB", size, "red").save(source)
    monkeypatch.setattr(worker, "MAX_OUTPUT_EDGE", 500)
    monkeypatch.setattr(worker, "MAX_OUTPUT_PIXELS", 62500)
    worker.prepare_image(source, target)
    with Image.open(target) as image:
        assert image.size == expected
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((image.width - 1, image.height - 1)) == (255, 0, 0)


def test_oversized_image_is_rejected_before_decoding(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (128, 128)).save(source)
    monkeypatch.setattr(worker, "MAX_RENDER_PIXELS", 1000)
    with pytest.raises(ValueError, match="oversized"):
        worker.prepare_image(source, tmp_path / "output.png")


def test_non_png_is_not_forwarded(tmp_path):
    source = tmp_path / "wrong.png"
    Image.new("RGB", (128, 128)).save(source, format="JPEG")
    with pytest.raises(ValueError, match="Invalid"):
        worker.prepare_image(source, tmp_path / "output.png")
