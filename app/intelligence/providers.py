"""Only fixed official public origins; no provider credentials or arbitrary URLs."""
import json,ssl
import httpx

URLS={'RBI':'https://www.rbi.org.in/pressreleases_rss.xml','MOSPI':'https://mcp.mospi.gov.in/'}

class OfficialFeeds:
    def request(self,source,body=None):
        if source not in URLS:raise ValueError('Unknown official data origin')
        context=ssl.create_default_context();context.minimum_version=ssl.TLSVersion.TLSv1_2
        headers={'Accept':'application/json, text/event-stream' if body is not None else 'application/rss+xml, application/xml, text/xml'}
        try:
            with httpx.Client(verify=context,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(12,connect=5)) as client:
                with client.stream('POST' if body is not None else 'GET',URLS[source],json=body,headers=headers) as response:
                    if response.status_code!=200:raise ValueError(source+' public data request failed (HTTP '+str(response.status_code)+')')
                    content=bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content)>4000000:raise ValueError('Official data response exceeded four megabytes')
            return content.decode('utf-8-sig')
        except ValueError:raise
        except Exception:raise ValueError(source+' connection or verified TLS failed; use official report import while unavailable') from None

    def rpc(self,name,arguments):
        if name not in {'list_datasets','get_indicators','get_metadata','get_data'}:raise ValueError('Only official read-only statistical operations are permitted')
        content=self.request('MOSPI',dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name=name,arguments=arguments)))
        if content.lstrip().startswith('{'):payload=json.loads(content)
        else:
            lines=[line[5:].strip() for line in content.splitlines() if line.startswith('data:')]
            if not lines:raise ValueError('MoSPI returned no structured data message')
            payload=json.loads(lines[-1])
        result=payload.get('result',{})
        if payload.get('error') or result.get('isError'):raise ValueError('MoSPI rejected the requested statistical query')
        data=result.get('structuredContent')
        if data is None:
            text=next((item.get('text') for item in result.get('content',[]) if item.get('type')=='text'),None)
            if not text:raise ValueError('MoSPI returned no statistical payload')
            data=json.loads(text)
        if not isinstance(data,dict):raise ValueError('Unexpected MoSPI statistical response')
        return data

    def fetch(self,source):
        if source=='RBI':return dict(source='RBI',kind='RBI_RSS',source_url=URLS['RBI'],content=self.request('RBI'))
        if source=='MOSPI':
            from .mospi import download
            return download(self)
        raise ValueError('Unknown official source')
