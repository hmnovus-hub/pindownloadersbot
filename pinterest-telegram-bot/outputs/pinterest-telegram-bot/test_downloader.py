from pathlib import Path

from downloader import (
    _best_video_url,
    _extract_meta,
    _media_type_for_file,
    find_pinterest_url,
    is_pinterest_url,
)


def test_valid_pinterest_urls() -> None:
    assert is_pinterest_url("https://www.pinterest.com/pin/123/")
    assert is_pinterest_url("https://pin.it/AbCd12")
    assert is_pinterest_url("https://ru.pinterest.com/pin/123/")


def test_rejects_deceptive_hosts() -> None:
    assert not is_pinterest_url("https://pinterest.com.evil.example/pin/123")
    assert not is_pinterest_url("https://evilpinterest.com/pin/123")
    assert not is_pinterest_url("javascript:alert(1)")


def test_find_url_in_message() -> None:
    assert find_pinterest_url("смотри: https://pin.it/AbCd12!") == "https://pin.it/AbCd12"


def test_extract_open_graph_metadata() -> None:
    page = """
    <html><head>
      <meta property="og:video:secure_url" content="https://v1.pinimg.com/video.mp4">
      <meta property="og:image" content="https://i.pinimg.com/originals/a.jpg">
      <meta property="og:title" content="Example &amp; Pin">
    </head></html>
    """
    video, image, title = _extract_meta(page)
    assert video == "https://v1.pinimg.com/video.mp4"
    assert image == "https://i.pinimg.com/originals/a.jpg"
    assert title == "Example & Pin"


def test_mp4_is_always_detected_as_video() -> None:
    assert _media_type_for_file(Path("pinterest.mp4"), {}) == "video"
    assert _media_type_for_file(Path("pinterest.jpg"), {"vcodec": "none"}) == "photo"


def test_finds_nested_story_pin_video() -> None:
    data = {
        "story_pin_data": {
            "pages": [{"blocks": [{"video": {"video_list": {
                "mobile": {"url": "https://v1.pinimg.com/video.m3u8", "width": 720, "height": 1280}
            }}}]}]
        }
    }
    assert _best_video_url(data) == "https://v1.pinimg.com/video.m3u8"
