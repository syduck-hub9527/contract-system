/* Core UI: surge tabs, thresholds, persistence, escaping, volume and funding cells. Dev only: npm install --no-save jsdom@30.1.2 */
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../contract_radar/assets/dashboard.html'),'utf8');
const callbacks=[],errors=[];
const row=(symbol,time,extra={})=>({symbol,market:'UM',price:100,dif:.001,dif_pct:.001,above200:true,above50:true,live_cross:'',confirmed_cross:'',updated_at:time,ws_connected:true,resync_pending:false,ema21_slope_pct:.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02,...extra});
const state={server_time:1800000000000,phase:'监测',loaded:3,total:3,skipped:0,ws_online:1,ws_expected:1,errors:[],rows:[row('OLDERUSDT',1799999999000),row('BTCUSDT',1800000000000,{live_cross:'golden',confirmed_cross:'death',ema21_slope_pct:.01,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.01}),row('ETHUSD_PERP',1799999999990,{market:'CM',above200:false,above50:false})]};
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){
 w.AbortSignal.timeout=()=>undefined;
 w.setInterval=(fn,delay)=>{callbacks.push([fn,delay]);return callbacks.length;};
 w.fetch=async url=>{if(url.startsWith('/dif-api/events?kind=surge&since='))return {ok:true,json:async()=>({server_time:state.server_time,events:[]})};assert.equal(url,'/dif-api/state');return {ok:true,json:async()=>state};};
 w.addEventListener('error',e=>errors.push(e.error));
}});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await tick();const d=dom.window.document;
 const names=()=>[...d.querySelectorAll('#tbody .name')].map(e=>e.textContent);
 const click=filter=>d.querySelector(`[data-filter="${filter}"]`).click();
 const refresh=()=>callbacks.find(x=>x[1]===2000)[0]();
 // 只有三个标签：急拉（默认）、急跌、信号记录；交叉信号不再出现在页面上
 assert.deepEqual([...d.querySelectorAll('[data-filter]')].map(b=>b.dataset.filter),['slope-up','slope-down','history']);
 assert.equal(d.querySelector('[data-filter="slope-up"]').classList.contains('active'),true);
 assert.deepEqual(names(),['ETHUSD_PERP','OLDERUSDT']);
 assert.match(d.querySelector('#tbody').textContent,/EMA21急拉/);
 assert.doesNotMatch(d.querySelector('#tbody').textContent,/金叉|死叉/);
 assert.equal(d.body.textContent.includes('盘中金叉'),false);assert.equal(d.querySelector('#threshold'),null);
 const older=state.rows[0],eth=state.rows[2];
 assert.equal(d.querySelector('#m-slope-up').textContent,'2');assert.equal(d.querySelector('#m-slope-down').textContent,'0');
 click('slope-down');assert.deepEqual(names(),[]);
 // 急跌：斜率转为大幅负值
 eth.ema21_slope_pct=-.5;await refresh();assert.deepEqual(names(),['ETHUSD_PERP']);assert.match(d.querySelector('#tbody').textContent,/EMA21急跌/);
 click('slope-up');assert.deepEqual(names(),['OLDERUSDT']);
 // 变化量低于“最小变化”(默认0.100%)不提示；高于此前均值幅度倍数才提示
 eth.ema21_slope_pct=.05;eth.ema21_slope_base_pct=0;await refresh();assert.deepEqual(names(),['OLDERUSDT']);
 eth.ema21_slope_pct=.5;eth.ema21_slope_base_pct=.01;eth.ema21_slope_base_abs_pct=.2;await refresh();assert.deepEqual(names(),['OLDERUSDT']);  // 变化0.49 < 3×0.2
 eth.ema21_slope_base_abs_pct=.02;await refresh();assert.deepEqual(names(),['ETHUSD_PERP','OLDERUSDT']);
 // 数据不足（斜率为 null）时不触发
 older.ema21_slope_pct=null;older.ema21_slope_base_pct=null;older.ema21_slope_base_abs_pct=null;await refresh();assert.deepEqual(names(),['ETHUSD_PERP']);
 older.ema21_slope_pct=.5;older.ema21_slope_base_pct=.01;older.ema21_slope_base_abs_pct=.02;await refresh();
 // 参数可调：倍数/最小变化改变后立即生效
 const mult=d.querySelector('#slope-mult'),min=d.querySelector('#slope-min'),ev=n=>new dom.window.Event(n);
 min.value='1';min.dispatchEvent(ev('input'));min.dispatchEvent(ev('change'));assert.deepEqual(names(),[]);assert.equal(dom.window.localStorage.getItem('difradar.slopeMin'),'1');
 d.querySelector('#threshold-reset').click();assert.equal(mult.value,'3.0');assert.equal(min.value,'0.100');assert.deepEqual(names(),['ETHUSD_PERP','OLDERUSDT']);
 mult.value='0';mult.dispatchEvent(ev('input'));mult.dispatchEvent(ev('change'));assert.equal(mult.value,'0.5');d.querySelector('#threshold-reset').click();
 d.querySelector('#market').value='CM';d.querySelector('#market').dispatchEvent(new dom.window.Event('change'));assert.deepEqual(names(),['ETHUSD_PERP']);
 d.querySelector('#market').value='all';d.querySelector('#market').dispatchEvent(new dom.window.Event('change'));
 d.querySelector('#query').value='OLD';d.querySelector('#query').dispatchEvent(new dom.window.Event('input'));assert.deepEqual(names(),['OLDERUSDT']);
 d.querySelector('#query').value='';d.querySelector('#query').dispatchEvent(new dom.window.Event('input'));
 // 趋势列：EMA50 / EMA200 上下
 const rowOf=symbol=>[...d.querySelectorAll('#tbody tr')].find(tr=>tr.querySelector('.name').textContent===symbol);
 assert.equal(d.querySelectorAll('thead th').length,8);assert.equal(d.querySelectorAll('thead th')[6].textContent,'趋势');
 assert.match(rowOf('OLDERUSDT').children[6].textContent,/EMA50 ↑.*EMA200 ↑/);
 assert.match(rowOf('ETHUSD_PERP').children[6].textContent,/EMA50 ↓.*EMA200 ↓/);
 state.rows[0].symbol='<img src=x onerror=bad>USDT';await refresh();
 assert.equal(d.querySelectorAll('#tbody img').length,0);
 state.rows[0].symbol='OLDERUSDT';
 // 成交量与资金费率单元格（在急拉标签页验证；BTC 设为急拉）
 const btc=state.rows[1],cm=state.rows[2];
 Object.assign(btc,{ema21_slope_pct:.5,ema21_slope_base_pct:.01,ema21_slope_base_abs_pct:.02});
 Object.assign(btc,{volume:0,volume_unit:'BTC',base_volume:0,quote_volume:0,quote_asset:'USDT',volume_stale:false,volume_updated_at:state.server_time});
 Object.assign(cm,{volume:494109,volume_unit:'张',base_volume:5192.94797687,base_asset:'BTC',quote_volume:null,volume_stale:false,volume_updated_at:state.server_time});
 await refresh();
 const cell=symbol=>rowOf(symbol).children[3];
 assert.equal(cell('BTCUSDT').textContent.trim(),'0');
 assert.match(cell('BTCUSDT').querySelector('span').title,/成交额 0 USDT/);
 assert.equal(cell('ETHUSD_PERP').textContent.trim(),'49.41 万');
 assert.match(cell('ETHUSD_PERP').querySelector('span').title,/精确值 494,109 张/);
 assert.match(cell('ETHUSD_PERP').querySelector('span').title,/币数量 5,192.95 BTC/);
 assert.equal(cell('OLDERUSDT').textContent,'—');
 const fund=symbol=>rowOf(symbol).children[4];
 assert.equal(fund('BTCUSDT').textContent,'—');
 Object.assign(btc,{funding_rate:0.0001,next_funding_time:state.server_time+3600000,funding_updated_at:state.server_time,funding_stale:false});
 Object.assign(cm,{funding_rate:-0.00025,next_funding_time:state.server_time+3600000,funding_updated_at:state.server_time,funding_stale:false});
 await refresh();
 assert.equal(fund('BTCUSDT').textContent,'+0.0100%');assert.equal(fund('BTCUSDT').querySelector('span').className,'pos');
 assert.equal(fund('ETHUSD_PERP').textContent,'-0.0250%');assert.equal(fund('ETHUSD_PERP').querySelector('span').className,'neg');
 btc.funding_stale=true;await refresh();assert.equal(fund('BTCUSDT').textContent,'—');
 btc.funding_stale=false;btc.funding_rate=null;await refresh();assert.equal(fund('BTCUSDT').textContent,'—');
 btc.volume_stale=true;await refresh();assert.equal(cell('BTCUSDT').textContent,'—');assert.match(cell('BTCUSDT').querySelector('span').title,/过期/);
 btc.volume_stale=false;btc.volume=null;await refresh();assert.equal(cell('BTCUSDT').textContent,'—');
 btc.volume=NaN;await refresh();assert.equal(cell('BTCUSDT').textContent,'—');
 btc.volume=12;btc.volume_unit='<img src=x onerror=bad>';btc.quote_asset='" onmouseover="bad';await refresh();
 assert.equal(d.querySelectorAll('#tbody img').length,0);assert.equal(cell('BTCUSDT').querySelectorAll('[onmouseover]').length,0);
 // 统计卡片：急拉/急跌数量
 state.rows[2].ema21_slope_pct=-.5;await refresh();
 assert.equal(d.querySelector('#m-slope-up').textContent,'2');assert.equal(d.querySelector('#m-slope-down').textContent,'1');
 state.rows[2].ema21_slope_pct=.5;await refresh();
 // 参数跨刷新保存
 const m0=d.querySelector('#slope-mult');m0.value='4';m0.dispatchEvent(new dom.window.Event('input'));
 assert.equal(dom.window.localStorage.getItem('difradar.slopeMult'),'4');d.querySelector('#threshold-reset').click();assert.equal(dom.window.localStorage.getItem('difradar.slopeMult'),'3.0');
 const d2=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){w.localStorage.setItem('difradar.slopeMin','0.036');w.AbortSignal.timeout=()=>undefined;w.setInterval=()=>0;w.fetch=async()=>({ok:true,json:async()=>state});}});
 await tick();assert.equal(d2.window.document.querySelector('#slope-min').value,'0.036');d2.window.close();
 assert.deepEqual(errors,[]);dom.window.close();console.log('Surge UI: tabs, thresholds, trend column, volume and funding cells, escaping: PASS');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
