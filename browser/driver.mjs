import {pathToFileURL} from 'node:url';
import {readFile,writeFile,lstat} from 'node:fs/promises';
import {resolve} from 'node:path';
export function checkedEndpoint(value){
 const u=new URL(value);
 if(u.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(u.hostname)||u.username||u.password||u.search||u.hash||u.pathname!=='/')throw Error('CDP must be an explicitly authorized loopback HTTP endpoint');
 return u.origin;
}
export function classify({challenge,login,composer}){
 if(challenge)return 'challenge_required';
 if(login||!composer)return 'signin_required';
 return 'ready';
}
async function readiness(page){
 const challenge=await page.getByText(/verify you are human|checking your browser|just a moment/i).count();
 const login=await page.getByRole('button',{name:/^(log in|sign in|войти)$/i}).count();
 const composer=await page.locator('#prompt-textarea[contenteditable="true"]').count();
 return classify({challenge:challenge>0,login:login>0,composer:composer>0});
}
export async function run(input){
 const endpoint=checkedEndpoint(input.endpoint);
 const {chromium}=await import('playwright');
 const browser=await chromium.connectOverCDP(endpoint,{timeout:15000});
 const context=browser.contexts()[0];
 if(!context){await browser.close();throw Error('No authorized browser context');}
 const page=await context.newPage();
 let submitted=false;
 try{
  await page.goto('https://chatgpt.com/',{waitUntil:'domcontentloaded',timeout:45000});
  await page.waitForTimeout(2000);
  const status=await readiness(page);
  if(status!=='ready')return {ok:false,status,message:'Sign in manually in the authorized Chrome profile; no credentials or cookies are copied.'};
  if(input.check)return {ok:true,status:'ready',backend:'chatgpt-web',imageModel:'service managed; not pinned',generated:false};
  if(typeof input.prompt!=='string'||input.prompt.trim().length<10||Buffer.byteLength(input.prompt)>1048576)throw Error('Invalid prompt length');
  const output=resolve(input.output);try{await lstat(output);throw Error('Output already exists');}catch(e){if(e.code!=='ENOENT')throw e;}
  const composer=page.locator('#prompt-textarea');
  await composer.fill(input.prompt);
  const send=page.locator('[data-testid="send-button"]');
  if(await send.count()!==1)throw Error('ChatGPT send control changed; refusing blind input');
  await send.click();submitted=true;
  const deadline=Date.now()+input.timeout*1000;let image;
  while(Date.now()<deadline){
   const last=page.locator('[data-message-author-role="assistant"]').last();
   if(await last.count()){
    const words=(await last.innerText()).slice(-3000);
    if(/you.ve reached.*limit|try again (later|after)|limit resets|достигнут.*лимит/i.test(words))throw Error('ChatGPT image quota reached; no automatic retry');
    const generated=last.locator('img[alt^="Generated image"],img[alt^="Сгенерированное изображение"]');
    if(await generated.count()===1&&await page.locator('[data-testid="stop-button"]').count()===0){image=generated;break;}
   }
   await page.waitForTimeout(2000);
  }
  if(!image)throw Error('Generation did not yield one unambiguous final image before timeout');
  await image.waitFor({state:'visible'});await image.hover();
  const response=page.locator('[data-message-author-role="assistant"]').last();
  let download=response.getByRole('button',{name:/^(download|save|скачать|сохранить)( image| изображение)?$/i});
  if(await download.count()!==1){await image.click();download=page.getByRole('dialog').getByRole('button',{name:/^(download|save|скачать|сохранить)( image| изображение)?$/i});}
  if(await download.count()!==1)throw Error('Original-image download control unavailable; no network/cookie fallback');
  const event=page.waitForEvent('download',{timeout:30000});await download.click();
  const file=await event;if(await file.failure())throw Error('Original-image download failed');
  await file.saveAs(output);
  return {ok:true,status:'generated',backend:'chatgpt-web',imageModel:'ChatGPT Images; version not exposed by UI',generated:true,conversationUrl:page.url(),output};
 }finally{
  if(!submitted)await page.close();
  // Disconnect from the user's running browser. Never kill/relaunch it or export storage.
  await browser.close();
 }
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 try{
  const chunks=[];for await(const chunk of process.stdin)chunks.push(chunk);
  const result=await run(JSON.parse(Buffer.concat(chunks).toString()));
  process.stdout.write(JSON.stringify(result)+'\n');process.exitCode=result.ok?0:3;
 }catch(e){process.stderr.write('draw-browser: '+e.message+'\n');process.exitCode=2;}
}
