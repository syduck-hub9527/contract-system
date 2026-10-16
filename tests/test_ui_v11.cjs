/* Surge workflow UI: stable sort + signal age, turnover and volume filters, surge history with cost, notifications. Dev only: npm install --no-save jsdom@30.1.2 */
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../contract_radar/assets/dashboard.html'),'utf8');
const T0=1800000000000;
const UP={ema21_slope_pct:.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02};
const DOWN={ema21_slope_pct:-.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02};
const base={market:'UM',price:100,above200:true,above50:true,updated_at:T0,ws_connected:true,resync_pending:false,ema21_slope_pct:.01,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.01,volume_stale:false,quote_volume:5e7,base_volume:5e5,volume_ratio:1};
const row=(symbol,extra={})=>({...base,symbol,...extra});
const state={server_time:T0,phase:'监测',loaded:4,total:4,skipped:0,ws_online:1,ws_expected:1,errors:[],rows:[]};
const events={server_time:T0,events:[]};
const notes=[],callbacks=[],errors=[],fetched=[];
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){
  w.AbortSignal.timeout=()=>undefined;
  w.setInterval=(fn,delay)=>{callbacks.push([fn,delay]);return callbacks.length;};
  w.fetch=async url=>{
    fetched.push(url);
    if(url==='/dif-api/state')return {ok:true,json:async()=>state};
    if(url==='/dif-api/events?kind=surge')return {ok:true,json:async()=>events};
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
  const names=()=>[...d.querySelectorAll('#tbody .name a')].map(e=>e.textContent);
  const body=()=>d.querySelector('#tbody').textContent;
  const click=f=>d.querySelector(`[data-filter="${f}"]`).click();
  const set=(id,v)=>{const el=d.querySelector('#'+id);el.value=v;el.dispatchEvent(new w.Event('input'));el.dispatchEvent(new w.Event('change'));};

  // 急拉：页面首次看到的时间排序，新出现的排在最上面，已有行不再随更新跳动；显示持续时间与量能
  state.rows=[row('ZZZUSDT',{...UP,volume_ratio:3.25}),row('MMMUSDT',UP),row('QQQUSDT')];
  await poll();
  assert.deepEqual(names(),['MMMUSDT','ZZZUSDT']);
  assert.match(body(),/量 3\.3×/);
  state.server_time=T0+30000;Object.assign(state.rows[2],UP);state.rows[0].updated_at=T0+29000;await poll();
  assert.deepEqual(names(),['QQQUSDT','MMMUSDT','ZZZUSDT']);
  assert.match(body(),/30秒/);

  // 最低成交额（万U）：U本位按成交额，币本位按币数量×价格；数据过期的合约不计入
  state.rows=[
    row('BIGUSDT',{...UP,quote_volume:3e8}),
    row('SMALLUSDT',{...UP,quote_volume:2e6}),
    row('BTCUSD_PERP',{...UP,market:'CM',quote_volume:null,base_volume:4000,price:50000}),
    row('STALEUSDT',{...UP,quote_volume:9e9,volume_stale:true}),
  ];
  await poll();
  assert.equal(names().length,4);
  set('min-turnover','10000');
  assert.deepEqual(names().sort(),['BIGUSDT','BTCUSD_PERP']);
  assert.equal(d.querySelector('#m-slope-up').textContent,'2');
  set('min-turnover','0');

  // 最低量能：只看放量信号；统计卡片“放量 急拉/急跌”按 2 倍计数
  state.rows=[row('LOUDUSDT',{...UP,volume_ratio:4}),row('QUIETUSDT',{...UP,volume_ratio:1.2}),row('NODATAUSDT',{...UP,volume_ratio:null}),row('DUMPUSDT',{...DOWN,volume_ratio:2.5})];
  await poll();
  assert.equal(d.querySelector('#m-surge-volume').textContent.replace(/\s+/g,''),'1/1');
  set('min-volume','2');
  assert.deepEqual(names(),['LOUDUSDT']);
  assert.equal(w.localStorage.getItem('difradar.minVolume'),'2');
  set('min-volume','0');assert.equal(names().length,3);

  // 信号记录：只请求急拉急跌；按方向计算并扣成本；汇总平均与盈利占比；筛选同样生效；内容会转义
  events.events=[
    {id:3,market:'UM',symbol:'ETHUSDT',kind:'surge_down',at:T0-600000,candle_t:T0-660000,elapsed_s:60,price:2500,volume_ratio:3.2,turnover:4e8,funding_rate:-0.0001,above50:false,above200:true,held_at_close:true,ret_5m:-1.0,ret_15m:-2.0,ret_30m:null,ret_60m:null,high_pct:0.3,low_pct:-2.5,done:false},
    {id:2,market:'CM',symbol:'<img src=x onerror=bad>',kind:'surge_up',at:T0-7200000,price:60000,volume_ratio:1.1,turnover:2e6,funding_rate:null,above50:true,above200:true,held_at_close:false,ret_5m:0.2,ret_15m:-0.5,ret_30m:1,ret_60m:2.25,high_pct:2.5,low_pct:-0.8,done:true},
    {id:1,market:'UM',symbol:'BTCUSDT',kind:'golden',at:T0-9000000,price:60000},
  ];
  click('history');await tick();await tick();
  assert.ok(fetched.includes('/dif-api/events?kind=surge'));
  const heads=[...d.querySelectorAll('thead th')].map(th=>th.textContent);
  assert.equal(heads.length,8);assert.equal(heads[0],'触发时间');
  assert.equal(d.querySelectorAll('#tbody tr').length,2);  // 交叉事件不显示
  assert.equal(d.querySelectorAll('#tbody img').length,0);
  // 急跌按做空：价格跌 2% = 收益 +2%，扣 0.10% 成本 = +1.90%；最大顺 = +2.50%，最大逆 = -0.30%
  let eth=d.querySelectorAll('#tbody tr')[0];
  assert.match(eth.children[6].textContent,/\+0\.90%.*\+1\.90%.*—.*—/);
  assert.match(eth.children[7].textContent,/\+2\.50% \/ -0\.30%/);
  assert.match(eth.children[2].textContent,/急跌.*收盘仍成立/);
  assert.match(eth.children[5].textContent,/EMA50 ↓.*EMA200 ↑/);
  assert.match(eth.children[2].title,/开盘后 60 秒/);
  assert.match(d.querySelector('#count').textContent,/记录 2 条.*平均 \+0\.65%.*盈利占比 50%.*样本 2/);
  set('cost','0.5');
  eth=d.querySelectorAll('#tbody tr')[0];
  assert.match(eth.children[6].textContent,/\+0\.50%.*\+1\.50%/);
  assert.equal(w.localStorage.getItem('difradar.cost'),'0.5');
  set('cost','0.10');
  set('min-volume','2');assert.equal(d.querySelectorAll('#tbody tr').length,1);set('min-volume','0');
  d.querySelector('#market').value='CM';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  assert.equal(d.querySelectorAll('#tbody tr').length,1);
  d.querySelector('#market').value='all';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  // 记录较大，轮询时最多每 10 秒重新获取一次
  const before=fetched.filter(u=>u.includes('events')).length;await poll();
  assert.equal(fetched.filter(u=>u.includes('events')).length,before);
  events.events=[];click('history');await tick();await tick();
  assert.match(d.querySelector('#empty-text').textContent,/--data-dir/);
  click('slope-up');
  assert.equal(d.querySelectorAll('thead th').length,8);assert.equal(d.querySelector('thead th').textContent,'交易对');

  // 提醒：默认关闭；打开后，同一标签页相邻两次轮询间新出现的信号触发一次通知
  state.rows=[row('AAAUSDT',UP)];
  await poll();await poll();
  assert.equal(notes.length,0);
  assert.equal(d.querySelector('#notify').textContent,'提醒：关');
  d.querySelector('#notify').click();
  assert.equal(d.querySelector('#notify').textContent,'提醒：开');
  assert.equal(w.localStorage.getItem('difradar.notify'),'1');
  await poll();assert.equal(notes.length,0);  // 打开后先建立基线，已有信号不提醒
  state.server_time+=2000;state.rows.push(row('NEWUSDT',UP));
  await poll();
  assert.equal(notes.length,1);assert.match(notes[0][0],/EMA21 急拉：1 个新信号/);assert.equal(notes[0][1],'NEWUSDT');
  await poll();assert.equal(notes.length,1);  // 同一信号不重复提醒
  state.rows.push(row('DUMPUSDT',DOWN));
  click('slope-down');await poll();assert.equal(notes.length,1);  // 切换标签只建立基线
  d.querySelector('#notify').click();assert.equal(w.localStorage.getItem('difradar.notify'),'0');
  state.server_time+=2000;state.rows.push(row('LATEUSDT',DOWN));await poll();assert.equal(notes.length,1);

  // 提醒、最低成交额、最低量能、往返成本跨刷新保存
  const d2=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w2){
    w2.localStorage.setItem('difradar.notify','1');w2.localStorage.setItem('difradar.minTurnover','50');
    w2.localStorage.setItem('difradar.minVolume','1.5');w2.localStorage.setItem('difradar.cost','0.08');
    w2.AbortSignal.timeout=()=>undefined;w2.setInterval=()=>0;w2.fetch=async()=>({ok:true,json:async()=>state});}});
  await tick();
  const q=id=>d2.window.document.querySelector(id);
  assert.equal(q('#notify').textContent,'提醒：开');assert.equal(q('#min-turnover').value,'50');
  assert.equal(q('#min-volume').value,'1.5');assert.equal(q('#cost').value,'0.08');
  d2.window.close();

  assert.deepEqual(errors,[]);dom.window.close();
  console.log('Surge workflow UI: stable sort, filters, surge history with cost, notifications: PASS');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
