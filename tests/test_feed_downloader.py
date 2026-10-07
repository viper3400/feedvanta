import importlib.util
import sys
from io import BytesIO
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "tools" / "feed_downloader.py"
SPEC = importlib.util.spec_from_file_location("feed_downloader", SCRIPT_PATH)
assert SPEC and SPEC.loader
feed_downloader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = feed_downloader
SPEC.loader.exec_module(feed_downloader)
MediaItem = feed_downloader.MediaItem


class Response(BytesIO):
    def __init__(self, content: bytes, url: str = "https://example.com/feed.xml"):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}
        self._url = url

    def geturl(self):
        return self._url


def test_rss_prefers_enclosure_and_falls_back_to_direct_video_link():
    xml = b"""<rss><channel>
      <item><title>Enclosure</title><guid>one</guid>
        <link>https://example.com/fallback.mp4</link>
        <enclosure url="https://cdn.example.com/preferred.webm" type="video/webm" />
      </item>
      <item><title>Fallback</title><guid>two</guid><link>https://cdn.example.com/video.mp4?token=x</link></item>
      <item><title>Article</title><link>https://example.com/article</link></item>
    </channel></rss>"""
    items, skipped = feed_downloader.parse_feed(xml, "https://example.com/feed.xml")
    assert [item.url for item in items] == [
        "https://cdn.example.com/preferred.webm",
        "https://cdn.example.com/video.mp4?token=x",
    ]
    assert skipped == 1


def test_atom_enclosure_and_local_feed_file(tmp_path: Path):
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
      <title>Atom Video</title><id>atom-1</id>
      <link rel="enclosure" href="media/video" type="video/mp4" />
    </entry></feed>"""
    source = tmp_path / "feed.xml"
    source.write_bytes(xml)
    loaded, base_url = feed_downloader.load_feed(str(source))
    items, skipped = feed_downloader.parse_feed(loaded, base_url)
    assert skipped == 0
    assert items[0].url == (tmp_path / "media/video").as_uri()
    assert items[0].extension == ".mp4"


def test_load_feed_from_url(monkeypatch):
    monkeypatch.setattr(
        feed_downloader, "urlopen",
        lambda request, timeout: Response(b"<rss><channel /></rss>", "https://redirected.example/feed.xml"),
    )
    content, base_url = feed_downloader.load_feed("https://example.com/feed.xml")
    assert content == b"<rss><channel /></rss>"
    assert base_url == "https://redirected.example/feed.xml"


def test_filename_is_safe_stable_and_collision_resistant():
    first = MediaItem("Zu Tisch: Z\u00fcrich / Folge 1", "abc123", "https://example.com/a.mp4", ".mp4")
    second = MediaItem("Zu Tisch: Z\u00fcrich / Folge 1", "def456", "https://example.com/b.mp4", ".mp4")
    assert feed_downloader.safe_filename(first) == "Zu-Tisch-Zurich-Folge-1-abc123.mp4"
    assert feed_downloader.safe_filename(first) != feed_downloader.safe_filename(second)


def test_download_overwrites_atomically(tmp_path: Path, monkeypatch):
    item = MediaItem("Video", "abc123", "https://example.com/video.mp4", ".mp4")
    target = tmp_path / feed_downloader.safe_filename(item)
    target.write_bytes(b"old")
    monkeypatch.setattr(feed_downloader, "urlopen", lambda request, timeout: Response(b"new-video"))
    _, result, error = feed_downloader.download_item(item, tmp_path)
    assert error is None
    assert result == target
    assert target.read_bytes() == b"new-video"
    assert not target.with_suffix(".mp4.part").exists()


def test_failed_download_retries_three_times_and_keeps_existing_file(tmp_path: Path, monkeypatch):
    item = MediaItem("Video", "abc123", "https://example.com/video.mp4", ".mp4")
    target = tmp_path / feed_downloader.safe_filename(item)
    target.write_bytes(b"old")
    calls = 0

    def fail(request, timeout):
        nonlocal calls
        calls += 1
        raise OSError("network down")

    monkeypatch.setattr(feed_downloader, "urlopen", fail)
    monkeypatch.setattr(feed_downloader.time, "sleep", lambda seconds: None)
    _, result, error = feed_downloader.download_item(item, tmp_path)
    assert calls == 3
    assert result is None
    assert error == "network down"
    assert target.read_bytes() == b"old"
    assert not target.with_suffix(".mp4.part").exists()


def test_main_uses_configured_worker_count_and_returns_failure(tmp_path: Path, monkeypatch):
    items = [MediaItem(f"Video {index}", str(index), f"https://example.com/{index}.mp4", ".mp4")
             for index in range(4)]
    monkeypatch.setattr(feed_downloader, "load_feed", lambda source: (b"xml", ""))
    monkeypatch.setattr(feed_downloader, "parse_feed", lambda content, base: (items, 2))

    def result(item, output):
        if item.identifier == "3":
            return item, None, "failed"
        return item, output / feed_downloader.safe_filename(item), None

    created_workers = []
    original_executor = feed_downloader.ThreadPoolExecutor

    class RecordingExecutor(original_executor):
        def __init__(self, max_workers):
            created_workers.append(max_workers)
            super().__init__(max_workers=max_workers)

    monkeypatch.setattr(feed_downloader, "download_item", result)
    monkeypatch.setattr(feed_downloader, "ThreadPoolExecutor", RecordingExecutor)
    assert feed_downloader.main(["feed.xml", "--output", str(tmp_path), "--workers", "2"]) == 1
    assert created_workers == [2]
