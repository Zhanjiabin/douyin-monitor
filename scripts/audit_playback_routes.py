"""Read-only audit of the reference collector and an explicit mix request.

Only summaries are written; no cookies, signatures, or full request URLs.
"""
import importlib.util
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from config import load_config, BROWSER_PROFILE
import store

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    source=Path(__file__).resolve().parents[2]/"src"/"collector.py"
    spec=importlib.util.spec_from_file_location("reference_collector",source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BROWSER_PROFILE=BROWSER_PROFILE
    report=[]
    with module.DouyinCollector(load_config()) as col:
        seen=[]
        def observe(response):
            if not any(p in response.url for p in ("/aweme/detail/","/mix/aweme/","/series/aweme/")):
                return
            try:
                body=response.json()
            except Exception:
                return
            rows=body.get("aweme_list") or []
            seen.append({"endpoint":response.url.split('?')[0].split('/web/')[-1],
                         "rows":len(rows),"positive_play":sum((a.get('statistics') or {}).get('play_count',0)>0 for a in rows),
                         "zero_play":sum((a.get('statistics') or {}).get('play_count')==0 for a in rows)})
        col._page.on('response',observe)
        cases=[('known_mix','7694237999369915683'),('missing_mix','7696895550930652459'),('missing_homepage','7699391482297355546')]
        for label,aid in cases:
            seen.clear()
            result=col.collect_video(aid)
            row={"case":label,"aweme_id":aid,"mix_id":result.get('mix_id'),"reference_episodes":len(result.get('episodes') or []),"endpoints":list(seen)}
            print(json.dumps(row,ensure_ascii=False),flush=True)
            report.append(row)
            if label=='missing_mix':
                # The reference's actual public endpoint, requested from the same browser.
                direct=col._page.evaluate('''async mid => {
                    const params=new URLSearchParams({device_platform:'webapp',aid:'6383',mix_id:mid,cursor:'0',count:'50'});
                    const response=await fetch('/aweme/v1/web/mix/aweme/?'+params,{credentials:'same-origin'});
                    const text=await response.text();
                    try {const data=JSON.parse(text); const rows=data.aweme_list||[];return {http:response.status,status:data.status_code,rows:rows.length,positive_play:rows.filter(a=>a.statistics?.play_count>0).length,ids:rows.map(a=>a.aweme_id)};}
                    catch {return {http:response.status,json:false,bytes:text.length};}
                }''',result.get('mix_id'))
                print('DIRECT_MIX',json.dumps(direct),flush=True)
                row['direct_mix']=direct
    output=Path(__file__).resolve().parents[1]/'output'/'playback-route-audit.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
