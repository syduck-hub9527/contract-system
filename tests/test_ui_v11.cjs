/* Surge workflow UI: stable sort + signal age, 5-minute rolling signal record, notifications. Dev only: npm install --no-save jsdom@30.1.2 */
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../contract_radar/assets/dashboard.html'),'utf8');
const T0=1800000000000,MIN=60000;
const UP={ema21_slope_pct:.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02};
const DOWN={ema21_slope_pct:-.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02};
const base={market:'UM',price:100,above200:true,above50:true,updated_at:T0,ws_connected:true,resync_pending:false,ema21_slope_pct:.01,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.01,volume_stale:false,quote_volume:5e7,base_volume:5e5,volume_ratio:3};
const row=(symbol,extra={})=>({...base,symbol,...extra});
const state={server_time:T0,phase:'监测',loaded:4,total:4,skipped:0,ws_online:1,ws_expected:1,errors:[],rows:[]};
let allEvents=[];
const notes=[],callbacks=[],errors=[],fetched=[];
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){
  w.AbortSignal.timeout=()=>undefined;
  w.setInterval=(fn,delay)=>{callbacks.push([fn,delay]);return callbacks.length;};
  w.fetch=async url=>{
    fetched.push(url);
    if(url==='/dif-api/state')return {ok:true,json:async()=>state};
    const m=/^\/dif-api\/events\?kind=surge&since=(-?\d+)$/.exec(url);
    if(m)return {ok:true,json:async()=>({server_time:state.server_time,events:allEvents.filter(e=>e.at>=+m[1])})};
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

  // 删除的输入框与量能不再出现在页面上
  for(const id of ['#min-turnover','#min-volume','#cost'])assert.equal(d.querySelector(id),null);
  assert.equal(d.body.textContent.includes('量能'),false);

  // 急拉：页面首次看到的时间排序，新出现的排在最上面，已有行不再随更新跳动；显示持续时间
  state.rows=[row('ZZZUSDT',UP),row('MMMUSDT',UP),row('QQQUSDT')];
  await poll();
  assert.deepEqual(names(),['MMMUSDT','ZZZUSDT']);
  assert.doesNotMatch(body(),/量 /);
  state.server_time=T0+30000;Object.assign(state.rows[2],UP);state.rows[0].updated_at=T0+29000;await poll();
  assert.deepEqual(names(),['QQQUSDT','MMMUSDT','ZZZUSDT']);
  assert.match(body(),/30秒/);

  // 信号记录：只请求并显示最近5分钟；超过5分钟自动移除；卡片显示最近5分钟的急拉/急跌条数
  const now=state.server_time;
  allEvents=[
    {id:4,market:'UM',symbol:'NEWUSDT',kind:'surge_down',at:now-10000,price:2500,above50:false,above200:true,held_at_close:null,volume_ratio:3.2,ret_5m:-1},
    {id:3,market:'CM',symbol:'<img src=x onerror=bad>',kind:'surge_up',at:now-2*MIN,price:60000,above50:true,above200:true,held_at_close:true},
    {id:2,market:'UM',symbol:'EDGEUSDT',kind:'surge_up',at:now-4.9*MIN,price:1.5,above50:true,above200:false,held_at_close:false},
    {id:1,market:'UM',symbol:'OLDUSDT',kind:'surge_up',at:now-6*MIN,price:9,above50:true,above200:true,held_at_close:true},
  ];
  await poll();
  assert.ok(fetched.includes(`/dif-api/events?kind=surge&since=${now-5*MIN}`));
  assert.equal(d.querySelector('#m-recent').textContent.replace(/\s+/g,''),'2/1');
  click('history');await tick();
  const heads=[...d.querySelectorAll('thead th')].map(th=>th.textContent);
  assert.deepEqual(heads,['触发时间','交易对','信号','触发价','趋势']);
  assert.equal(d.querySelectorAll('#tbody tr').length,3);
  assert.equal(d.querySelectorAll('#tbody img').length,0);
  assert.doesNotMatch(body(),/OLDUSDT/);
  assert.doesNotMatch(body(),/量能|3\.2×|-1\.00%/);
  const first=d.querySelectorAll('#tbody tr')[0];
  assert.match(first.textContent,/NEWUSDT/);assert.match(first.children[2].textContent,/急跌/);
  assert.match(first.children[4].textContent,/EMA50 ↓.*EMA200 ↑/);
  assert.match(body(),/收盘仍成立/);assert.match(body(),/收盘已回落/);
  assert.match(d.querySelector('#count').textContent,/最近5分钟 急拉 2 条.*急跌 1 条/);
  // 时间推进 20 秒：EDGEUSDT 超过5分钟，自动移除（即使接口还没刷新）
  state.server_time=now+20000;
  click('slope-up');click('history');
  assert.doesNotMatch(body(),/EDGEUSDT/);
  assert.equal(d.querySelectorAll('#tbody tr').length,2);
  // 市场与搜索筛选
  d.querySelector('#market').value='CM';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  assert.equal(d.querySelectorAll('#tbody tr').length,1);
  d.querySelector('#market').value='all';d.querySelector('#market').dispatchEvent(new w.Event('change'));
  d.querySelector('#query').value='NEW';d.querySelector('#query').dispatchEvent(new w.Event('input'));
  assert.deepEqual(names(),['NEWUSDT']);
  d.querySelector('#query').value='';d.querySelector('#query').dispatchEvent(new w.Event('input'));
  allEvents=[];state.server_time=now+6*MIN;await poll();
  assert.match(d.querySelector('#empty-text').textContent,/最近5分钟没有急拉急跌/);
  assert.equal(d.querySelector('#m-recent').textContent.replace(/\s+/g,''),'0/0');
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

  // 提醒开关跨刷新保存
  const d2=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w2){
    w2.localStorage.setItem('difradar.notify','1');
    w2.AbortSignal.timeout=()=>undefined;w2.setInterval=()=>0;w2.fetch=async()=>({ok:true,json:async()=>state});}});
  await tick();
  assert.equal(d2.window.document.querySelector('#notify').textContent,'提醒：开');
  d2.window.close();

  assert.deepEqual(errors,[]);dom.window.close();
  console.log('Surge workflow UI: stable sort, 5-minute rolling signal record, notifications: PASS');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
