"""Fixed Kite broker routes. Writes require a separate deployment capability."""
import os,re,ssl,time
import httpx

class BrokerFailure(ValueError):pass

class KiteBroker:
    def __init__(self,kite,enabled=None):
        self.kite=kite;self.last_write=0
        self.enabled=os.getenv('ARTHA_LIVE_TRANSPORT_ENABLED')=='1' if enabled is None else enabled

    def request(self,method,path,data=None):
        read=method=='GET' and path in {'/user/profile','/user/margins/equity','/orders','/trades','/portfolio/holdings','/portfolio/positions'}
        write=(method=='POST' and path=='/orders/regular') or (method=='DELETE' and re.fullmatch(r'/orders/regular/[0-9]{1,30}',path))
        if not read and not write:raise ValueError('Broker endpoint is outside the pilot allowlist')
        if write and not self.enabled:raise ValueError('Real order transport is disabled by deployment policy')
        if not self.kite.status()['authenticated']:raise ValueError('Sign in with Zerodha before accessing your broker account')
        # Credential rotation and authentication share this lock. No secrets leave the server.
        with self.kite.lock:
            if write:
                delay=1.1-(time.monotonic()-self.last_write)
                if delay>0:time.sleep(delay)
                self.last_write=time.monotonic()
            context=ssl.create_default_context();context.minimum_version=ssl.TLSVersion.TLSv1_3
            try:
                with httpx.Client(base_url='https://api.kite.trade',verify=context,timeout=httpx.Timeout(12,connect=5),trust_env=False,follow_redirects=False) as client:
                    with client.stream(method,path,headers={'X-Kite-Version':'3','Authorization':'token '+self.kite.key+':'+self.kite.token},data=data) as response:
                        if response.status_code in {401,403}:self.kite.token=''
                        if response.status_code!=200:raise BrokerFailure('Broker request was not confirmed; reconcile before taking further action')
                        content=bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content)>8*1024*1024:raise BrokerFailure('Broker response exceeded the size limit; reconcile before continuing')
                import json
                result=json.loads(content)
                if result.get('status')!='success' or not isinstance(result.get('data'),(dict,list)):raise BrokerFailure('Unexpected broker response; reconcile before continuing')
                return result['data']
            except BrokerFailure:raise
            except Exception:raise BrokerFailure('Broker response is uncertain; no request was retried. Reconcile with Zerodha.') from None

    def snapshot(self):
        profile=self.request('GET','/user/profile')
        return dict(account_id=profile.get('user_id'),exchanges=profile.get('exchanges',[]),products=profile.get('products',[]),order_types=profile.get('order_types',[]),
            funds=self.request('GET','/user/margins/equity'),orders=self.request('GET','/orders'),trades=self.request('GET','/trades'),
            holdings=self.request('GET','/portfolio/holdings'),positions=self.request('GET','/portfolio/positions'))

    def place(self,order,tag):
        if not self.enabled:raise ValueError('Real order transport is disabled by deployment policy')
        params=dict(exchange='NSE',tradingsymbol=order['symbol'],transaction_type=order['side'],quantity=order['quantity'],product='CNC',order_type=order.get('order_type','LIMIT'),price=order['limit_price'],validity='DAY',tag=tag)
        if params['order_type']=='SL':params['trigger_price']=order['stop_loss']
        return self.request('POST','/orders/regular',params)

    def cancel(self,identifier):return self.request('DELETE','/orders/regular/'+identifier)
