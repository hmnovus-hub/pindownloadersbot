from instagram_downloader import find_instagram_url, is_instagram_url


def test_instagram_post_and_reel_urls() -> None:
    assert is_instagram_url("https://www.instagram.com/reel/Dce1GFev4Wc/")
    assert is_instagram_url("https://instagram.com/p/DcgsPR2juJZ/?utm_source=test")


def test_rejects_profiles_and_deceptive_hosts() -> None:
    assert not is_instagram_url("https://instagram.com/example/")
    assert not is_instagram_url("https://instagram.com.evil.example/reel/abc/")


def test_finds_instagram_url_in_text() -> None:
    assert find_instagram_url("вот https://instagram.com/reels/abc_123/!") == (
        "https://instagram.com/reels/abc_123/"
    )
