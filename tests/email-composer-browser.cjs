const {chromium}=require('playwright');
const fs=require('fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 const page=await browser.newPage({viewport:{width:960,height:900},colorScheme:'dark'});
 page.setDefaultTimeout(5000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const initial={card:'card1',revision:0,status:'local',fields:{to:'alex@example.com',cc:'',bcc:'',subject:'Meeting next week',priority:'normal',html:'<p>Hi Alex,</p><p>Could we move our meeting to next week?</p><p>A few options:</p><ul><li>Tuesday morning</li><li>Thursday afternoon</li></ul><p>Thanks,<br>Jamie</p>'}};
 const html=fs.readFileSync('email-composer/card.html','utf8').replace('/*INITIAL*/',JSON.stringify(initial));
 await page.setContent('<html class="light"><style>body{margin:0}iframe{display:block;width:100%;border:0}</style><div id="m-embeds-container"><iframe sandbox="allow-scripts"></iframe></div></html>');
 // Run the actual fixed parent bridge, not a test-only approximation. OS is dark, app starts light.
 const bridge=fs.readFileSync('email-composer/action.py','utf8').split('async def theme_bridge')[1].match(/r"""([\s\S]*?)"""/)[1];
 await page.evaluate('(async()=>{const MESSAGE="m",CARD="card1";'+bridge+'})()');
 await page.evaluate(()=>addEventListener('message',e=>{if(e.source===document.querySelector('iframe').contentWindow&&e.data?.type==='iframe:height')document.querySelector('iframe').style.height=e.data.height+'px'}));
 await page.locator('iframe').evaluate((el,html)=>el.srcdoc=html,html);
 const frame=page.frameLocator('iframe');
 await frame.locator('html[data-theme=light]').waitFor();
 assert.equal(await frame.locator('.composer').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(255, 255, 255)');
 assert.equal(await frame.locator('#cc-row').isVisible(),false);
 assert.equal(await frame.locator('#bcc-row').isVisible(),false);
 assert.equal(await frame.getByRole('button',{name:'Bulleted list',exact:true}).count(),1);
 assert.equal(await frame.getByRole('button',{name:'Numbered list',exact:true}).count(),1);
 if(process.env.SCREENSHOT_DIR){fs.mkdirSync(process.env.SCREENSHOT_DIR,{recursive:true});await frame.locator('.composer').screenshot({path:process.env.SCREENSHOT_DIR+'/minimal-light.png'});}
 await page.evaluate(()=>document.documentElement.className='dark');
 await frame.locator('html[data-theme=dark]').waitFor();
 assert.equal(await frame.locator('.composer').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(32, 32, 32)');
 if(process.env.SCREENSHOT_DIR)await frame.locator('.composer').screenshot({path:process.env.SCREENSHOT_DIR+'/minimal-dark.png'});
 const snapshot=()=>page.evaluate(()=>new Promise(resolve=>{const w=document.querySelector('iframe').contentWindow;const nonce=String(Math.random());setTimeout(()=>resolve({error:'snapshot timeout'}),3000);const fn=e=>{if(e.source===w&&e.data?.nonce===nonce){removeEventListener('message',fn);resolve(e.data.payload)}};addEventListener('message',fn);w.postMessage({type:'email-composer:request',nonce,operation:'snapshot'},'*')}));
 await frame.getByLabel('Subject',{exact:true}).fill('Edited manually');
 await frame.getByRole('button',{name:'Cc',exact:true}).click();
 await frame.getByLabel('CC',{exact:true}).fill('mike@example.com; taylor@example.com');
 // Snapshot includes pending recipients, even before blur/Enter commits their chips.
 assert.equal((await snapshot()).fields.cc,'mike@example.com; taylor@example.com');
 await frame.getByLabel('CC',{exact:true}).press('Enter');
 await frame.getByRole('button',{name:'Remove mike@example.com from CC',exact:true}).click();
 assert.equal((await snapshot()).fields.cc,'taylor@example.com');
 await frame.getByRole('button',{name:'Remove taylor@example.com from CC',exact:true}).click();
 assert.equal((await snapshot()).fields.cc,'');
 await frame.getByRole('button',{name:'Bcc',exact:true}).click();
 await frame.getByLabel('BCC',{exact:true}).fill('private@example.com');
 await frame.locator('summary').click();
 await frame.getByLabel('Priority',{exact:true}).selectOption('high');
 assert.equal(await frame.locator('#priority-badge').innerText(),'High priority');
 await frame.locator('summary').click();
 await frame.getByRole('textbox',{name:'Message body'}).fill('A list item');
 await frame.getByRole('button',{name:'Bulleted list',exact:true}).click();
 assert.match((await snapshot()).fields.html,/<ul><li>/);
 await frame.getByRole('button',{name:'Numbered list',exact:true}).click();
 assert.match((await snapshot()).fields.html,/<ol><li>/);
 // The inline link form works without weakening the iframe sandbox to allow native modals.
 await frame.locator('#editor').evaluate(el=>{const range=document.createRange();range.selectNodeContents(el.querySelector('li'));const selection=getSelection();selection.removeAllRanges();selection.addRange(range)});
 await frame.getByRole('button',{name:'Insert link',exact:true}).click();
 await frame.getByLabel('Link URL').fill('https://example.com');
 await frame.getByRole('button',{name:'Insert',exact:true}).click();
 assert.match((await snapshot()).fields.html,/<a href="https:\/\/example.com">A list item<\/a>/);
 await page.evaluate(()=>document.documentElement.className='light');
 await frame.locator('html[data-theme=light]').waitFor();
 const edited=await snapshot();assert.equal(edited.fields.subject,'Edited manually');assert.equal(edited.fields.bcc,'private@example.com');assert.equal(edited.fields.priority,'high');
 // A theme message from another frame/window must not override the parent's selection.
 await frame.locator('#editor').evaluate(()=>dispatchEvent(new MessageEvent('message',{data:{type:'email-composer:theme',theme:'dark'}})));
 assert.equal(await frame.locator('html').getAttribute('data-theme'),'light');
 for(const width of [360,320]){await page.setViewportSize({width,height:900});assert.equal(await frame.locator('body').evaluate(el=>el.scrollWidth>innerWidth),false,'horizontal overflow at '+width);}
 assert.deepEqual(errors,[]);
 console.log('Browser passed: app light/dark against dark OS, live switching without lost edits, recipient chips and clearing, CC/BCC reveal, priority, real list/link editing, snapshot, 320/360px layout; no runtime errors.');
 await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
