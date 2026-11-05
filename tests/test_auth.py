"""Exercise the public HTTP boundary with temporary credentials and sessions."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import app
import auth


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = auth.hash_password('test-only-password')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='server-auth-')
        self.root = Path(self.temp.name)
        self.file = self.root / 'settings.json'
        self.file.write_text(json.dumps({'username':'test-user','password_hash':self.password_hash}),encoding='utf-8')
        self.server = app.LocalHTTPServer(('127.0.0.1',0), app.Handler)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.env = patch.dict(os.environ, {'DOUYIN_AUTH_FILE':str(self.file),'DOUYIN_AUTH_DB':str(self.root/'sessions.db'),'DOUYIN_PUBLIC_ORIGIN':self.base})
        self.env.start()
        self.state = patch.object(app.workbench,'state',return_value={'accounts':['fixture']})
        self.state_mock = self.state.start()
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.client = requests.Session()

    def tearDown(self):
        self.client.close()
        self.server.shutdown();self.server.server_close()
        self.state.stop();self.env.stop();self.temp.cleanup()

    def login(self, **extra):
        return self.client.post(self.base+'/api/auth/login',json={'username':'test-user','password':'test-only-password',**extra},timeout=5)

    def test_login_is_a_page_and_every_business_route_is_protected(self):
        page=self.client.get(self.base+'/login')
        self.assertEqual(page.status_code,200)
        self.assertIn('id="loginForm"',page.text)
        self.assertNotIn('WWW-Authenticate',page.headers)
        self.assertIn('暖暖影视',page.text)
        for path in ['/theme.js', '/theme.css']:
            response=self.client.get(self.base+path,allow_redirects=False)
            self.assertEqual(response.status_code,200,path)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        for path in ['/home','/screen','/assets','/manage','/media/missing.jpg','/product.js','/discover','/discovery.js','/audience.js','/discovery.css']:
            response=self.client.get(self.base+path,allow_redirects=False)
            self.assertEqual(response.status_code,302,path)
            self.assertTrue(response.headers['Location'].startswith('/login?next='))
        for path in ['/api/workbench/state','/api/playback/settings','/api/tikhub/catalog','/api/integrations/asset?source=downloads&path=video.mp4','/api/discovery/catalog','/api/discovery/cache?query=%7B%7D','/api/discovery/export?query=%7B%7D']:
            self.assertEqual(self.client.get(self.base+path).status_code,401,path)
        self.assertEqual(self.client.post(self.base+'/api/intel/account-settings',json={}).status_code,401)
        for endpoint in ['plan','submit','rules','watches','settings']:
            self.assertEqual(self.client.post(self.base+'/api/discovery/'+endpoint,json={}).status_code,401)
        self.state_mock.assert_not_called()

    def test_login_cookie_persists_and_logout_invalidates_it(self):
        response=self.login(next='/screen#overview')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['next'],'/screen#overview')
        for attr in ['HttpOnly','SameSite=Lax','Path=/','Max-Age=']:
            self.assertIn(attr,response.headers['Set-Cookie'])
        token=self.client.cookies.get(auth.COOKIE_NAME)
        self.assertNotIn(token.encode(),(self.root/'sessions.db').read_bytes())
        self.assertEqual(self.client.get(self.base+'/api/workbench/state').json(),{'accounts':['fixture']})
        self.assertEqual(self.client.get(self.base+'/home').status_code,200)
        self.assertEqual(self.client.get(self.base+'/api/auth/session').json()['username'],'test-user')
        self.client.post(self.base+'/api/auth/logout')
        self.assertEqual(self.client.get(self.base+'/api/workbench/state').status_code,401)
        self.assertIsNone(auth.current_user({'Cookie':auth.COOKIE_NAME+'='+token}))

    def test_password_and_username_errors_are_generic_and_rate_limited(self):
        for i in range(auth.ATTEMPT_LIMIT):
            r=self.login(username='unknown' if i%2 else 'test-user',password='wrong')
            self.assertEqual((r.status_code,r.json()['error']),(401,'账号或密码错误'))
        self.assertEqual(self.login().status_code,429)

    def test_invalid_cookie_basic_auth_and_cross_origin_are_rejected(self):
        for value in ['bad-cookie',secrets.token_urlsafe(32)]:
            r=self.client.get(self.base+'/api/workbench/state',headers={'Cookie':auth.COOKIE_NAME+'='+value})
            self.assertEqual(r.status_code,401)
            self.assertNotIn('WWW-Authenticate',r.headers)
        r=self.client.get(self.base+'/api/workbench/state',auth=('test-user','test-only-password'))
        self.assertEqual(r.status_code,401)
        self.assertEqual(self.login().status_code,200)
        r=self.client.post(self.base+'/api/auth/logout',headers={'Origin':'https://outside.example'})
        self.assertEqual(r.status_code,403)
        self.assertTrue(self.client.get(self.base+'/api/auth/session').json()['authenticated'])
        r=self.client.post(self.base+'/api/auth/logout',headers={'Origin':self.base})
        self.assertEqual(r.status_code,200)

    def test_expired_session_and_password_rotation_invalidate_sessions(self):
        self.login()
        with auth.database() as conn:
            conn.execute('UPDATE sessions SET expires=?',(time.time()-1,))
        self.assertEqual(self.client.get(self.base+'/api/workbench/state').status_code,401)
        self.login()
        self.file.write_text(json.dumps({'username':'test-user','password_hash':auth.hash_password('different-password')}),encoding='utf-8')
        self.assertEqual(self.client.get(self.base+'/api/workbench/state').status_code,401)

    def test_redirects_stay_on_site_and_config_fails_closed(self):
        for value in ['https://outside.example','//outside.example','/%2foutside.example','/\\outside.example','/%0aLocation:evil','//[broken','/login']:
            self.assertEqual(auth.safe_next(value),'/home',value)
        self.assertEqual(self.login(next='https://outside.example').json()['next'],'/home')
        self.file.unlink()
        with self.assertRaises(RuntimeError):auth.enabled()


if __name__ == '__main__':
    unittest.main()
