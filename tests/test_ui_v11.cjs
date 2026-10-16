/* v1.1 UI: confirmed tab, stable sort + signal age, turnover filter, history view, notifications. Dev only: npm install --no-save jsdom@30.1.2 */
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../contract_radar/assets/dashboard.html'),'utf8');
const T0=1800000000000;
const base={market:'UM',price:100,above200:true,live_cross:'',confirmed_cross:'',confirmed_at:0,live_cross_since:0,updated_at:T0,ws_connected:true,resync_pending:false,ema21_slope_pct:.01,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.01,volume_stale:false,quote_volume:5e7,base_volume:5e5};
const row=(symbol,extra={})=>({...base,symbol,...extra});
const state={server_time:T0,phase:'监测',loaded:4,total:4,skipped:0,ws_online:1,ws_expected:1,errors:[],rows:[]};
const events={server_time:T0,events:[]};
const notes=[],callbacks=[],errors=[];
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){
  w.AbortSignal.timeout=()=>undefined;
  w.setInterval=(fn,delay)=>{callbacks.push([fn,delay]);return callbacks.length;};
  w.fetch=async url=>{
    if(url==='/dif-api/state')return {ok:true,json:async()=>state};
    if(url==='/dif-api/events')return {ok:true,json:async()=>events};
    throw new Error('unexpected '+url);
  };
  w.Notification=class{constructor(title,opts){notes.push([title,opts.body]);}};
  w.Notification.permission='granted';
  w.addEventListener('error',e=>errors.push(e.error));
}});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
  await tick();const d=dom.window.document,w=dom.window;
  const poll=async()=>{await callbacks.find(x=>x[1]===2000)[0]();await tick();};
  const names=()=>[...d.querySelectorAll('#tbody .name')].map(e=>e.textContent);
  const click=f=>d.querySelector(`[data-filter="${f}"]`).click();
  const set=(id,v)=>{const el=d.querySelector('#'+id);el.value=v;el.dispatchEvent(new w.Event('input'));el.dispatchEvent(new w.Event('change'));};

  // 盘中金叉：按服务端记录的首次出现时间由新到旧，同时显示持续时长；最近更新时间不再影响顺序
  state.rows=[
    row('AAAUSDT',{live_cross:'golden',live_cross_since:T0-90000,updated_at:T0}),
    row('BBBUSDT',{live_cross:'golden',live_cross_since:T0-5000,updated_at:T0-50000}),
    row('CCCUSDT',{live_cross:'golden',live_cross_since:T0-90000,updated_at:T0-1000}),
  ];
  await poll();
  assert.deepEqual(names(),['BBBUSDT','AAAUSDT','CCCUSDT']);
  assert.match(d.querySelector('#tbody').textContent,/1分30秒/);
  assert.match(d.querySelector('#tbody').textContent,/5秒/);
  state.rows[2].updated_at=T0+5;await poll();
  assert.deepEqual(names(),['BBBUSDT','AAAUSDT','CCCUSDT']);

  // 已确认交叉标签：只列出 confirmed_cross 非空的合约，按确认时间由新到旧
  state.rows=[
    row('OLDUSDT',{confirmed_cross:'death',confirmed_at:T0-200000}),
    row('NEWUSDT',{confirmed_cross:'golden',confirmed_at:T0-10000}),
    row('NONEUSDT'),
  ];
  click('confirmed-any');await poll();
  assert.deepEqual(names(),['NEWUSDT','OLDUSDT']);
  assert.match(d.querySelector('#tbody').textContent,/已确认金叉/);
  assert.doesNotMatch(d.querySelector('#tbody').textContent,/盘中/);

  // 斜率信号：页面首次看到的时间排序，新出现的排在最上面，已有行不变
  state.rows=[row('ZZZUSDT',{ema21_slope_pct:.5}),row('MMMUSDT',{ema21_slope_pct:.5}),row('QQQUSDT')];
  click('slope-up');await poll();
  assert.deepEqual(names(),['MMMUSDT','ZZZUSDT']);
  state.server_time=T0+30000;state.rows[2].ema21_slope_pct=.5;await poll();
  assert.deepEqual(names(),['QQQUSDT','MMMUSDT','ZZZUSDT']);
  assert.match(d.querySelector('#tbody').textContent,/30秒/);

  // 最低成交额（万U）：U本位按成交额，币本位按币数量×价格；数据过期的合约不计入；统计卡片同步
  state.rows=[
    row('BIGUSDT',{ema21_slope_pct:.5,quote_volume:3e8}),
    row('SMALLUSDT',{ema21_slope_pct:.5,quote_volume:2e6}),
    row('BTCUSD_PERP',{market:'CM',ema21_slope_pct:.5,quote_volume:null,base_volume:4000,price:50000}),
    row('STALEUSDT',{ema21_slope_pct:.5,quote_volume:9e9,volume_stale:true}),
  ];
  await poll();
  assert.equal(names().length,4);
  set('min-turnover','10000');  // 1 亿
  assert.deepEqual(names().sort(),['BIGUSDT','BTCUSD_PERP']);
  assert.equal(d.querySelector('#m-slope-up').textContent,'2');
  assert.equal(w.localStorage.getItem('difradar.minTurnover'),'10000');
  set('min-turnover','0');assert.equal(names().length,4);

  // 信号记录：切换表头、展示后续涨跌幅，市场/搜索/成交额筛选同样生效，内容会转义
  events.events=[
    {id:2,market:'UM',symbol:'ETHUSDT',kind:'death',at:T0-600000,price:2500,turnover:4e8,funding_rate:-0.0001,ret_15m:-1.234,ret_30m:null,ret_60m:null,done:false},
    {id:1,market:'CM',symbol:'<img src=x onerror=bad>',kind:'golden',at:T0-7200000,price:60000,turnover:2e6,funding_rate:null,ret_15m:.5,ret_30m:1,ret_60m:2.25,done:true},
  ];
  click('history');await tick();await tick();
  const heads=[...d.querySelectorAll('thead th')].map(th=>th.textContent);
  assert.equal(heads.length,8);assert.equal(heads[0],'信号时间');
  assert.equal(d.querySelectorAll('#tbody tr').length,2);
  assert.equal(d.querySelectorAll('#tbody img').length,0);
  const body=d.querySelector('#tbody').textContent;
  assert.match(body,/已确认死叉/);assert.match(body,/-1\.23%/);assert.match(body,/\+2\.25%/);assert.match(body,/-0\.0100%/);
  set('min-turnover','10000');assert.deepEqual(names(),['ETHUSDT']);set('min-turnover','0');
  d.querySelector('#market').value='CM';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  assert.equal(d.querySelectorAll('#tbody tr').length,1);
  d.querySelector('#market').value='all';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  events.events=[];await poll();
  assert.match(d.querySelector('#empty-text').textContent,/--data-dir/);
  click('live-golden');
  assert.equal(d.querySelectorAll('thead th').length,8);assert.equal(d.querySelector('thead th').textContent,'交易对');

  // 提醒：默认关闭；打开后，同一标签页相邻两次轮询间新出现的信号触发一次通知
  state.rows=[row('AAAUSDT',{live_cross:'golden',live_cross_since:T0})];
  await poll();await poll();
  assert.equal(notes.length,0);
  assert.equal(d.querySelector('#notify').textContent,'提醒：关');
  d.querySelector('#notify').click();
  assert.equal(d.querySelector('#notify').textContent,'提醒：开');
  assert.equal(w.localStorage.getItem('difradar.notify'),'1');
  await poll();assert.equal(notes.length,0);  // 打开后先建立基线，已有信号不提醒
  state.rows.push(row('NEWUSDT',{live_cross:'golden',live_cross_since:T0+2000}));
  await poll();
  assert.equal(notes.length,1);assert.match(notes[0][0],/盘中金叉：1 个新信号/);assert.equal(notes[0][1],'NEWUSDT');
  await poll();assert.equal(notes.length,1);  // 同一信号不重复提醒
  // 同一合约重新出现（新的首次出现时间）视为新信号；切换标签不提醒
  state.rows[1].live_cross_since=T0+60000;await poll();assert.equal(notes.length,2);
  state.rows.push(row('DEADUSDT',{live_cross:'death',live_cross_since:T0}));
  click('live-death');await poll();assert.equal(notes.length,2);
  d.querySelector('#notify').click();assert.equal(w.localStorage.getItem('difradar.notify'),'0');
  state.rows.push(row('LATEUSDT',{live_cross:'death',live_cross_since:T0+9000}));await poll();assert.equal(notes.length,2);

  // 提醒开关跨刷新保存
  const d2=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w2){w2.localStorage.setItem('difradar.notify','1');w2.localStorage.setItem('difradar.minTurnover','50');w2.AbortSignal.timeout=()=>undefined;w2.setInterval=()=>0;w2.fetch=async()=>({ok:true,json:async()=>state});}});
  await tick();
  assert.equal(d2.window.document.querySelector('#notify').textContent,'提醒：开');
  assert.equal(d2.window.document.querySelector('#min-turnover').value,'50');
  d2.window.close();

  assert.deepEqual(errors,[]);dom.window.close();
  console.log('v1.1 UI: confirmed tab, stable sort and signal age, turnover filter, history, notifications: PASS');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
