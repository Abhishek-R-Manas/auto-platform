(() => {
  'use strict';
  let snapshot=null,busy=false,checking=false;
  function showCheck(kind){const result=snapshot.checks[kind];$(`setup-${kind}-result`).textContent=result?`${result.status==='PASSED'?'Verified: ':'Check failed: '}${result.message}`:snapshot.session.authenticated?'Ready to test this session.':'Sign in with Zerodha first.';}
  async function refreshSetup(){
    if(busy||activeView!=='setup')return;busy=true;
    try{
      snapshot=await api('/api/onboarding/status');const s=snapshot.session,m=snapshot.market;
      $('setup-callback').value=snapshot.callback_url;
      $('setup-overall').textContent=m.fresh_symbols>0&&s.authenticated?'QUOTES ARRIVING':m.enabled&&s.authenticated?'WAITING FOR QUOTES':s.authenticated?'SIGNED IN':s.login_available?'SIGN IN REQUIRED':'ADD APP KEYS';
      $('setup-notice').textContent=snapshot.authentication_error|| (m.fresh_symbols>0&&s.authenticated?'Market observations are arriving. Your account balances and executions remain simulated.':s.authenticated?'You are signed in. Run the data checks below, then connect your market feed.':s.login_available?'App keys are saved. Continue to Zerodha to sign in for this session.':'Start with your ₹500/month Connect app. Add the app keys below, then sign in on Zerodha.');
      $('setup-keys-status').textContent=s.login_available?'App key and secret are configured. Leave the fields empty unless replacing them.':s.authenticated?'An existing token session is available. Add the secret to enable browser sign-in later.':'Paste the key and secret from your Kite developer app.';
      $('setup-login-status').textContent=s.authenticated?'Session available. Check data access below.':s.login_available?'Ready for Zerodha’s normal sign-in.':'Save your app key and secret first.';
      $('setup-login').setAttribute('aria-disabled',String(!s.login_available));$('setup-login').tabIndex=s.login_available?0:-1;$('setup-session-note').textContent=snapshot.session_note;
      $('setup-check-quotes').disabled=!s.authenticated||checking;$('setup-check-history').disabled=!s.authenticated||checking;
      showCheck('quotes');showCheck('history');$('setup-connect').disabled=!snapshot.can_connect||m.enabled;
      $('setup-feed-status').textContent=m.enabled?(m.fresh_symbols?'Receiving fresh observations':'Connection requested; waiting for available market observations'):'Stock quote access must pass before starting the feed.';
      $('setup-feed-counts').textContent=`${m.universe_count} NSE instruments · ${m.received_symbols} received · ${m.fresh_symbols} fresh · ${m.stale_or_missing_symbols} stale or missing. ${m.enabled?(m.error||m.phase):'Feed is not enabled.'}`;
      $('setup-paper-status').textContent=snapshot.paper_source==='KITE'?(snapshot.paper_ready?'Your paper desk uses fresh Kite market quotes. Every order and balance remains simulated.':'Kite is selected for the paper desk. Waiting for fresh executable quotes before it is ready.'):'Your paper desk still uses synthetic prices. Connect stock quotes below when the account is flat.';
      $('setup-paper-connect').disabled=!snapshot.can_connect||snapshot.paper_source==='KITE';
      const complete=[s.login_available||s.authenticated,s.authenticated,!!snapshot.checks.quotes?.access_verified&&!!snapshot.checks.history?.access_verified,m.enabled&&s.authenticated];
      ['keys','login','checks','feed'].forEach((name,i)=>$(`setup-progress-${name}`).classList.toggle('complete',complete[i]));
      $('setup-coverage').innerHTML=`<div class="table-scroll"><table><thead><tr><th>Data</th><th>Availability</th></tr></thead><tbody>${snapshot.coverage.map(row=>`<tr><td>${escapeHTML(row.name)}</td><td>${escapeHTML(row.available)}</td></tr>`).join('')}</tbody></table></div>`;
    }catch(error){$('setup-notice').textContent=error.message;}finally{busy=false;}
  }
  $('setup-credentials').addEventListener('submit',async event=>{
    event.preventDefault();const button=$('setup-save-keys');button.disabled=true;
    try{const body={api_key:$('setup-api-key').value.trim(),api_secret:$('setup-api-secret').value.trim()};await api('/api/onboarding/kite/credentials',body);toast('App keys saved privately. Sign in with Zerodha next.');}
    catch(error){toast(error.message,true);}
    finally{$('setup-api-key').value='';$('setup-api-secret').value='';button.disabled=false;await refreshSetup();await refresh();}
  });
  for(const kind of ['quotes','history'])$(`setup-check-${kind}`).addEventListener('click',async()=>{
    const button=$(`setup-check-${kind}`);checking=true;$('setup-check-quotes').disabled=true;$('setup-check-history').disabled=true;$(`setup-${kind}-result`).textContent='Checking actual data access…';
    try{const result=await api('/api/onboarding/kite/check',{kind});toast(result.message,result.status!=='PASSED');}
    catch(error){toast(error.message,true);}finally{checking=false;await refreshSetup();}
  });
  $('setup-connect').addEventListener('click',async()=>{try{await api('/api/onboarding/kite/connect',{});toast('Market connection requested. Watch the freshness counts.');}catch(error){toast(error.message,true);}await refreshSetup();});
  $('setup-paper-connect').addEventListener('click',async()=>{const button=$('setup-paper-connect');button.disabled=true;try{await api('/api/market/source',{source:'KITE',instruments:['NSE:RELIANCE','NSE:HDFCBANK','NSE:INFY']});toast('Paper desk connected to stock quotes. Balances remain virtual.');await refresh();}catch(error){toast(error.message,true);}finally{await refreshSetup();}});
  $('setup-open-market').addEventListener('click',()=>navigate('marketdata'));
  $('settings-open-setup').addEventListener('click',()=>{navigate('setup');refreshSetup();});
  $('setup-copy-callback').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('setup-callback').value);toast('Redirect URL copied. Paste it into your Kite app.');}catch(error){$('setup-callback').focus();$('setup-callback').select();toast('Select and copy the redirect URL shown here.');}});
  document.addEventListener('click',event=>{if(event.target.closest('[data-view]'))setTimeout(refreshSetup,100);});
  setInterval(refreshSetup,3000);setTimeout(refreshSetup,200);
})();
