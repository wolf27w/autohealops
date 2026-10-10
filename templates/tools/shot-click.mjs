import { spawn } from 'node:child_process'
import { writeFileSync } from 'node:fs'
// Usage: node shot-click.mjs <url> <outPng> <clickText> [waitMs] [width] [height]
const [url, out, clickText, waitMs = '1500', w = '1440', h = '900'] = process.argv.slice(2)
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const PORT = 9336
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
  '--hide-scrollbars', `--remote-debugging-port=${PORT}`,'--remote-allow-origins=*',
  '--user-data-dir=/tmp/chrome-cdp4','about:blank'], { stdio:'ignore' })
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
await send('Emulation.setDeviceMetricsOverride',{width:+w,height:+h,deviceScaleFactor:2,mobile:false},sessionId)
await send('Page.navigate',{url},sessionId)
await sleep(3500)
if (clickText) {
  const expr = `(()=>{const b=[...document.querySelectorAll('button,a,span[role="button"]')].find(x=>x.textContent.includes(${JSON.stringify(clickText)}));if(!b)return 'NOT_FOUND';b.click();return 'CLICKED'})()`
  const r = await send('Runtime.evaluate',{expression:expr,returnByValue:true},sessionId)
  console.log('click:', r.result?.result?.value)
  await sleep(+waitMs)
}
const shot = await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},sessionId)
if (!shot.result || !shot.result.data) { console.error('NO DATA'); process.exit(2) }
writeFileSync(out, Buffer.from(shot.result.data,'base64'))
console.log('saved', out)
proc.kill('SIGKILL'); process.exit(0)
