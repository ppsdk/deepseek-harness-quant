"""Read-only capability probe for public market feeds; no local quote access."""
from concurrent.futures import ThreadPoolExecutor
import json
import requests
from bs4 import BeautifulSoup

def probe(kind):
    try:
        with requests.Session() as session:
            session.trust_env=False
            if kind=='sina_ma':
                r=session.get('https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData',params=dict(symbol='sh000300',scale=240,ma='7,13,34',datalen=100),timeout=12)
                raw=r.text;value=json.loads(raw[raw.index('['):raw.rindex(']')+1]);return dict(feed=kind,status=r.status_code,rows=len(value),fields=list(value[-1]),date=value[-1].get('day'))
            if kind=='legu':
                r=session.get('https://legulegu.com/stockdata/market-activity',timeout=12);soup=BeautifulSoup(r.text,'lxml')
                return dict(feed=kind,status=r.status_code,table_text=[x.get_text(' ',strip=True) for x in soup.select('table')][:1],date=soup.select_one('.market-activity-meta').get_text(' ',strip=True) if soup.select_one('.market-activity-meta') else None)
            r=session.get('https://qt.gtimg.cn/q=sh000001,sh000300',timeout=12);r.encoding='gbk'
            return dict(feed=kind,status=r.status_code,quote_fields=[len(x.split('~')) for x in r.text.split(';') if '~' in x])
    except Exception as e:return dict(feed=kind,error=type(e).__name__+': '+str(e)[:160])

if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item in pool.map(probe,['sina_ma','legu','tencent']):print(json.dumps(item,ensure_ascii=False))
