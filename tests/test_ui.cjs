/* Compatibility checks for the untouched UI. Dev only: npm install --no-save jsdom@30.1.2 */
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const html=fs.readFileSync(path.join(__dirname,'../dashboard.html'),'utf8');
const callbacks=[],errors=[];
const row=(symbol,time,extra={})=>({symbol,market:'UM',price:100,dif:.001,dif_pct:.001,above200:true,live_cross:'',confirmed_cross:'',updated_at:time,ws_connected:true,resync_pending:false,...extra});
const state={server_time:1800000000000,phase:'监测',loaded:3,total:3,skipped:0,ws_online:1,ws_expected:1,errors:[],rows:[row('OLDERUSDT',1799999999000),row('BTCUSDT',1800000000000,{live_cross:'golden',confirmed_cross:'death'}),row('ETHUSD_PERP',1799999999990,{market:'CM',above200:false})]};
const dom=new JSDOM(html,{url:'http://127.0.0.1:8765',runScripts:'dangerously',beforeParse(w){
 w.AbortSignal.timeout=()=>undefined;
 w.setInterval=(fn,delay)=>{callbacks.push([fn,delay]);return callbacks.length;};
 w.fetch=async url=>{assert.equal(url,'/dif-api/state');return {ok:true,json:async()=>state};};
 w.addEventListener('error',e=>errors.push(e.error));
}});
const tick=()=>new Promise(r=>setImmediate(r));
(async()=>{
 await tick();const d=dom.window.document;
 const names=()=>[...d.querySelectorAll('#tbody .name')].map(e=>e.textContent);
 assert.deepEqual(names(),['BTCUSDT','ETHUSD_PERP','OLDERUSDT']);
 const click=filter=>d.querySelector(`[data-filter="${filter}"]`).click();
 click('live-golden');assert.deepEqual(names(),['BTCUSDT']);assert.match(d.querySelector('#tbody').textContent,/盘中金叉/);assert.doesNotMatch(d.querySelector('#tbody').textContent,/确认死叉/);
 click('confirmed-death');assert.deepEqual(names(),['BTCUSDT']);assert.match(d.querySelector('#tbody').textContent,/确认死叉/);assert.doesNotMatch(d.querySelector('#tbody').textContent,/盘中金叉/);
 click('all');
 d.querySelector('#market').value='CM';d.querySelector('#market').dispatchEvent(new dom.window.Event('change'));assert.deepEqual(names(),['ETHUSD_PERP']);
 d.querySelector('#market').value='all';d.querySelector('#query').value='BTC';d.querySelector('#query').dispatchEvent(new dom.window.Event('input'));assert.deepEqual(names(),['BTCUSDT']);
 d.querySelector('#query').value='';d.querySelector('#query').dispatchEvent(new dom.window.Event('input'));
 state.rows[0].symbol='<img src=x onerror=bad>USDT';await callbacks.find(x=>x[1]===2000)[0]();
 assert.equal(d.querySelectorAll('#tbody img').length,0);
 assert.deepEqual(errors,[]);dom.window.close();console.log('Original UI sorting, filters, badges and escaping: PASS');
})().catch(e=>{dom.window.close();console.error(e);process.exitCode=1;});
