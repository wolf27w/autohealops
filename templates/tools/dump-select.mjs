import { spawn } from 'node:child_process'
// Usage: node dump-select.mjs <url>
const [url] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9341
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*','--user-data-dir=/tmp/chrome-cdp9','about:blank'], { stdio:'ignore' })
const sleep = (ms) => new Promise(r=>setTimeout(r,ms))
async function version(){for(let i=0;i<60;i++){try{const r=await fetch(`http://127.0.0.1:${PORT}/json/version`);return await r.json()}catch{await sleep(200)}}throw new Error('no cdp')}
const v = await version()
const ws = new WebSocket(v.webSocketDebuggerUrl)
let id=0; const pending=new Map()
ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id&&pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}})
await new Promise(r=>ws.addEventListener('open',r))
const send=(method,params={},sessionId)=>new Promise(res=>{const i=++id;pending.set(i,res);ws.send(JSON.stringify(sessionId?{id:i,method,params,sessionId}:{id:i,method,params}))})
const {result:{targetId}}=await send('Target.createTarget',{url:'about:blank'})
const {result:{sessionId}}=await send('Target.attachToTarget',{targetId,flatten:true})
await send('Page.enable',{},sessionId)
await send('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:2,mobile:false},sessionId)
await send('Page.navigate',{url},sessionId)
await sleep(3500)
const click = (t) => `(()=>{const el=[...document.querySelectorAll('button,a,.ant-tabs-tab,.ant-select-selector')].find(x=>x.textContent.replace(/\\s+/g,' ').includes(${JSON.stringify(t)}));if(!el)return 'NOT_FOUND';el.click();return 'CLICKED'})()`
await send('Runtime.evaluate',{expression:click('权限管理'),returnByValue:true},sessionId); await sleep(900)
await send('Runtime.evaluate',{expression:click('按集群授权'),returnByValue:true},sessionId); await sleep(900)
await send('Runtime.evaluate',{expression:click('选择项目'),returnByValue:true},sessionId); await sleep(1500)
const expr = `(()=>{
  const opts=[...document.querySelectorAll('.ant-select-item-option, .ant-select-item')].map(o=>({t:o.textContent.trim(), cls:o.className.slice(0,60)}));
  const dropdowns=document.querySelectorAll('.ant-select-dropdown').length;
  return JSON.stringify({dropdowns, opts}, null, 1);
})()`
const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
console.log(r.result.result.value)
proc.kill('SIGKILL'); process.exit(0)
