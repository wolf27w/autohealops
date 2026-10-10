import { spawn } from 'node:child_process'
// Usage: node dump-modal.mjs <url> <clickText>
const [url, clickText] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9337
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*','--user-data-dir=/tmp/chrome-cdp5','about:blank'], { stdio:'ignore' })
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
const click = `(()=>{const b=[...document.querySelectorAll('button,a')].find(x=>x.textContent.includes(${JSON.stringify(clickText)}));b.click();return 'ok'})()`
await send('Runtime.evaluate',{expression:click,returnByValue:true},sessionId)
await sleep(1500)
const expr = `(()=>{
  const modal=document.querySelector('.ant-modal');
  if(!modal) return JSON.stringify({err:'no modal'});
  const labels=[...modal.querySelectorAll('.ant-form-item-label label')].map(l=>l.innerText.trim());
  const btns=[...modal.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(Boolean);
  const hasReadonlyInput=modal.querySelector('input[id*="readonly"]')!==null;
  return JSON.stringify({labels, linkButtons: btns.filter(t=>t.includes('高级')||t.includes('只读')), hasReadonlyInput}, null, 1);
})()`
const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
console.log(r.result.result.value)
proc.kill('SIGKILL'); process.exit(0)
