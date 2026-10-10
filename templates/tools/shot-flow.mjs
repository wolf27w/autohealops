import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
// Usage: node shot-flow.mjs <url> <outPng> <click1> <click2> ... <clickN>
// 依次点击匹配文本的元素，最后截图
const [url, out, ...clicks] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9338
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*',
  '--user-data-dir=/tmp/chrome-cdp6','about:blank'], { stdio:'ignore' })
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
for (const t of clicks) {
  const expr = `(()=>{const el=[...document.querySelectorAll('button,a,.ant-tabs-tab,.ant-menu-item')].find(x=>x.textContent.replace(/\\s+/g,' ').includes(${JSON.stringify(t)}));if(!el)return 'NOT_FOUND';el.click();return 'CLICKED:'+${JSON.stringify(t)}})()`
  const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
  console.log('click', r.result?.result?.value)
  await sleep(900)
}
const shot = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId)
if (!shot.result || !shot.result.data) { console.error('NO DATA'); process.exit(2) }
writeFileSync(out, Buffer.from(shot.result.data,'base64'))
console.log('saved', out)
proc.kill('SIGKILL'); process.exit(0)
