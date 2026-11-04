import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import media_cache


class MediaCacheTests(unittest.TestCase):
    def test_rejects_arbitrary_hosts_and_path_traversal(self):
        with patch("media_cache.requests.get") as get:
            for url in ("http://localhost/x", "https://evil.example/x", "https://douyinpic.com.evil.example/x"):
                self.assertIsNone(media_cache.cache_image(url))
            get.assert_not_called()
        for name in ("../douyin.db", "x.svg", "a"*64+".jpg/../db", "C:\\data\\db"):
            self.assertIsNone(media_cache.media_path(name))

    def test_image_is_persisted_and_reused_without_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(media_cache,"MEDIA_DIR",Path(folder)):
            response=Mock(status_code=200)
            response.iter_content.return_value=[b"\xff\xd8\xfffixture"]
            context=Mock()
            context.__enter__=Mock(return_value=response)
            context.__exit__=Mock(return_value=False)
            with patch("media_cache.requests.get",return_value=context) as get:
                url="https://p3-pc.douyinpic.com/avatar.jpg"
                first=media_cache.cache_image(url)
                self.assertTrue(first.startswith("/media/"))
                self.assertEqual(media_cache.cache_image(url),first)
                self.assertEqual(get.call_count,1)
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
                self.assertEqual(media_cache.media_path(first.split('/')[-1]).read_bytes(),b"\xff\xd8\xfffixture")
