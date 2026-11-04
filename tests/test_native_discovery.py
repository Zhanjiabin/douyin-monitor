import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_discovery_hook import Capture,install


def rows(start,count):
    return {'data':[{'aweme_info':{'aweme_id':str(7703151458754472787+i),'desc':'AI短片','video':{'duration':12000}}} for i in range(start,start+count)]}


class NativeDiscoveryCaptureTests(unittest.TestCase):
    def test_multiple_keywords_deduplicate_and_never_exceed_total_or_per_word_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Capture(Path(folder)/'capture.json',{'limit':7,'per_keyword':3})
            self.assertEqual(len(c.page('AI动画',rows(0,20))['data']),3)
            self.assertEqual(len(c.page('AI短片',rows(0,20))['data']),3)
            self.assertEqual(len(c.page('AI创意',rows(0,20))['data']),1)
            self.assertEqual(len(c.items),7)
            self.assertEqual(len(c.page('AI动画',rows(30,20))['data']),0)

    def test_filter_parameters_are_applied_before_native_request_and_fetch_stops_at_limit(self):
        calls=[]
        class Client:
            async def search_info_by_keyword(self,keyword,**kwargs):calls.append(kwargs);return rows(0,20)
        with tempfile.TemporaryDirectory() as folder:
            c=install(Client,{'limit':2,'per_keyword':2,'days':7,'sort':1},Path(folder)/'capture.json',int,int)
            asyncio.run(Client().search_info_by_keyword('AI视频',publish_time=0))
            asyncio.run(Client().search_info_by_keyword('AI视频'))
            self.assertEqual(len(calls),1);self.assertEqual(calls[0]['publish_time'],7);self.assertEqual(calls[0]['sort_type'],1)
            self.assertEqual(len(c.items),2)

    def test_missing_response_is_a_failure_and_does_not_fabricate_data(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Capture(Path(folder)/'capture.json',{'limit':10})
            with self.assertRaises(ValueError):c.page('AI视频',{'status_code':8})
            self.assertEqual(c.items,{})

    def test_native_cursor_wins_over_upstream_hardcoded_twenty_item_offset(self):
        calls=[]
        class Client:
            async def search_info_by_keyword(self,keyword,**kwargs):
                calls.append(kwargs['offset'])
                return {**rows(kwargs['offset'],10),'cursor':kwargs['offset']+10,'has_more':1}
        with tempfile.TemporaryDirectory() as folder:
            install(Client,{'limit':30,'per_keyword':30},Path(folder)/'capture.json',int,int)
            asyncio.run(Client().search_info_by_keyword('AI',offset=0))
            asyncio.run(Client().search_info_by_keyword('AI',offset=20))
            self.assertEqual(calls,[0,10])
