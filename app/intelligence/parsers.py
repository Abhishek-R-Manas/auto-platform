"""Explicit units, scopes and publication dates; no inferred institutional identities."""
import calendar,csv,io,math,re
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from xml.etree import ElementTree
from ..research.schemas import aware,iso

UNITS={'CPI_INDEX':'INDEX','CPI_YOY':'PERCENT','IIP_INDEX':'INDEX','IIP_YOY':'PERCENT','GDP_YOY':'PERCENT','REPO_RATE':'PERCENT','CRR':'PERCENT'}

class PlainText(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)
    def text(self):return re.sub(r'\s+',' ',' '.join(self.parts)).strip()

def plain(value):
    parser=PlainText();parser.feed(value);return parser.text()

def number(value):
    try:result=float(str(value).strip().replace(',',''))
    except (TypeError,ValueError):raise ValueError('Report contains an invalid numeric value') from None
    if not math.isfinite(result):raise ValueError('Report contains a nonfinite value')
    return result

def csv_rows(content):
    reader=csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValueError('CSV needs unique column headers')
    rows=[]
    for row in reader:
        if None in row or any(v is None for v in row.values()):raise ValueError('CSV cells do not match the headers')
        if len(rows)>=10000:raise ValueError('Report exceeds 10,000 rows')
        rows.append(row)
    if not rows:raise ValueError('Report has no observations')
    return rows

def trade_day(value):
    for fmt in ('%d-%b-%Y','%Y-%m-%d','%d-%m-%Y','%d/%m/%Y'):
        try:return datetime.strptime(value.strip(),fmt).date()
        except ValueError:pass
    raise ValueError('Unrecognized trade date')

def fii_rows(content,scope,published_at,received_at,ttl):
    if published_at and aware(published_at)>aware(received_at):raise ValueError('Publication time is in the future')
    result=[];seen=set()
    for raw in csv_rows(content):
        row={re.sub(r'[^a-z]','',key.lower()):value for key,value in raw.items()}
        category=str(row.get('category','')).upper().strip()
        who='fii' if category in {'FII/FPI','FII','FPI'} else 'dii' if category=='DII' else None
        if not who:raise ValueError('Expected FII/FPI or DII report category')
        day=trade_day(row.get('date',''));marker=(who,day)
        if marker in seen:raise ValueError('Duplicate category/date in report')
        seen.add(marker)
        if day>aware(received_at).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date() or published_at and day>aware(published_at).date():raise ValueError('Trade date is in the future or after publication')
        amounts={}
        for field in ('buy','sell','net'):
            key=next((key for key in row if key.startswith(field+'value')),None)
            if not key:raise ValueError('Expected NSE Buy/Sell/Net Value columns in INR crores')
            # The exchange CSV must identify crores or use the canonical field name.
            if key!=field+'value' and 'crore' not in key:raise ValueError('FII/DII value units must be INR crores')
            amounts[field]=number(row[key])
        if amounts['buy']<0 or amounts['sell']<0 or abs(amounts['buy']-amounts['sell']-amounts['net'])>.021:raise ValueError('Reported net does not reconcile to buy minus sell')
        for field,value in amounts.items():result.append(dict(series=f'{who}.{scope}.{field}',period=day.isoformat(),value=value,unit='INR_CRORE',published_at=iso(published_at) if published_at else None,age_anchor=iso(datetime.combine(day,datetime.min.time().replace(hour=15,minute=30),__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))),valid_until=iso(datetime.combine(day,datetime.min.time().replace(hour=15,minute=30),__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))+timedelta(hours=ttl)),status='PROVISIONAL',base_year=None))
    return result

def period_end(value):
    if re.fullmatch(r'\d{4}-\d{2}',value):
        year,month=map(int,value.split('-'));return datetime(year,month,calendar.monthrange(year,month)[1],23,59,59,tzinfo=timezone.utc)
    if re.fullmatch(r'\d{4}-Q[1-4]',value):
        year=int(value[:4]);month=int(value[-1])*3;return datetime(year,month,calendar.monthrange(year,month)[1],23,59,59,tzinfo=timezone.utc)
    return datetime.combine(trade_day(value),datetime.max.time(),timezone.utc)

def macro_rows(content,received_at):
    result=[];seen=set()
    for row in csv_rows(content):
        if not {'series','period','value','unit','published_at'}<=row.keys():raise ValueError('Macro CSV needs series, period, value, unit and published_at')
        series=row['series'].strip().upper();base=row.get('base_year','NONE').strip() or 'NONE';period=row['period'].strip()
        if series not in UNITS or row['unit'].strip().upper()!=UNITS[series]:raise ValueError('Unsupported macro series or unit')
        if not re.fullmatch(r'(?:NONE|[0-9]{4}(?:-[0-9]{2,4})?)',base):raise ValueError('Declare the macro series base year')
        if series in {'CPI_INDEX','CPI_YOY','IIP_INDEX','IIP_YOY','GDP_YOY'} and base=='NONE':raise ValueError('This macro series requires its published base year')
        published=aware(row['published_at']);effective=period_end(period)
        if published>aware(received_at):raise ValueError('Macro publication is in the future')
        value=number(row['value'])
        if UNITS[series]=='INDEX' and value<=0:raise ValueError('Macro indices must be positive')
        marker=(series,base,period)
        if marker in seen:raise ValueError('Duplicate macro series/reference period')
        seen.add(marker);status=row.get('status','PROVISIONAL').upper()
        if status not in {'PROVISIONAL','FINAL','REVISED'}:raise ValueError('Unknown publication status')
        if series in {'REPO_RATE','CRR'} and not 0<=value<=100:raise ValueError('Invalid policy rate')
        ttl=180 if series=='GDP_YOY' else 365 if series in {'REPO_RATE','CRR'} else 60
        result.append(dict(series=f'macro.{series}.{base}',period=effective.date().isoformat(),reference_period=period,value=value,unit=UNITS[series],base_year=base,published_at=iso(published),valid_until=iso(published+timedelta(days=ttl)),status=status))
    return result

def rbi_items(content,received_at):
    if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():raise ValueError('Unsupported XML declarations')
    try:root=ElementTree.fromstring(content.lstrip('\ufeff'))
    except ElementTree.ParseError:raise ValueError('Official feed returned invalid RSS') from None
    result=[]
    for item in root.findall('./channel/item')[:100]:
        title=plain(item.findtext('title',''))[:500];url=item.findtext('link','').strip();date=item.findtext('pubDate')
        if not title or not date:continue
        raw_date=parsedate_to_datetime(date);assumed=raw_date.tzinfo is None
        if assumed:raw_date=raw_date.replace(tzinfo=__import__('zoneinfo').ZoneInfo('Asia/Kolkata'))
        precision='DATE_ONLY' if assumed and raw_date.hour==raw_date.minute==raw_date.second==0 else 'LOCAL_PUBLISHER_TIME' if assumed else 'TIMESTAMP_WITH_OFFSET'
        published=aware(raw_date)
        if published>aware(received_at):continue
        host=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).hostname
        if host not in {'www.rbi.org.in','rbi.org.in'}:continue
        result.append(dict(series='event.RBI',period=published.date().isoformat(),value=None,unit='RELEASE',published_at=iso(published),valid_until=iso(published+timedelta(days=30)),status='PUBLISHED',publication_precision=precision,publication_timezone_assumed='Asia/Kolkata' if assumed else None,title=title,summary=plain(item.findtext('description',''))[:1000],publication_url=url))
    if not result:raise ValueError('No dated RBI release items were returned')
    return result
