from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import native_download as download


class NativeDownloadTests(unittest.TestCase):
    def test_platform_links_reject_unrelated_hosts(self):
        self.assertIn('douyin.com',download.content_url('https://www.douyin.com/video/7703151458754472787','dy'))
        for value in ['file:///etc/passwd','https://douyin.com.example.org/a','http://127.0.0.1/a','https://user@douyin.com/a']:
            with self.assertRaises(ValueError):download.content_url(value,'dy')

    def test_media_download_cannot_reach_local_network(self):
        for address in ('127.0.0.1','192.168.1.1','::1'):
            with patch.object(download.socket,'getaddrinfo',return_value=[(socket.AF_INET,1,6,'',(address,443))]):
                with self.assertRaises(ValueError):download.public_media('https://media.example.com/video.mp4')

    def test_tun_fake_ip_requires_known_cdn_and_independent_public_dns(self):
        download.DNS_CACHE.clear()
        response=Mock(status_code=200)
        response.json.return_value={'Status':0,'Answer':[{'type':1,'data':'121.228.190.75'}]}
        fake=[(socket.AF_INET,1,6,'',('198.18.0.160',443))]
        with patch.object(download.socket,'getaddrinfo',return_value=fake),patch.object(download.requests,'get',return_value=response) as call:
            url='https://v95-aw-web.douyinvod.com/video'
            self.assertEqual(download.public_media(url),url)
            call.assert_called_once()
            with self.assertRaises(ValueError):download.public_media('https://unknown.example.com/video')
            self.assertEqual(call.call_count,1)

    def test_known_cdn_cannot_use_fake_ip_when_public_dns_is_private(self):
        download.DNS_CACHE.clear()
        response=Mock(status_code=200)
        response.json.return_value={'Status':0,'Answer':[{'type':1,'data':'127.0.0.1'}]}
        fake=[(socket.AF_INET,1,6,'',('198.18.0.160',443))]
        with patch.object(download.socket,'getaddrinfo',return_value=fake),patch.object(download.requests,'get',return_value=response):
            with self.assertRaises(ValueError):download.public_media('https://v95-aw-web.douyinvod.com/video')

    def response(self,status,body,content_type='video/mp4'):
        r=Mock(status_code=status,is_redirect=False,headers={'Content-Length':str(len(body)),'Content-Type':content_type})
        r.iter_content.return_value=[body]
        return r

    def test_existing_partial_file_resumes_and_commits_only_after_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'video.mp4'
            target.with_suffix('.mp4.part').write_bytes(b'abc')
            session=Mock();session.get.return_value=self.response(206,b'def')
            progress=[]
            with patch.object(download.requests,'Session',return_value=session),patch.object(download,'public_media',side_effect=lambda x:x):
                size=download.download_file('https://media.example.com/a',target,'dy',lambda:False,lambda a,b:progress.append((a,b)))
            self.assertEqual(size,6)
            self.assertEqual(target.read_bytes(),b'abcdef')
            self.assertEqual(session.get.call_args.kwargs['headers']['Range'],'bytes=3-')
            self.assertEqual(progress[-1],(6,6))

    def test_html_response_or_cancel_does_not_replace_a_known_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'video.mp4';target.write_bytes(b'known-video')
            for response,cancelled in [(self.response(200,b'<html>','text/html'),lambda:False),(self.response(200,b'new'),lambda:True)]:
                session=Mock();session.get.return_value=response
                with patch.object(download.requests,'Session',return_value=session),patch.object(download,'public_media',side_effect=lambda x:x):
                    with self.assertRaises(ValueError):download.download_file('https://media.example.com/a',target,'dy',cancelled,lambda *_:None)
                self.assertEqual(target.read_bytes(),b'known-video')


if __name__=='__main__':unittest.main()
