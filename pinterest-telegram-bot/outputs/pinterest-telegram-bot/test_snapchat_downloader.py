from snapchat_downloader import find_snapchat_url, is_snapchat_url


SPOTLIGHT = "https://www.snapchat.com/spotlight/W7_EDlXWTBiXAEEniNoMPwAAYaGN6anlyaWtmAaAQJOrDAaAQJOquAAAAAQ?ref=web_spotlight"


def test_spotlight_url_is_supported() -> None:
    assert is_snapchat_url(SPOTLIGHT)
    assert find_snapchat_url(f"смотри {SPOTLIGHT}") == SPOTLIGHT


def test_rejects_profiles_and_fake_domains() -> None:
    assert not is_snapchat_url("https://www.snapchat.com/add/example")
    assert not is_snapchat_url("https://snapchat.com.evil.example/spotlight/abc")

