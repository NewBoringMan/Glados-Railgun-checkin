// Bounded acceptance through the existing production DCF broker only.
// No direct provider calls, policy overrides, foreground permission, or clipboard.
// Output is restricted to evidence about this app, never raw account/email text.
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const broker='/Users/Shared/MacControl/LocalAnt/shared-tools/desktop-control-broker';
const require=createRequire(broker+'/package.json');
const {Client}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/index.js')));
const {StdioClientTransport}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/sdk/client/stdio.js')));
const mode=process.argv[2]??'status';
if(!['status','health','capture','maintenance','quit'].includes(mode))throw Error('unsupported_acceptance_operation');
const transport=new StdioClientTransport({command:broker+'/bin/dcf-broker',args:[],cwd:broker,
  env:{...process.env,DCF_OWNER:'DevSpace'},stderr:'pipe'});
const client=new Client({name:'glados-dcf-acceptance',version:'1.1'});
const sessions=new Set();
let finished=false;
const timer=setTimeout(()=>{client.close().catch(()=>{});},170000);
function emit(value){console.log(JSON.stringify(value));}
function textParts(result){return (result.content??[]).filter(x=>x.type==='text').map(x=>x.text);}
function metadata(result){
  const found=[];
  for(const text of textParts(result)){
    try{
      const data=JSON.parse(text);found.push(data);
      if(typeof data.dcf_session_id==='string')sessions.add(data.dcf_session_id);
    }catch{}
  }
  return found;
}
async function call(name,args={},timeout=80000){
  const r=await client.callTool({name,arguments:args},undefined,{timeout});
  metadata(r);
  if(r.isError){
    const text=textParts(r).join(' ');
    let reason='dcf_action_rejected';
    if(/foreground focus|user.activity|user.changed/i.test(text))reason='user_focus_changed_stopped';
    else if(/locked/i.test(text))reason='screen_locked_stopped';
    throw Error(reason);
  }
  return r;
}
async function capture(window){
  const r=await call('desktop_capture_context',{app:'GLaDOS Account Center',
    ...(window?{window}:{}),include_screenshot:false,force_refresh:true});
  const text=textParts(r).filter(s=>!s.trimStart().startsWith('{')).join('\n');
  const meta=metadata(r).find(x=>x.dcf_session_id);
  if(!meta)throw Error('missing_dcf_session');
  return {r,text,session:meta.dcf_session_id,meta};
}
function inspect(c){
  const getButton=(title)=>{
    const quoted=JSON.stringify(title);
    const lines=c.text.split('\n').filter(line=>line.includes(' - '+quoted+' - ')&&!line.includes('[not actionable]'));
    return lines.length===1 ? lines[0].match(/\b(elem_\d+)\b/)?.[1] : undefined;
  };
  return {session:c.session,hasMaintenance:!!getButton('登录维护'),hasPolicy:!!getButton('兑换方案'),
    hasAccountEmailTab:c.text.includes('账号邮箱'),hasSetupTab:c.text.includes('收码设置'),
    hasStatusTab:c.text.includes('运行状态'),hasAccountCount26:/26 个账号|账号总数[\s\S]*?"26"/.test(c.text),
    savedEmailTextObserved:/已确认邮箱\s*5|邮箱已确认/.test(c.text),
    maintenanceButton:getButton('登录维护'),policyButton:getButton('兑换方案'),
    textPresent:!!c.text};
}
try{
  await client.connect(transport);
  const status=await call('control_status',{},18000);
  const state=metadata(status)[0];
  if(!state||state.backgroundOnly!==true||state.foregroundAllowedUntil)throw Error('background_policy_required');
  emit({event:'policy',backgroundOnly:true,screenUnlocked:state.screenLockState==='unlocked'});
  if(mode==='status')finished=true;
  else if(mode==='health'){
    const health=await call('provider_health',{},35000);
    const d=metadata(health)[0];
    emit({event:'health',desktop:d?.desktop?.providers});finished=true;
  }else{
    const c=await capture();const summary=inspect(c);emit({event:'capture',...summary});
    if(mode==='maintenance'){
      if(summary.hasAccountEmailTab&&summary.hasSetupTab){emit({event:'maintenance_already_open'});}
      else{
        if(!summary.maintenanceButton)throw Error('maintenance_button_not_found');
        await call('desktop_click',{session_id:c.session,element_index:summary.maintenanceButton},35000);
      }
      const second=await capture('GLaDOS 登录维护');
      const observed=inspect(second);emit({event:'maintenance_window',...observed});
      if(!observed.hasAccountEmailTab||!observed.hasSetupTab||!observed.hasStatusTab)throw Error('maintenance_ui_incomplete');
    } else if(mode==='quit'){
      // A close/replacement step explicitly authorized by the user; don't quit an
      // unknown modal, active consent or in-progress credential maintenance.
      if(!/^Window: 概览$/m.test(c.text)||!summary.maintenanceButton||/验证码已请求|正在等待本次|取消等待/.test(c.text))
        throw Error('safe_overview_required_before_quit');
      await call('desktop_press_key',{session_id:c.session,key:'super+q'},35000);
      emit({event:'quit_sent_through_dcf'});
    }
    finished=true;
  }
}catch(e){emit({event:'failed',reason:e?.message??'dcf_unavailable'});process.exitCode=1;}
finally{
  for(const session of sessions)await client.callTool({name:'desktop_session_close',arguments:{session_id:session}},undefined,{timeout:4000}).catch(()=>{});
  await client.close().catch(()=>{});clearTimeout(timer);
  emit({event:'closed',passed:finished,sessionsClosed:sessions.size});
}
