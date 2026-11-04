"""Read official statistical metadata before choosing numeric series/filter codes."""
import calendar,re
from datetime import datetime,timedelta,timezone
from .parsers import number,period_end
from ..research.catalog import canonical
from ..research.schemas import aware,iso

URL='https://mcp.mospi.gov.in/'

def data_object(payload):
    if payload.get('error') or payload.get('statusCode') is False:raise ValueError('MoSPI rejected a statistical query')
    data=payload.get('data')
    if isinstance(data,list):
        if len(data)!=1 or not isinstance(data[0],dict):raise ValueError('Unexpected MoSPI metadata shape')
        return data[0]
    if not isinstance(data,dict):raise ValueError('Missing official statistical metadata')
    return data

def code(metadata,collection,label_key,code_key,label):
    rows=metadata.get(collection,[]);matches=[item[code_key] for item in rows if str(item.get(label_key,'')).casefold()==label.casefold()]
    if len(matches)!=1:raise ValueError('Official '+collection+' definition is missing or ambiguous')
    return str(matches[0])

def newest_base(payload):
    rows=data_object(payload).get('base_year',[])
    values=[str(item['base_year']) for item in rows if re.fullmatch(r'\d{4}(?:-\d{2})?',str(item.get('base_year','')))]
    if not values:raise ValueError('No official base-year definitions')
    return max(values,key=lambda value:int(value[:4]))

def query_year(metadata,maximum):
    rows=metadata.get('year',[]);values=[str(item['year']) for item in rows if int(str(item['year'])[:4])<=maximum]
    if not values:raise ValueError('No applicable official reference years')
    return max(values,key=lambda value:int(value[:4]))

def normalize(dataset,base,records,at):
    result=[];seen=set()
    for item in records:
        if str(item.get('base_year'))!=base:raise ValueError('Returned macro base year differs from the requested series')
        if dataset=='NAS':
            if item.get('indicator')!='GDP Growth Rate' or item.get('frequency')!='Quarterly' or item.get('unit')!='%':raise ValueError('Expected quarterly GDP growth in percent')
            year=str(item['year']);quarter=str(item['quarter'])
            if not re.fullmatch(r'\d{4}-\d{2}',year) or not re.fullmatch(r'Q[1-4]',quarter):raise ValueError('Invalid fiscal GDP reference period')
            start=int(year[:4]);q=int(quarter[1]);calendar_year=start+int(q==4);month={1:6,2:9,3:12,4:3}[q]
            period=f'{calendar_year:04d}-{month:02d}';values=[('GDP_YOY',item['constant_price'],'PERCENT')];ttl=180
            label='FY'+year+' '+quarter+'; constant prices'
        else:
            year=int(item['year']);month=next((index for index,name in enumerate(calendar.month_name) if name.casefold()==str(item.get('month','')).casefold()),None)
            if not month:raise ValueError('Unknown official reference month')
            period=f'{year:04d}-{month:02d}';label=period;ttl=90
            if dataset=='CPI':
                if item.get('state')!='All India' or item.get('sector')!='Combined' or item.get('division')!='CPI (General)' or any(item.get(key) for key in ('group','class','sub_class','item')):continue
                values=[('CPI_INDEX',item['index'],'INDEX'),('CPI_YOY',item['inflation'],'PERCENT')]
            else:
                if item.get('type')!='General' or item.get('category')!='General' or item.get('sub_category'):continue
                values=[('IIP_INDEX',item['index'],'INDEX'),('IIP_YOY',item['growth_rate'],'PERCENT')]
        end=period_end(period)
        if end>aware(at):continue
        for series,value,unit in values:
            if value in {None,'','NA','N/A'}:continue
            value=number(value)
            if unit=='INDEX' and value<=0:raise ValueError('Official macro index is invalid')
            key=(series,period)
            if key in seen:raise ValueError('Official query returned multiple ambiguous vintages for one period')
            seen.add(key)
            result.append(dict(series=f'macro.{series}.{base}',period=end.date().isoformat(),reference_period=label,value=value,unit=unit,base_year=base,published_at=None,publication_time_known=False,valid_until=iso(end+timedelta(days=ttl)),status='OFFICIAL_CURRENT_VINTAGE',method='Official published value; availability starts at recorded server receipt'))
    return result

def download(client,at=None):
    at=aware(at or datetime.now(timezone.utc));evidence={};observations=[];warnings=[]
    # A fixed provider client executes read-only statistical operations, without
    # model tools, generated code, account context or credentials.
    for dataset in ('CPI','IIP','NAS'):
        try:
            indicators=client.rpc('get_indicators',dict(dataset=dataset));base=newest_base(indicators)
            arguments=dict(dataset=dataset,base_year=base)
            if dataset=='CPI':arguments.update(level='Group',series='Current')
            elif dataset=='IIP':arguments.update(frequency='Monthly')
            else:
                definitions=data_object(indicators)
                indicator=code(definitions,'indicator','name','indicator_code','GDP Growth Rate')
                arguments.update(indicator_code=int(indicator),series='Current',frequency_code=2)
            metadata_payload=client.rpc('get_metadata',arguments);metadata=data_object(metadata_payload)
            limit_year=at.year if dataset!='NAS' or at.month>=4 else at.year-1
            filters=dict(base_year=base,year=query_year(metadata,limit_year),limit=100,page=1,Format='JSON')
            if dataset=='CPI':filters.update(series='Current',state_code=code(metadata,'state','state_name','state_code','All India'),sector_code=code(metadata,'sector','sector_name','sector_code','Combined'),division_code=code(metadata,'division','division_name','division_code','CPI (General)'))
            elif dataset=='IIP':filters.update(frequency='Monthly',type='General',category_code=code(metadata,'category','category_name','category_code','General'))
            else:filters.update(series='Current',frequency_code='Quarterly',indicator_code=indicator)
            payload=client.rpc('get_data',dict(dataset=dataset,filters=filters))
            if payload.get('error') or payload.get('statusCode') is False or not isinstance(payload.get('data'),list):raise ValueError('Official statistical query returned no usable table')
            if int(payload.get('meta_data',{}).get('totalPages',1))>1:raise ValueError('Official query needs pagination; refusing a silently incomplete series')
            rows=normalize(dataset,base,payload['data'],at)
            if not rows:raise ValueError('Official statistical table contains no applicable headline observations')
            evidence[dataset]=dict(indicators=indicators,metadata=metadata_payload,filters=filters,response=payload)
            observations.extend(rows)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:warnings.append(dataset+': '+str(exc)[:180])
    if not observations:raise ValueError('MoSPI returned no usable official macro values; inspect source status or import a dated report')
    return dict(source='MOSPI',kind='MOSPI_OFFICIAL_MCP',source_url=URL,content=canonical(evidence),observations=observations,extra=dict(warnings=warnings,publication_time_note='Original release timestamps are absent from these tables; server receipt is the earliest recorded availability.'))
