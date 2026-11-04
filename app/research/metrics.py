"""Portfolio diagnostics with explicit sample and search limitations."""
import math
import random
import statistics
from statistics import NormalDist
from .catalog import aware


def drawdown(values):
    peak=values[0] if values else 0;worst=0
    for value in values:
        peak=max(peak,value)
        worst=max(worst,(peak-value)/peak if peak>0 else 0)
    return worst


def metrics(curve,capital,seed=42):
    if not curve: return dict(observations=0,total_return_pct=0,max_drawdown_pct=0,sharpe=None,cagr_pct=None,daily_returns=[],notes=['No active evaluation observations'])
    daily={}
    for row in curve: daily[row['time'][:10]]=row['equity']
    values=[capital,*daily.values()]
    returns=[b/a-1 for a,b in zip(values,values[1:]) if a>0]
    mean=statistics.mean(returns) if returns else 0
    sd=statistics.stdev(returns) if len(returns)>1 else 0
    sharpe=mean/sd*math.sqrt(252) if len(returns)>=20 and sd else None
    duration=(aware(curve[-1]['time'])-aware(curve[0]['time'])).total_seconds()/86400
    end=curve[-1]['equity']
    result=dict(observations=len(curve),trading_days=len(daily),total_return_pct=(end/capital-1)*100,
        max_drawdown_pct=drawdown([capital,*[r['equity'] for r in curve]])*100,
        sharpe=sharpe,cagr_pct=((end/capital)**(365.25/duration)-1)*100 if duration>=365 and end>0 else None,
        daily_returns=returns,notes=['Sharpe uses daily marked equity and zero risk-free return; it is not a profitability guarantee'])
    if len(returns)<20: result['notes'].append('Fewer than 20 daily returns; Sharpe is unavailable')
    if duration<365: result['notes'].append('Less than one year; CAGR is not reported')
    if len(returns)>=20:
        rng=random.Random(seed);means=[];dd=[];block=min(5,len(returns));n=len(returns)
        for _ in range(200):
            sample=[]
            while len(sample)<n:
                start=rng.randrange(n);sample.extend(returns[(start+j)%n] for j in range(block))
            sample=sample[:n];means.append(statistics.mean(sample)*252)
            path=[capital]
            for r in sample: path.append(path[-1]*(1+r))
            dd.append(drawdown(path))
        means.sort();dd.sort()
        result['bootstrap']=dict(method='Circular 5-observation block bootstrap, 200 seeded resamples',annual_mean_return_interval_pct=[means[5]*100,means[194]*100],drawdown_95_pct=dd[189]*100,note='Historical resampling diagnostic; not a tail-loss bound')
    return result


def deflated_sharpe(returns,trial_daily_sharpes,trials):
    if len(returns)<30 or len(trial_daily_sharpes)<2 or trials<2:
        return dict(available=False,reason='Needs at least 30 daily observations and two completed comparable trials',trials=trials)
    mean=statistics.mean(returns);sd=statistics.stdev(returns)
    if not sd: return dict(available=False,reason='Zero sample volatility',trials=trials)
    sr=mean/sd;n=len(returns)
    skew=sum(((r-mean)/sd)**3 for r in returns)/n
    kurt=sum(((r-mean)/sd)**4 for r in returns)/n
    trial_sd=statistics.stdev(trial_daily_sharpes)
    normal=NormalDist();gamma=.5772156649
    benchmark=trial_sd*((1-gamma)*normal.inv_cdf(1-1/trials)+gamma*normal.inv_cdf(1-1/(trials*math.e)))
    variance=max(1e-12,1-skew*sr+(kurt-1)*sr*sr/4)
    probability=normal.cdf((sr-benchmark)*math.sqrt(n-1)/math.sqrt(variance))
    return dict(available=True,probability=probability,trials=trials,expected_best_daily_sharpe=benchmark,
        method='Estimated Deflated Sharpe using observed trial dispersion, skewness and kurtosis',
        limitations='Dependent trials and serial returns affect inference; leakage and bad execution assumptions are not corrected')
