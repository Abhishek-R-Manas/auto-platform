"""Optional fixed-origin Telegram delivery. Uncertain sends are never retried automatically."""
import json
import re
import ssl
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
from pydantic import Field, SecretStr, model_validator
from typing import Annotated
from ..research.schemas import Strict

class AlertConfig(Strict):
    bot_token: Annotated[SecretStr,Field(min_length=20,max_length=256)]
    chat_id: Annotated[SecretStr,Field(min_length=1,max_length=30)]
    enabled: bool=False
    consent: bool=False
    daily_limit: Annotated[int,Field(ge=1,le=500)]=50
    @model_validator(mode='after')
    def validate_config(self):
        if not re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{15,200}',self.bot_token.get_secret_value()):raise ValueError('Invalid bot token format')
        if not re.fullmatch(r'-?[0-9]{1,20}',self.chat_id.get_secret_value()):raise ValueError('Use a numeric destination chat id')
        if self.enabled and not self.consent:raise ValueError('External delivery requires explicit authorization')
        return self

class AlertRetry(Strict):
    acknowledge_possible_duplicate: bool=False

class DeliveryError(ValueError):pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class TelegramDelivery:
    def __init__(self,values):
        self.token=values.get('ARTHA_TELEGRAM_BOT_TOKEN','');self.chat=values.get('ARTHA_TELEGRAM_CHAT_ID','')
    def status(self):return dict(provider='TELEGRAM',configured=bool(self.token and self.chat))
    def send(self,text):
        if not self.status()['configured']:raise DeliveryError('Telegram delivery is not configured')
        context=ssl.create_default_context();context.minimum_version=ssl.TLSVersion.TLSv1_3
        opener=build_opener(ProxyHandler({}),HTTPSHandler(context=context),NoRedirect())
        # urllib has no request URL logging by default. The token-bearing URL is never retained.
        request=Request('https://api.telegram.org/bot'+self.token+'/sendMessage',data=json.dumps(dict(chat_id=self.chat,text=text[:3500],disable_notification=False)).encode(),headers={'Content-Type':'application/json'},method='POST')
        try:
            with opener.open(request,timeout=12) as response:raw=response.read(100001)
            if len(raw)>100000:raise DeliveryError('Delivery response exceeded its size limit')
            result=json.loads(raw)
            if result.get('ok') is not True or not isinstance(result.get('result',{}).get('message_id'),int):raise DeliveryError('Provider did not confirm message delivery')
            return dict(confirmed=True,message_id=result['result']['message_id'])
        except DeliveryError:raise
        except Exception:raise DeliveryError('Delivery failed or is uncertain; review before an explicit retry to avoid duplicate messages') from None
