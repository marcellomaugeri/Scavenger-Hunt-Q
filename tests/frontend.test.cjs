/* Browser behavioural checks; run with: node tests/frontend.test.cjs
 * Requires local Chrome/Chromium (CHROME_BIN can override the executable).
 * Transport and audio output are stubbed; rendering and DOM behaviour are real.
 */
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../app/assets');
const browserNames = process.platform === 'win32' ? ['chrome.exe', 'msedge.exe'] : ['chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable'];
const candidates = (process.env.PATH || '').split(path.delimiter).flatMap(dir => browserNames.map(name => path.join(dir, name)));
candidates.push('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');
for (const dir of [process.env.PROGRAMFILES, process.env['PROGRAMFILES(X86)'], process.env.LOCALAPPDATA].filter(Boolean)) {
  candidates.push(path.join(dir, 'Google/Chrome/Application/chrome.exe'), path.join(dir, 'Microsoft/Edge/Application/msedge.exe'));
}
const chrome = process.env.CHROME_BIN || candidates.find(file => fs.existsSync(file));
if (!chrome) throw new Error('Chrome/Chromium was not found. Set CHROME_BIN to its executable path.');
const setup = `
window.played = []; window.pausedSounds = []; window.commands = []; window.testTime = 0;
Object.defineProperty(performance, 'now', {value: () => window.testTime});
window.Audio = class { constructor(src){this.src=src;} play(){played.push(this.src);return Promise.resolve();} pause(){pausedSounds.push(this.src);} };
window.musicSources=[]; window.musicLoads=0;
window.fetch=async () => {musicLoads++;return {ok:true,arrayBuffer:async()=>new ArrayBuffer(8)};};
window.musicDecode=new Promise(resolve=>{window.releaseMusic=()=>resolve({duration:30});});
window.AudioContext=class {
 constructor(){this.state='suspended';this.currentTime=0;this.destination={};window.musicContext=this;}
 resume(){this.state='running';return Promise.resolve();}
 decodeAudioData(){return musicDecode;}
 createGain(){return {connect(){},disconnect(){},gain:{value:0,setValueAtTime(){},linearRampToValueAtTime(){},cancelScheduledValues(){}}};}
 createBufferSource(){const source={connect(){},disconnect(){},start(){this.started=true;},stop(){this.stopped=true;}};musicSources.push(source);return source;}
};
window.WebUI = class { on_message(name,cb){if(name==='game_state')window.receive=cb;else if(name==='recognition')window.receiveRecognition=cb;} on_connect(cb){window.reconnect=cb;} on_disconnect(cb){window.disconnect=cb;} send_message(type,payload){commands.push({type,payload});} };
`;
const checks = `
(async () => {
 const failures=[]; let count=0;
 function check(condition,label){count++;if(!condition)failures.push(label);}
 function shown(id){return !document.getElementById(id).hidden;}
 function emit(next){receive({camera_ready:true,...next});}
 function clicks(command){document.querySelector('[data-command="'+command+'"]').click();}
 function snd(name){return played.filter(p=>p.includes(name)).length;}
 const base={phase:'menu',revision:1,total_rounds:10,current_round:1,scores:{red:0,blue:0},round_seconds:60,round_remaining:60,countdown_remaining:3,clue:'I hold a drink but never take a sip.',recaps:[],result:null,paused:false};
 emit(base);
 check(shown('menu'),'menu visible');
 const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
 await settle();
 emit({phase:'preparing',revision:1});
 releaseMusic();await settle();
 check(musicSources.length===0,'late music decode cannot start during a hunt');
 emit(base);await settle();
 check(musicSources.length===1&&musicSources[0].started&&musicSources[0].loop,'menu automatically starts one looping buffer');
 emit(base);emit(base);await settle();
 check(musicSources.length===1&&musicLoads===1,'menu heartbeats do not restart or reload music');
 musicContext.state='suspended';
 document.dispatchEvent(new Event('pointerdown'));
 check(musicContext.state==='running'&&musicSources.length===1,'first input resumes blocked autoplay without another loop');
 check(document.querySelectorAll('[data-command]:disabled').length===0,'connected controls enabled');
 clicks('start_game');check(commands.at(-1).type==='start_game','click starts game');
 emit({phase:'settings',revision:2});
 emit({phase:'settings',revision:2,voice_status:'Voice controls: temporary setup'});
 check(!document.getElementById('voice-status')&&!document.getElementById('settings').textContent.includes('temporary setup'),'voice setup status is never rendered');
 check(!musicSources[0]?.stopped,'menu music continues into settings');
 document.querySelector('[data-seconds="120"]').click();
 check(commands.at(-1).type==='set_round_seconds'&&commands.at(-1).payload.seconds===120,'timer sends numeric duration');
 emit({phase:'settings',round_seconds:120,revision:3});
 check(document.querySelector('[data-seconds="120"]').getAttribute('aria-checked')==='true','timer selection reflected');
 const beforeKeys=commands.length;
 const timer=document.querySelector('[data-seconds="30"]');timer.focus();
 let allKeysBlocked=true;
 for(const key of ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Tab','Enter',' ','Escape','m','p']) {
  for(const type of ['keydown','keypress','keyup']) {
   const event=new KeyboardEvent(type,{key,bubbles:true,cancelable:true});
   timer.dispatchEvent(event);allKeysBlocked&&=event.defaultPrevented;
  }
 }
 check(allKeysBlocked,'keyboard navigation and native activation defaults are blocked');
 check(commands.length===beforeKeys,'keyboard cannot change timer or navigate');
 check(document.activeElement===timer,'keyboard does not move focus');
 const beforeRecovery=commands.length;
 emit({phase:'recovery',revision:3,recovery_reason:'camera',paused:true,camera_ready:false});
 check(shown('recovery-panel')&&!shown('recovery-action'),'camera recovery has no retry or resume action');
 check(document.getElementById('recovery-detail').textContent.includes('automatically'),'camera recovery explains automatic continuation');
 emit({phase:'recovery',revision:3,recovery_reason:'recognition',paused:true});
 check(!shown('recovery-action')&&commands.length===beforeRecovery,'automatic recovery needs no browser command');
 check(!document.getElementById('game').classList.contains('camera-unavailable'),'recognition recovery keeps a healthy camera visible');
 emit({phase:'preparing',revision:4,paused:false,recovery_reason:''});
 check(musicSources.at(-1)?.stopped,'starting a hunt stops menu music');
 const camera=document.getElementById('camera-feed');
 check(shown('preparing-panel'),'preparing panel visible');
 check(camera.getAttribute('src')==='/camera','camera stream source');
 emit({phase:'countdown',revision:5,countdown_remaining:3});
 check(snd('countdown-tick')===1,'countdown transition ticks once');
 emit({phase:'countdown',revision:6,countdown_remaining:3});
 check(snd('countdown-tick')===1,'repeated countdown state does not tick');
 emit({phase:'countdown',revision:6,countdown_remaining:2});
 emit({phase:'countdown',revision:6,countdown_remaining:2.1});
 emit({phase:'countdown',revision:6,countdown_remaining:2});
 check(snd('countdown-tick')===2,'countdown clock corrections do not replay ticks');
 emit({phase:'round',revision:7});
 check(snd('hunt-start')===1,'hunt start once');
 emit({phase:'round',revision:8,round_remaining:59.5});
 check(camera===document.getElementById('camera-feed'),'camera survives phase and HUD update');
 check(snd('hunt-start')===1,'HUD updates do not replay start');
 emit({phase:'round',revision:9,clue:'<img src=x onerror="window.injected=true"> & a clue'});
 check(!document.getElementById('clue').querySelector('img')&&!window.injected,'clue is text, never HTML');
 check(document.getElementById('clue').textContent.includes('<img'),'clue literal text preserved');
 const beforeEscape=commands.length;
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 check(commands.length===beforeEscape,'escape cannot leave an active match');
 const beforePause=commands.length;
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'p',bubbles:true}));
 check(commands.length===beforePause,'keyboard cannot pause');
 document.getElementById('pause-control').click();
 check(commands.at(-1).type==='pause_game','pause button still works');
 emit({phase:'recovery',paused:true,recovery_reason:'paused',revision:10});
 check(document.getElementById('recovery-title').textContent==='Hunt paused','pause explanation');
 check(document.getElementById('recovery-action').dataset.command==='resume_game','pause resume action');
 check(shown('recovery-action')&&document.getElementById('recovery-action').textContent==='Play','deliberate pause shows Play');
 const beforeMenu=commands.length;
 document.querySelector('#recovery-panel [data-command="back_to_menu"]').click();
 check(commands.length===beforeMenu+1&&commands.at(-1).type==='back_to_menu','Menu returns directly without confirmation');
 emit({phase:'countdown',paused:false,revision:11});
 emit({phase:'round',revision:12,round_remaining:7});
 check(!document.getElementById('hurry')&&document.getElementById('time-value').textContent==='00:07','timer has no hurry label');
 check(document.querySelectorAll('.score-capsule>span').length===0,'score boxes show numbers without team labels');
 const warningTicks=snd('countdown-tick');
 emit({phase:'round',revision:12,round_remaining:5});
 check(document.getElementById('time-value').classList.contains('timer-urgent')&&snd('countdown-tick')===warningTicks+1,'five seconds starts red timer flashing and one tick');
 emit({phase:'round',revision:12,round_remaining:5});
 emit({phase:'round',revision:12,round_remaining:5.1});
 emit({phase:'round',revision:12,round_remaining:5});
 check(snd('countdown-tick')===warningTicks+1,'timer corrections do not repeat warning ticks');
 for(const remaining of [4,3,2,1,0]) emit({phase:'round',revision:12,round_remaining:remaining});
 check(snd('countdown-tick')===warningTicks+5&&!document.getElementById('time-value').classList.contains('timer-urgent'),'final five seconds tick once each and stop at zero');
 const result={round_number:1,target:'Cup <svg onload=x>',winner:'red',points:1000};
 emit({phase:'round_result',revision:13,result,scores:{red:1000,blue:0},recaps:[result]});
 const resultCues=snd('object-found');
 emit({phase:'recovery',revision:13.1,paused:true,recovery_reason:'paused'});
 emit({phase:'round_result',revision:13.2,paused:false,recovery_reason:''});
 check(snd('object-found')===resultCues,'resuming the same result does not replay its sound');
 emit({phase:'recovery',revision:13.3,paused:true,recovery_reason:'paused',result:null});
 disconnect();reconnect();emit({phase:'recovery',revision:13.4,paused:true,result:null});
 emit({phase:'round_result',revision:13.5,paused:false,recovery_reason:'',result});
 check(snd('object-found')===resultCues,'reconnecting during a paused result does not replay its sound');
 check(snd('object-found')===1,'award cue');
 check(!document.getElementById('result-title').querySelector('svg'),'result target safe');
 emit({phase:'round_result',revision:14});
 check(snd('object-found')===1,'duplicate award update does not play');
 disconnect();
 check(shown('connection-panel'),'disconnect message');
 check(document.getElementById('game').classList.contains('camera-unavailable'),'disconnect hides stale camera');
 reconnect();emit({phase:'round_result',revision:14});
 check(snd('object-found')===1,'reconnect does not replay award');
 check(camera===document.getElementById('camera-feed'),'reconnect preserves camera node');
 emit({phase:'round',revision:15});
 emit({phase:'round_result',revision:16,current_round:10,result:{round_number:10,target:'Apple',winner:null,points:0}});
 check(snd('time-up')===1,'timeout cue');
 check(document.getElementById('result-next').textContent==='Final scores coming up…','last result does not promise round eleven');
 const rounds=Array.from({length:10},(_,i)=>({round_number:i+1,target:i===9?'<script>bad</script>':'Object '+(i+1),winner:i===8?null:i%2?'blue':'red',points:i===8?0:1000}));
 emit({phase:'game_over',revision:17,recaps:rounds,scores:{red:4000,blue:5000}});
 check(snd('match-complete')===1,'match complete cue');
 check(document.querySelectorAll('#recap tbody tr').length===10,'all ten recap rows');
 check(document.querySelectorAll('#recap table').length===2,'recap two five-row tables');
 check(document.querySelectorAll('#recap script').length===0,'recap target safe');
 check(document.querySelector('#recap .timeout').textContent==='Time up','timeout readable');
 check(document.getElementById('final-title').textContent==='Blue wins!','blue winner');
 if (window.visualBrowser) {
 await document.fonts.ready;
 check(getComputedStyle(document.body).cursor==='none'&&getComputedStyle(document.querySelector('button')).cursor==='none','cursor hidden over game and controls');
 check(document.fonts.check('italic 32px "Changa One"'),'Changa One loaded');
 check(document.fonts.check('600 32px "Work Sans"'),'Work Sans loaded');
 check([...document.querySelectorAll('#recap tbody tr')].every(row=>{const r=row.getBoundingClientRect();return r.height>0&&r.top>=0&&r.bottom<=innerHeight;}),'all recap rows visible in browser viewport');
 check(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
 }
 emit({phase:'game_over',revision:18,scores:{red:5000,blue:5000}});
 check(document.getElementById('final-title').textContent==="It's a draw!",'draw title');
 emit({phase:'countdown',revision:19});emit({phase:'round',revision:20});
 emit({phase:'round',revision:21,round_remaining:40,clue:base.clue});
 const b=camera.getBoundingClientRect();
 if (window.visualBrowser) check(Math.abs(b.height-Math.min(innerHeight,innerWidth*3/4))<1&&Math.abs(b.width-Math.min(innerWidth,innerHeight*4/3))<1&&Math.abs(b.left-(innerWidth-b.width)/2)<1,'centred 12:9 camera container geometry');
 camera.dispatchEvent(new Event('error'));
 await new Promise(resolve=>setTimeout(resolve,150));
 check(camera===document.getElementById('camera-feed')&&camera.getAttribute('src').startsWith('/camera?retry='),'failed camera request retries on the same node');
 const src=camera.getAttribute('src');
 window.testTime=6000;
 await new Promise(resolve=>setTimeout(resolve,150));
 check(shown('connection-panel'),'stale heartbeat shows connection loss');
 check(camera===document.getElementById('camera-feed')&&camera.getAttribute('src')===src,'stale heartbeat does not replace/restart camera');
 emit({...base,revision:22});await settle();
 check(musicSources.length===2&&musicSources.at(-1).started,'menu music resumes automatically after reconnection');
 document.dispatchEvent(new KeyboardEvent('keydown',{key:'m',bubbles:true}));await settle();
 check(musicSources.length===2&&!musicSources.at(-1).stopped,'keyboard cannot change menu audio');
 disconnect();check(musicSources.at(-1)?.stopped,'connection loss stops music');
 reconnect();emit({...base,revision:23});await settle();
 check(musicSources.length===3&&musicLoads===1,'reconnect resumes one cached menu loop');
 emit({phase:'round_result',revision:24,result:{winner:'red',target:'cup',points:1000}});
 document.querySelector('#recovery-panel [data-command="back_to_menu"]').click();
 const pausesBeforeReset=pausedSounds.length;
 emit({...base,phase:'loading',revision:25});
 check(shown('welcome')&&!shown('menu')&&!shown('game'),'game reset shows the existing loading screen');
 check(pausedSounds.length===pausesBeforeReset+6,'game reset stops any previous match sound effects');
 check(camera===document.getElementById('camera-feed')&&camera.getAttribute('src')===src,'game reset preserves the existing camera stream');
 emit({...base,revision:26});await settle();
 check(shown('menu')&&musicSources.length===4&&musicLoads===1,'reset returns to menu and reuses cached music');
 const report=document.createElement('pre');report.id='test-result';report.textContent=JSON.stringify({count,failures,viewport:[innerWidth,innerHeight]});document.body.append(report);
})().catch(error=>{const report=document.createElement('pre');report.id='test-result';report.textContent=JSON.stringify({failures:[String(error.stack)]});document.body.append(report);});
`;
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ttf':'font/ttf','.wav':'audio/wav'};
const server=http.createServer((req,res)=>{
  if(req.url.startsWith('/camera')){res.setHeader('Content-Type','image/gif');res.end(Buffer.from('R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=','base64'));return;}
  if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end(fs.readFileSync(path.join(root,'index.html'),'utf8').replace('<script src="libs/socket.io.min.js"></script>','<script>window.visualBrowser=true;'+setup+'</script>').replace('<script src="libs/arduino.js"></script>','').replace('</body>','<script>'+checks.replaceAll('</script', '<\\/script')+'</script></body>'));return;}
  const file=path.join(root,decodeURIComponent(req.url.split('?')[0]));
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);
});
server.listen(0,'127.0.0.1',()=>{
  const userDir=fs.mkdtempSync(path.join(os.tmpdir(),'shq-frontend-chrome-'));
  const child=spawn(chrome,['--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--no-proxy-server',`--user-data-dir=${userDir}`,'--window-size=1920,1080','--hide-scrollbars','--dump-dom','--virtual-time-budget=3000',`http://127.0.0.1:${server.address().port}/`]);
  let output='';let errors='';
  child.stdout.on('data',data=>output+=data);child.stderr.on('data',data=>errors+=data);
  const timeout=setTimeout(()=>child.kill(),30000);
  child.on('error',error=>{console.error(error);server.close();process.exitCode=1;});
  child.on('close',()=>{
    clearTimeout(timeout);server.close();
    try{
      const match=output.match(/<pre id="test-result">(.*?)<\/pre>/s);
      assert.ok(match,'browser did not finish checks: '+errors.slice(-1500));
      const result=JSON.parse(match[1].replaceAll('&gt;','>').replaceAll('&lt;','<').replaceAll('&amp;','&'));
      assert.deepEqual(result.failures,[]);
      console.log(`PASS: ${result.count} frontend behavioural checks in Chrome at ${result.viewport.join('x')}.`);
    }catch(error){console.error(error);process.exitCode=1;fs.writeFileSync(path.join(os.tmpdir(),'shq-frontend-test-dom.html'),output);}
    fs.rmSync(userDir,{recursive:true,force:true});
  });
});
